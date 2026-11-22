"""Chat with the agent in the terminal, as if you were the customer on WhatsApp.

python -m app.cli                  # throwaway database in memory
python -m app.cli --db agent.db    # same file the API uses
"""

import argparse
import sys
from pathlib import Path

from app.agenda import load_agenda
from app.agent import Agent
from app.chat_client import ChatClient
from app.db import AgentDB
from app.retrieval import Retriever
from app.store import load_facts

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default=str(ROOT / "data" / "barberia"))
    parser.add_argument("--db", default=":memory:")
    parser.add_argument("--phone", default="56900000000", help="fake customer number")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data = Path(args.data)
    agent = Agent(
        agenda=load_agenda(data / "agenda.json"),
        db=AgentDB(args.db),
        retriever=Retriever(load_facts(data / "faq.json")),
        model=ChatClient.from_env(),
    )
    print("Escribe como cliente. Ctrl+C para salir.\n")
    try:
        while True:
            text = input("tú > ").strip()
            if not text:
                continue
            reply = agent.handle(args.phone, text)
            if reply.text:
                print("bot > " + reply.text.replace("\n", "\n      ") + "\n")
            else:
                print("bot > (callado: la conversación la lleva una persona; MENU para volver)\n")
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    main()
