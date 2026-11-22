"""Static checks on the exported n8n workflows, so a bad edit is caught in CI."""

import json
from pathlib import Path

import pytest

N8N = Path(__file__).resolve().parent.parent / "n8n"
FILES = sorted(N8N.glob("*.json"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_workflow_is_consistent(path):
    wf = json.loads(path.read_text(encoding="utf-8"))
    names = [n["name"] for n in wf["nodes"]]
    assert len(names) == len(set(names))
    for source, outputs in wf["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            for link in branch:
                assert link["node"] in names


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_sending_nodes_ship_disabled(path):
    wf = json.loads(path.read_text(encoding="utf-8"))
    for node in wf["nodes"]:
        if "graph.facebook.com" in node["parameters"].get("url", ""):
            assert node.get("disabled") is True, node["name"]


def test_agent_calls_use_the_api_key_credential():
    wf = json.loads((N8N / "whatsapp_agent.json").read_text(encoding="utf-8"))
    ask = next(n for n in wf["nodes"] if n["name"] == "Ask the agent")
    assert ask["parameters"]["genericAuthType"] == "httpHeaderAuth"
    assert ask["credentials"]["httpHeaderAuth"]["name"] == "Agent API key"


def test_agent_call_passes_the_message_id():
    wf = json.loads((N8N / "whatsapp_agent.json").read_text(encoding="utf-8"))
    ask = next(n for n in wf["nodes"] if n["name"] == "Ask the agent")
    assert "id: $json.id" in ask["parameters"]["jsonBody"]


def test_notify_the_shop_is_wired_to_handoff_and_disabled():
    wf = json.loads((N8N / "whatsapp_agent.json").read_text(encoding="utf-8"))
    nodes = {n["name"]: n for n in wf["nodes"]}
    assert nodes["Notify the shop"]["disabled"] is True
    assert "$json.handoff" in json.dumps(nodes["Needs a person?"]["parameters"])
    ask_targets = [link["node"] for link in wf["connections"]["Ask the agent"]["main"][0]]
    assert "Needs a person?" in ask_targets
    true_branch = wf["connections"]["Needs a person?"]["main"][0]
    assert [link["node"] for link in true_branch] == ["Notify the shop"]
