"""Small client for a chat-completions style endpoint."""

import os

import httpx

from app.guard import FALLBACK
from app.store import Fact

SYSTEM_MESSAGE = (
    "Eres el asistente de una tienda. Responde en español, en una o dos frases, usando "
    "solo los hechos numerados que se entregan. No agregues cifras, horarios, enlaces "
    "ni correos que no estén en los hechos. Termina la respuesta con el número del "
    "hecho que usaste entre corchetes, por ejemplo [2]. Si preguntan por un producto o "
    "servicio que no aparece en los hechos, no respondas con el dato de otro. Si "
    f"ningún hecho responde la pregunta, responde exactamente: {FALLBACK}"
)


class ChatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.transport = transport

    @classmethod
    def from_env(cls) -> "ChatClient | None":
        """Build a client from env vars, or None when no key is set."""
        api_key = os.environ.get("MODEL_API_KEY")
        base_url = os.environ.get("MODEL_BASE_URL")
        if not api_key or not base_url:
            return None
        return cls(base_url, api_key, os.environ.get("MODEL_NAME", "default"))

    def build_payload(self, question: str, facts: list[Fact]) -> dict:
        listing = "\n".join(f"{n}. {f.text}" for n, f in enumerate(facts, 1))
        return {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": f"Hechos:\n{listing}\n\nPregunta: {question}"},
            ],
        }

    def complete(self, question: str, facts: list[Fact]) -> str | None:
        """Return the model text, or None on any failure so the caller can fall back."""
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as http:
                resp = http.post(
                    self.url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=self.build_payload(question, facts),
                )
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            return None
        return text.strip() if isinstance(text, str) and text.strip() else None
