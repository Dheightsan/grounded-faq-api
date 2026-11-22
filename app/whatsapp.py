"""WhatsApp Cloud API: webhook signature, message parsing and sending replies."""

import hashlib
import hmac
import logging
import os

import httpx
from pydantic import BaseModel

log = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com"
NOT_TEXT = "Por ahora solo leo mensajes de texto."


class Incoming(BaseModel):
    message_id: str
    phone: str
    text: str | None  # None for audio, images, stickers...


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Check the X-Hub-Signature-256 header Meta sends with each webhook call."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header.removeprefix("sha256=").encode())


def _items(value) -> list[dict]:
    """The dicts inside a JSON list; anything else is skipped."""
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def parse_incoming(payload) -> list[Incoming]:
    """Messages in a webhook payload. Status updates (sent, read) are ignored."""
    found = []
    for entry in _items(_dict(payload).get("entry")):
        for change in _items(entry.get("changes")):
            for msg in _items(_dict(change.get("value")).get("messages")):
                if not isinstance(msg.get("id"), str) or not isinstance(msg.get("from"), str):
                    continue
                if not msg["id"] or not msg["from"]:
                    continue
                kind = msg.get("type")
                text = None
                if kind == "text":
                    text = _dict(msg.get("text")).get("body")
                elif kind == "button":
                    text = _dict(msg.get("button")).get("text")
                elif kind == "interactive":
                    inter = _dict(msg.get("interactive"))
                    reply = _dict(inter.get("button_reply") or inter.get("list_reply"))
                    text = reply.get("title")
                if not isinstance(text, str):
                    text = None
                found.append(Incoming(message_id=msg["id"], phone=msg["from"], text=text))
    return found


class WhatsAppSender:
    def __init__(
        self,
        token: str,
        phone_number_id: str,
        api_version: str = "v23.0",
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.url = f"{GRAPH_URL}/{api_version}/{phone_number_id}/messages"
        self.token = token
        self.timeout = timeout
        self.transport = transport

    @classmethod
    def from_env(cls) -> "WhatsAppSender | None":
        token = os.environ.get("WHATSAPP_TOKEN")
        phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
        if not token or not phone_id:
            return None
        return cls(token, phone_id, os.environ.get("WHATSAPP_API_VERSION", "v23.0"))

    def send_text(self, to: str, body: str) -> bool:
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                headers = {"Authorization": f"Bearer {self.token}"}
                r = client.post(self.url, json=payload, headers=headers)
                r.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            # never log the message text or the token
            log.warning("whatsapp send failed: %s", type(exc).__name__)
            return False
