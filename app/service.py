"""Answer flow: retrieve, ask the model if there is one, check, fall back."""

from typing import Protocol

from pydantic import BaseModel

from app.guard import FALLBACK, is_grounded
from app.retrieval import Retriever
from app.store import Fact


class Model(Protocol):
    def complete(self, question: str, facts: list[Fact]) -> str | None: ...


class Answer(BaseModel):
    answer: str
    grounded: bool
    sources: list[str]


def answer_question(question: str, retriever: Retriever, model: Model | None) -> Answer:
    hits = retriever.search(question)
    if not hits:
        return Answer(answer=FALLBACK, grounded=False, sources=[])

    facts = [fact for fact, _ in hits]
    text = model.complete(question, facts) if model else None

    if text is None:
        # extractive mode: no model, or the call failed
        best = facts[0]
        return Answer(answer=best.text, grounded=True, sources=[best.id])

    if text == FALLBACK or not is_grounded(text, [f.text for f in facts]):
        return Answer(answer=FALLBACK, grounded=False, sources=[])

    return Answer(answer=text, grounded=True, sources=[f.id for f in facts])
