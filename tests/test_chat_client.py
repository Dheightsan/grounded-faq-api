import json

import httpx

from app.chat_client import SYSTEM_MESSAGE, ChatClient
from app.guard import FALLBACK
from app.retrieval import Retriever
from app.service import answer_question
from app.store import Fact, load_facts

FACTS = [Fact(id="precio-taladro", topic="precios", text="El taladro cuesta $42.990.")]


def make_client(handler) -> ChatClient:
    return ChatClient(
        "http://models.test/v1/",
        "test-key",
        "test-model",
        transport=httpx.MockTransport(handler),
    )


def test_request_is_built_correctly():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        body = {"choices": [{"message": {"content": " Cuesta $42.990. "}}]}
        return httpx.Response(200, json=body)

    out = make_client(handler).complete("cuanto cuesta el taladro", FACTS)

    assert out == "Cuesta $42.990."
    assert seen["url"] == "http://models.test/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    body = seen["body"]
    assert body["model"] == "test-model"
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_MESSAGE}
    assert FALLBACK in SYSTEM_MESSAGE
    assert "El taladro cuesta $42.990." in body["messages"][1]["content"]
    assert "cuanto cuesta el taladro" in body["messages"][1]["content"]


def test_server_error_returns_none():
    client = make_client(lambda request: httpx.Response(500))
    assert client.complete("hola", FACTS) is None


def test_bad_body_returns_none():
    client = make_client(lambda request: httpx.Response(200, json={"oops": 1}))
    assert client.complete("hola", FACTS) is None


def test_500_falls_back_to_extractive_answer():
    client = make_client(lambda request: httpx.Response(500))
    result = answer_question("cuanto cuesta el taladro", Retriever(load_facts()), client)
    assert result.grounded is True
    assert result.sources == ["precio-taladro"]
    assert "$42.990" in result.answer


def test_from_env_needs_key_and_base_url(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_BASE_URL", "http://models.test/v1")
    assert ChatClient.from_env() is None
    monkeypatch.setenv("MODEL_API_KEY", "k")
    monkeypatch.setenv("MODEL_NAME", "m")
    client = ChatClient.from_env()
    assert client is not None and client.model == "m"
