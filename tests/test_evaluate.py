from app.evaluate import DATA, run_questions, run_scripts, summarize


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
