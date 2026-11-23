"""Run the eval set and print the numbers.

python -m app.evaluate             # plain text, no model
python -m app.evaluate --markdown  # table for the README
python -m app.evaluate --questions data/eval_heldout.json
python -m app.evaluate --with-model  # needs MODEL_API_KEY and MODEL_BASE_URL
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agenda import load_agenda
from app.agent import Agent
from app.chat_client import ChatClient
from app.db import AgentDB
from app.guard import FALLBACK
from app.retrieval import Retriever
from app.service import Model, trace_answer
from app.store import Fact, load_facts

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FACTS = {"barberia": DATA / "barberia" / "faq.json", "ferreteria": DATA / "store.json"}
TZ = ZoneInfo("America/Santiago")
START = datetime(2026, 11, 23, 9, 0, tzinfo=TZ)  # a Monday, shop closed

ANA = "56911111111"
LUIS = "56922222222"
BOOK = ["reservar", "si", "Ana Pérez", "1", "1", "1", "si"]  # service, day, slot, confirm


class CountedModel:
    """Counts the calls to a model and tries again when one fails."""

    def __init__(self, model: Model, name: str = "", retries: int = 3, wait: float = 10.0):
        self.model = model
        self.name = name
        self.retries = retries
        self.wait = wait
        self.calls = 0
        self.failed = 0

    def complete(self, question: str, facts: list[Fact]) -> str | None:
        self.calls += 1
        for attempt in range(self.retries + 1):
            text = self.model.complete(question, facts)
            if text is not None:
                return text
            if attempt < self.retries:
                time.sleep(self.wait)
        self.failed += 1
        return None


def run_questions(path: Path = DATA / "eval.json", model: Model | None = None) -> list[dict]:
    retrievers = {name: Retriever(load_facts(p)) for name, p in FACTS.items()}
    rows = []
    for item in json.loads(path.read_text(encoding="utf-8")):
        answer, step = trace_answer(item["q"], retrievers[item["set"]], model)
        got = ",".join(answer.sources) if answer.grounded else "fallback"
        if item["expect"] == "fallback":
            outcome = "rejected" if got == "fallback" else "wrong"
        elif item["expect"] in answer.sources:
            outcome = "correct"
        else:
            outcome = "missed" if got == "fallback" else "wrong"
        rows.append({**item, "got": got, "outcome": outcome, "step": step, "answer": answer.answer})
    return rows


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    for name in [*FACTS, None]:
        part = [r for r in rows if name is None or r["set"] == name]
        oos = [r for r in part if r["expect"] == "fallback"]
        out.append(
            {
                "set": name or "total",
                "answerable": len(part) - len(oos),
                "correct": sum(r["outcome"] == "correct" for r in part),
                "missed": sum(r["outcome"] == "missed" for r in part),
                "out_of_scope": len(oos),
                "rejected": sum(r["outcome"] == "rejected" for r in part),
                "wrong": sum(r["outcome"] == "wrong" for r in part),
            }
        )
    return out


class Chat:
    """One agent on a fresh in-memory database and a movable clock."""

    def __init__(self):
        self.now = START
        data = DATA / "barberia"
        self.agent = Agent(
            agenda=load_agenda(data / "agenda.json"),
            db=AgentDB(),
            retriever=Retriever(load_facts(data / "faq.json")),
            clock=lambda: self.now,
        )
        self.last = None

    def say(self, phone, *texts):
        for text in texts:
            self.last = self.agent.handle(phone, text)
        return self.last

    def wait(self, **delta):
        self.now += timedelta(**delta)


def script_book():
    chat = Chat()
    text = chat.say(ANA, *BOOK).text
    return "quedó reservada" in text and len(chat.agent.db.upcoming(ANA, chat.agent.now())) == 1


def script_same_slot():
    chat = Chat()
    chat.say(ANA, *BOOK)
    slots = chat.say(LUIS, "reservar", "si", "Luis", "1", "1").text
    if "10:00" in slots or "10:30" not in slots:
        return False
    # both reach the confirmation for 10:30, only the first one gets it
    chat.say(ANA, "reservar", "1", "1", "1")
    chat.say(LUIS, "1")
    first = chat.say(ANA, "si").text
    second = chat.say(LUIS, "si").text
    return "quedó reservada" in first and second.startswith("Esa hora se acaba de ocupar")


def script_cancel():
    chat = Chat()
    chat.say(ANA, *BOOK)
    text = chat.say(ANA, "cancelar", "1").text
    return "cancelé" in text and not chat.agent.db.upcoming(ANA, chat.agent.now())


def script_handoff():
    chat = Chat()
    first = chat.say(ANA, "quiero hablar con una persona")
    notice = chat.say(ANA, "hola buenas, necesito ayuda")
    quiet = chat.say(ANA, "alguien por ahi?")
    back = chat.say(ANA, "menu")
    return first.handoff and "Te atiende una persona" in notice.text and quiet.text == "" and (
        "Reservar hora" in back.text
    )


def script_erase():
    chat = Chat()
    chat.say(ANA, *BOOK)
    chat.say(ANA, "borrar mis datos")
    text = chat.say(ANA, "si").text
    gone = chat.agent.db.customer_name(ANA) is None and not chat.agent.db.upcoming(ANA, chat.now)
    return "borré" in text and gone


def script_stale_confirm():
    chat = Chat()
    chat.say(ANA, *BOOK[:-1])
    chat.wait(minutes=31)
    text = chat.say(ANA, "si").text
    return "ya venció" in text and not chat.agent.db.upcoming(ANA, chat.agent.now())


def script_two_active_max():
    chat = Chat()
    chat.say(ANA, *BOOK)
    chat.say(ANA, "reservar", "1", "1", "2", "si")
    text = chat.say(ANA, "reservar").text
    return "máximo" in text


def script_out_of_scope():
    chat = Chat()
    text = chat.say(ANA, "cuanto cuesta el tinte de pelo").text
    return text.startswith(FALLBACK) and not chat.agent.db.upcoming(ANA, chat.now)


SCRIPTS = {
    "book an appointment": script_book,
    "two customers, same slot": script_same_slot,
    "cancel a booking": script_cancel,
    "ask for a person": script_handoff,
    "erase my data": script_erase,
    "confirmation expires": script_stale_confirm,
    "third booking refused": script_two_active_max,
    "question outside the data": script_out_of_scope,
}


def run_scripts() -> dict[str, bool]:
    return {name: bool(fn()) for name, fn in SCRIPTS.items()}


def model_lines(rows: list[dict], model: CountedModel) -> list[str]:
    steps = [r["step"] for r in rows]
    return [
        "",
        f"model: {model.name}, {model.calls} calls, {model.failed} failed",
        f"  answers blocked by the guard: {steps.count('blocked')}",
        f"  answers with no fact cited: {steps.count('uncited')}",
        f"  refused by the model: {steps.count('refused')}",
    ]


def render(
    summary: list[dict],
    scripts: dict[str, bool],
    rows: list[dict],
    markdown: bool,
    model: CountedModel | None = None,
) -> str:
    head = ["set", "answerable", "correct", "missed", "out_of_scope", "rejected", "wrong"]
    lines = []
    if markdown:
        lines.append("| " + " | ".join(head) + " |")
        lines.append("|" + "---|" * len(head))
        lines += ["| " + " | ".join(str(s[h]) for h in head) + " |" for s in summary]
    else:
        lines.append("  ".join(f"{h:>12}" for h in head))
        lines += ["  ".join(f"{s[h]!s:>12}" for h in head) for s in summary]
    if model:
        lines += model_lines(rows, model)
    passed = sum(scripts.values())
    lines.append("")
    lines.append(f"conversation scripts: {passed}/{len(scripts)} pass")
    lines += [f"  {'pass' if ok else 'FAIL'}  {name}" for name, ok in scripts.items()]
    bad = [r for r in rows if r["outcome"] in ("missed", "wrong")]
    if bad:
        lines.append("")
        lines.append("failed questions:")
        for r in bad:
            miss = f"expected {r['expect']}, got {r['got']}"
            lines.append(f"  [{r['outcome']}] {r['set']}: {r['q']} ({miss}, {r['step']})")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--questions", type=Path, default=DATA / "eval.json")
    parser.add_argument("--with-model", action="store_true")
    parser.add_argument("--save", type=Path, help="write every question and answer as JSON")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    model = None
    if args.with_model:
        client = ChatClient.from_env()
        if client is None:
            sys.exit("--with-model needs MODEL_API_KEY and MODEL_BASE_URL")
        client.timeout = 60.0
        model = CountedModel(client, client.model)
    rows = run_questions(args.questions, model)
    if args.save:
        args.save.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(render(summarize(rows), run_scripts(), rows, args.markdown, model))


if __name__ == "__main__":
    main()
