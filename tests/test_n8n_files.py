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
