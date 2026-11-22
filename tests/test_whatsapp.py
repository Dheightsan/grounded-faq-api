import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app, get_agent, get_sender
from app.whatsapp import NOT_TEXT, WhatsAppSender, parse_incoming, verify_signature

KEY = "test-key"
PHONE = "56911111111"


def payload(*messages):
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "123"},
            "messages": list(messages),
        }}]}],
    }  # fmt: skip


def text_msg(body, msg_id="wamid.1", phone=PHONE):
    return {"from": phone, "id": msg_id, "type": "text", "text": {"body": body}}


class FakeSender:
    def __init__(self):
        self.sent = []

    def send_text(self, to, body):
        self.sent.append((to, body))
        return True


@pytest.fixture
def sender():
    return FakeSender()


@pytest.fixture
def client(agent, sender, monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", KEY)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-me")
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    app.dependency_overrides[get_agent] = lambda: agent
    app.dependency_overrides[get_sender] = lambda: sender
    yield TestClient(app)
    app.dependency_overrides.clear()


# signature and parsing


def test_signature():
    body = b'{"a": 1}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, good)
    assert not verify_signature("secret", body + b" ", good)
    assert not verify_signature("secret", body, None)
    assert not verify_signature("secret", body, "md5=abc")


def test_parse_kinds():
    msgs = parse_incoming(payload(
        text_msg("hola"),
        {"from": PHONE, "id": "wamid.2", "type": "interactive",
         "interactive": {"type": "button_reply", "button_reply": {"id": "b1", "title": "Sí"}}},
        {"from": PHONE, "id": "wamid.3", "type": "image", "image": {"id": "x"}},
    ))  # fmt: skip
    assert [(m.message_id, m.text) for m in msgs] == [
        ("wamid.1", "hola"),
        ("wamid.2", "Sí"),
        ("wamid.3", None),
    ]


def test_parse_status_update_has_no_messages():
    status = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.1"}]}}]}]}
    assert parse_incoming(status) == []


# webhook


def test_verify_handshake(client):
    params = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"}
    r = client.get("/whatsapp/webhook", params=params)
    assert r.status_code == 200 and r.text == "42"
    params["hub.verify_token"] = "wrong"
    assert client.get("/whatsapp/webhook", params=params).status_code == 403


def test_webhook_replies_and_sends(client, sender):
    r = client.post("/whatsapp/webhook", json=payload(text_msg("hola")))
    assert r.status_code == 200
    reply = r.json()["replies"][0]
    assert reply["to"] == PHONE and reply["sent"] is True
    assert "1. Reservar hora" in reply["text"]
    assert sender.sent == [(PHONE, reply["text"])]


def test_webhook_ignores_retried_message(client, sender):
    client.post("/whatsapp/webhook", json=payload(text_msg("hola")))
    r = client.post("/whatsapp/webhook", json=payload(text_msg("hola")))
    assert r.json() == {"replies": []}
    assert len(sender.sent) == 1


def test_webhook_non_text(client):
    msg = {"from": PHONE, "id": "wamid.9", "type": "audio", "audio": {"id": "a"}}
    r = client.post("/whatsapp/webhook", json=payload(msg))
    assert r.json()["replies"][0]["text"] == NOT_TEXT


def test_webhook_checks_signature_when_secret_is_set(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
    body = json.dumps(payload(text_msg("hola"))).encode()
    bad_sig = {"x-hub-signature-256": "sha256=00"}
    bad = client.post("/whatsapp/webhook", content=body, headers=bad_sig)
    assert bad.status_code == 401
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    ok = client.post(
        "/whatsapp/webhook",
        content=body,
        headers={"x-hub-signature-256": sig, "content-type": "application/json"},
    )
    assert ok.status_code == 200


def test_webhook_rejects_non_json(client):
    r = client.post("/whatsapp/webhook", content=b"nope")
    assert r.status_code == 400


# API used by n8n


def test_agent_message_needs_key(client, monkeypatch):
    body = {"from": PHONE, "text": "hola"}
    assert client.post("/agent/message", json=body).status_code == 401
    wrong = client.post("/agent/message", json=body, headers={"X-API-Key": "nope"})
    assert wrong.status_code == 401
    monkeypatch.delenv("AGENT_API_KEY")
    assert client.post("/agent/message", json=body, headers={"X-API-Key": KEY}).status_code == 503


def test_agent_message_and_bookings(client):
    h = {"X-API-Key": KEY}
    for text in ["reservar", "si", "Ana Pérez", "1", "1", "1", "si"]:
        r = client.post("/agent/message", json={"from": "+" + PHONE, "text": text}, headers=h)
        assert r.status_code == 200
    assert "R-1" in r.json()["text"]
    day = client.get("/agent/bookings", params={"date": "2026-11-24"}, headers=h).json()
    assert day == [{
        "id": 1, "phone": PHONE, "name": "Ana Pérez", "service": "Corte de pelo",
        "start": "2026-11-24T10:00:00", "end": "2026-11-24T10:30:00",
    }]  # fmt: skip
    assert client.get("/agent/bookings", params={"date": "2026-11-25"}, headers=h).json() == []


def test_agent_message_rejects_bad_phone(client):
    body = {"from": "abc", "text": "hola"}
    r = client.post("/agent/message", json=body, headers={"X-API-Key": KEY})
    assert r.status_code == 422


# sending


def test_sender_posts_to_graph_api():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    s = WhatsAppSender("tok", "999", transport=httpx.MockTransport(handler))
    assert s.send_text(PHONE, "hola")
    assert seen["url"] == "https://graph.facebook.com/v23.0/999/messages"
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["text"] == {"body": "hola"} and seen["body"]["to"] == PHONE


def test_sender_failure_returns_false():
    s = WhatsAppSender("tok", "999", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert s.send_text(PHONE, "hola") is False
