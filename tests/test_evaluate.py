import json
import re

import pytest
from httpx import MockTransport, Response

from app.chat_client import ChatClient
from app.evaluate import (
    DATA,
    CountedModel,
    main,
    render,
    run_questions,
    run_scripts,
    summarize,
)


def test_eval_numbers_do_not_drop():
    total = summarize(run_questions())[-1]
    assert total["answerable"] == 38
    assert total["out_of_scope"] == 20
    assert total["correct"] >= 30
    assert total["rejected"] >= 17
    assert total["wrong"] <= 4


def test_conversation_scripts_do_not_drop():
    scripts = run_scripts()
    assert len(scripts) == 8
    assert sum(scripts.values()) == 8
    assert scripts["book an appointment"]
    assert scripts["two customers, same slot"]


def test_heldout_numbers_do_not_drop():
    total = summarize(run_questions(DATA / "eval_heldout.json"))[-1]
    assert total["correct"] >= 15
    assert total["wrong"] <= 2


def mock_model(handler, **kwargs) -> CountedModel:
    transport = MockTransport(handler)
    client = ChatClient("http://models.test/v1", "k", "test-model", transport=transport)
    return CountedModel(client, "test-model", wait=0, **kwargs)


def reply(text: str) -> Response:
    return Response(200, json={"choices": [{"message": {"content": text}}]})


def first_fact(request) -> str:
    user = json.loads(request.content)["messages"][1]["content"]
    return re.search(r"^1\. (.+)$", user, re.MULTILINE).group(1)


def test_with_model_that_quotes_the_best_fact():
    model = mock_model(lambda request: reply(f"{first_fact(request)} [1]"))
    rows = run_questions(model=model)
    total = summarize(rows)[-1]
    plain = summarize(run_questions())[-1]
    # it also answers the weak matches, so it gets more right and more wrong
    assert total["correct"] > plain["correct"]
    assert total["wrong"] > plain["wrong"]
    assert model.calls == sum(r["step"] != "no_match" for r in rows)
    assert model.failed == 0
    assert {r["step"] for r in rows} == {"model", "rescued", "no_match"}


def test_with_model_counts_what_the_guard_blocks():
    model = mock_model(lambda request: reply("Eso cuesta $99.999 y abrimos a las 03:17. [1]"))
    rows = run_questions(model=model)
    total = summarize(rows)[-1]
    assert total["correct"] == 0 and total["wrong"] == 0
    blocked = sum(r["step"] == "blocked" for r in rows)
    assert blocked == model.calls > 0
    text = render(summarize(rows), {}, rows, False, model)
    assert f"model: test-model, {model.calls} calls, 0 failed" in text
    assert f"answers blocked by the guard: {blocked}" in text


def test_with_model_that_refuses_everything():
    model = mock_model(lambda request: reply("No tengo ese dato."))
    total = summarize(run_questions(model=model))[-1]
    assert total["correct"] == 0
    assert total["missed"] == 38
    assert total["rejected"] == 20


def test_failed_calls_are_retried_and_counted():
    seen = []

    def flaky(request):
        seen.append(1)
        return Response(429) if len(seen) == 1 else reply("Cuesta $42.990. [1]")

    model = mock_model(flaky)
    assert model.complete("cuanto cuesta el taladro", []) == "Cuesta $42.990. [1]"
    assert (model.calls, model.failed, len(seen)) == (1, 0, 2)

    down = mock_model(lambda request: Response(500), retries=1)
    assert down.complete("hola", []) is None
    assert (down.calls, down.failed) == (1, 1)


def test_with_model_needs_the_env_vars(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["evaluate", "--with-model"])
    with pytest.raises(SystemExit) as exit_:
        main()
    assert "MODEL_API_KEY" in str(exit_.value)
