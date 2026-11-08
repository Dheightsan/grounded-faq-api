from functools import lru_cache

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from app.chat_client import ChatClient
from app.retrieval import Retriever
from app.service import Answer, Model, answer_question
from app.store import Fact, load_facts

app = FastAPI(title="grounded-faq-api")


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)


@lru_cache
def get_retriever() -> Retriever:
    return Retriever(load_facts())


def get_model() -> Model | None:
    return ChatClient.from_env()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask")
def ask(
    body: AskIn,
    retriever: Retriever = Depends(get_retriever),
    model: Model | None = Depends(get_model),
) -> Answer:
    return answer_question(body.question, retriever, model)


@app.get("/facts")
def facts(topic: str | None = None, retriever: Retriever = Depends(get_retriever)) -> list[Fact]:
    return retriever.by_topic(topic)
