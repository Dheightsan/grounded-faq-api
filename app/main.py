import os
import re
import secrets
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.agenda import load_agenda
from app.agent import Agent, Reply
from app.chat_client import ChatClient
from app.db import AgentDB
from app.retrieval import Retriever
from app.service import Answer, Model, answer_question
from app.store import Fact, load_facts
from app.whatsapp import NOT_TEXT, WhatsAppSender, parse_incoming, verify_signature

ROOT = Path(__file__).resolve().parent.parent
PHONE = re.compile(r"\+?\d{8,15}")

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


# WhatsApp agent


@lru_cache
def get_agent() -> Agent:
    data_dir = Path(os.environ.get("AGENT_DATA_DIR") or ROOT / "data" / "barberia")
    return Agent(
        agenda=load_agenda(data_dir / "agenda.json"),
        db=AgentDB(os.environ.get("AGENT_DB_PATH") or "agent.db"),
        retriever=Retriever(load_facts(data_dir / "faq.json")),
        model=ChatClient.from_env(),
    )


def get_sender() -> WhatsAppSender | None:
    return WhatsAppSender.from_env()


def _same(a: str, b: str) -> bool:
    # compare_digest raises TypeError on non-ASCII str, so compare bytes
    return secrets.compare_digest(a.encode(), b.encode())


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("AGENT_API_KEY")
    if not expected:
        raise HTTPException(503, "AGENT_API_KEY is not set")
    if not x_api_key or not _same(x_api_key, expected):
        raise HTTPException(401, "bad API key")


class MessageIn(BaseModel):
    phone: str = Field(alias="from")
    text: str = Field(min_length=1, max_length=1000)
    id: str | None = Field(default=None, max_length=200)  # WhatsApp message id, for retries


@app.post("/agent/message", dependencies=[Depends(require_api_key)])
def agent_message(body: MessageIn, agent: Agent = Depends(get_agent)) -> Reply:
    """One customer message in, one reply out. Used by n8n or any other channel."""
    if not PHONE.fullmatch(body.phone):
        raise HTTPException(422, "phone must be 8 to 15 digits")
    if body.id and not agent.db.first_time(body.id, agent.now()):
        return Reply(text="")  # a retry of a message already answered
    try:
        return agent.handle(body.phone.lstrip("+"), body.text)
    except Exception:
        if body.id:
            agent.db.forget_message(body.id)
        raise


class BookingOut(BaseModel):
    id: int
    phone: str
    name: str | None
    service: str
    start: datetime
    end: datetime


@app.get("/agent/bookings", dependencies=[Depends(require_api_key)])
def agent_bookings(
    day: date = Query(alias="date"), agent: Agent = Depends(get_agent)
) -> list[BookingOut]:
    """Active bookings for one day, for reminders or the shop's own list."""
    start = datetime.combine(day, datetime.min.time())
    out = []
    for b, name in agent.db.on_day(start, start + timedelta(days=1)):
        service = agent.agenda.service(b.service_id)
        out.append(
            BookingOut(
                id=b.id,
                phone=b.phone,
                name=name,
                service=service.name if service else b.service_id,
                start=b.start,
                end=b.end,
            )
        )
    return out


@app.get("/whatsapp/webhook", response_class=PlainTextResponse)
def whatsapp_verify(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str = Query(default="", alias="hub.challenge"),
) -> str:
    """Meta calls this once when the webhook URL is registered."""
    expected = os.environ.get("WHATSAPP_VERIFY_TOKEN")
    if mode == "subscribe" and expected and token and _same(token, expected):
        return challenge
    raise HTTPException(403, "verification failed")


@app.post("/whatsapp/webhook")
async def whatsapp_receive(
    request: Request,
    agent: Agent = Depends(get_agent),
    sender: WhatsAppSender | None = Depends(get_sender),
) -> dict:
    secret = os.environ.get("WHATSAPP_APP_SECRET")
    if not secret:
        raise HTTPException(503, "WHATSAPP_APP_SECRET is not set")
    body = await request.body()
    if not verify_signature(secret, body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "bad signature")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "body is not JSON") from None

    replies = []
    for msg in parse_incoming(payload):
        if not agent.db.first_time(msg.message_id, agent.now()):
            continue  # Meta retried a delivery we already answered
        try:
            if msg.text is None:
                reply = Reply(text=NOT_TEXT)
            else:
                reply = await run_in_threadpool(agent.handle, msg.phone, msg.text)
            sent = False
            if reply.text and sender:
                sent = await run_in_threadpool(sender.send_text, msg.phone, reply.text)
        except Exception:
            # let Meta's retry go through instead of answering nothing
            agent.db.forget_message(msg.message_id)
            raise
        replies.append(
            {"to": msg.phone, "text": reply.text, "handoff": reply.handoff, "sent": sent}
        )
    # meta only needs a 200
    return {"replies": replies}
