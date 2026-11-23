"""Answer flow: retrieve, ask the model if there is one, check, fall back."""

import re
from typing import Protocol

from pydantic import BaseModel

from app.guard import FALLBACK, is_grounded
from app.retrieval import Retriever
from app.store import Fact

CITATION = re.compile(r"\s*\[(\d+)\]")


class Model(Protocol):
    def complete(self, question: str, facts: list[Fact]) -> str | None: ...


class Answer(BaseModel):
    answer: str
    grounded: bool
    sources: list[str]


def answer_question(question: str, retriever: Retriever, model: Model | None) -> Answer:
    return trace_answer(question, retriever, model)[0]


def trace_answer(question: str, retriever: Retriever, model: Model | None) -> tuple[Answer, str]:
    """The answer and the step that decided it.

    Steps: no_match, extractive, model, refused, blocked.
    """
    hits = retriever.search(question)
    if not hits:
        return _no_answer(), "no_match"

    facts = [fact for fact, _ in hits]
    text = model.complete(question, facts) if model else None
    checked = _check(text, facts) if text is not None else None

    if checked is None or checked[1] == "uncited":
        # extractive mode: no model, or the call failed, or it cited no fact
        best = facts[0]
        return Answer(answer=best.text, grounded=True, sources=[best.id]), "extractive"

    return checked


def _no_answer() -> Answer:
    return Answer(answer=FALLBACK, grounded=False, sources=[])


def _check(text: str, facts: list[Fact]) -> tuple[Answer, str]:
    """Keep a model answer only if it cites facts and its data is in them."""
    numbers = {int(n) for n in CITATION.findall(text)}
    cited = [f for n, f in enumerate(facts, 1) if n in numbers]
    text = CITATION.sub("", text).strip()
    if text.lower().startswith(FALLBACK.rstrip(".").lower()):
        return _no_answer(), "refused"
    if not is_grounded(text, [f.text for f in cited or facts]):
        return _no_answer(), "blocked"
    if not cited:
        return _no_answer(), "uncited"
    return Answer(answer=text, grounded=True, sources=[f.id for f in cited]), "model"
