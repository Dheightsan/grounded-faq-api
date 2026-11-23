import pytest
from fastapi.testclient import TestClient

from app.guard import FALLBACK
from app.main import app, get_model


class FakeModel:
    def __init__(self, reply: str):
        self.reply = reply

    def complete(self, question, facts):
        return self.reply


@pytest.fixture
def client():
    app.dependency_overrides[get_model] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def use_model(reply: str):
    app.dependency_overrides[get_model] = lambda: FakeModel(reply)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_unknown_question_gets_fallback(client):
    r = client.post("/ask", json={"question": "cual es la capital de Francia"})
    assert r.status_code == 200
    assert r.json() == {"answer": FALLBACK, "grounded": False, "sources": []}


def test_known_question_extractive(client):
    r = client.post("/ask", json={"question": "cuanto cuesta el taladro"})
    data = r.json()
    assert data["grounded"] is True
    assert data["sources"] == ["precio-taladro"]
    assert "$42.990" in data["answer"]


def test_model_that_invents_a_number_is_blocked(client):
    use_model("El taladro cuesta $29.990.")
    data = client.post("/ask", json={"question": "cuanto cuesta el taladro"}).json()
    assert data == {"answer": FALLBACK, "grounded": False, "sources": []}


def test_model_with_valid_answer_passes(client):
    use_model("El taladro percutor cuesta $42.990. [1]")
    data = client.post("/ask", json={"question": "cuanto cuesta el taladro"}).json()
    assert data["answer"] == "El taladro percutor cuesta $42.990."
    assert data["grounded"] is True
    assert data["sources"] == ["precio-taladro"]


def test_model_answer_without_a_citation_is_dropped(client):
    use_model("Sale $42.990 el taladro.")
    data = client.post("/ask", json={"question": "cuanto cuesta el taladro"}).json()
    assert data["answer"] == "El taladro percutor de 650 W cuesta $42.990."
    assert data["sources"] == ["precio-taladro"]


def test_number_from_a_fact_the_model_did_not_cite_is_blocked(client):
    # fact 1 is the drill, the price in the answer is from another fact
    use_model("El taladro cuesta $3.490. [1]")
    data = client.post("/ask", json={"question": "cuanto cuesta el taladro y los clavos"}).json()
    assert data == {"answer": FALLBACK, "grounded": False, "sources": []}


def test_empty_question_is_rejected(client):
    assert client.post("/ask", json={"question": ""}).status_code == 422


def test_facts_filter_by_topic(client):
    r = client.get("/facts", params={"topic": "retiro"})
    assert {f["topic"] for f in r.json()} == {"retiro"}
    assert len(client.get("/facts").json()) >= 25
