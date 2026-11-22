from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.agenda import load_agenda
from app.agent import Agent
from app.db import AgentDB
from app.retrieval import Retriever
from app.store import load_facts

DATA = Path(__file__).resolve().parent.parent / "data" / "barberia"
TZ = ZoneInfo("America/Santiago")


class Clock:
    """Fixed, movable time for the agent."""

    def __init__(self, *args):
        self.at = datetime(*args, tzinfo=TZ)

    def __call__(self):
        return self.at

    def set(self, *args):
        self.at = datetime(*args, tzinfo=TZ)


@pytest.fixture
def clock():
    # Monday 23-11-2026 09:00 in Santiago; the shop is closed on Mondays
    return Clock(2026, 11, 23, 9, 0)


@pytest.fixture
def agent(clock):
    return Agent(
        agenda=load_agenda(DATA / "agenda.json"),
        db=AgentDB(),
        retriever=Retriever(load_facts(DATA / "faq.json")),
        clock=clock,
    )


def say(agent, phone, *messages):
    """Send messages in order and return the last reply text."""
    reply = None
    for text in messages:
        reply = agent.handle(phone, text)
    return reply.text


def book(agent, phone, service="1", day="1", slot="1", name="Ana Pérez"):
    """Run the whole booking flow for a new customer and return the final reply."""
    texts = ["reservar"]
    if agent.db.customer_name(phone) is None:
        texts += ["sí", name]
    texts += [service, day, slot, "si"]
    return say(agent, phone, *texts)
