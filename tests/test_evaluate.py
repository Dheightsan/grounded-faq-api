from app.evaluate import run_questions, run_scripts, summarize


def test_eval_numbers_do_not_drop():
    total = summarize(run_questions())[-1]
    assert total["answerable"] == 38
    assert total["out_of_scope"] == 20
    assert total["correct"] >= 30
    assert total["rejected"] >= 2
    assert total["wrong"] <= 23


def test_conversation_scripts_do_not_drop():
    scripts = run_scripts()
    assert len(scripts) == 8
    assert sum(scripts.values()) >= 7
    assert scripts["book an appointment"]
    assert scripts["two customers, same slot"]
