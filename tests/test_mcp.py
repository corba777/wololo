"""MCP bridge tests — provider adaptation and agent integration."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.test_llm_agent import make_observation
from tests.test_llm_tools import ScriptedToolClient, done_reply, tool_reply
from wololo.agents.llm import ToolCall
from wololo.agents.mcp import McpToolProvider
from wololo.agents.tools import TOOL_DEFS, ToolLlmAgent


class RecordingSession:
    """Fake MCP session that records calls and returns scripted results."""

    def __init__(self, result: Any = "ok") -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "check_inbox",
                "description": "Check for new mail.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "send_reply",
                "description": "Reply to a mail.",
                "input_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_provider_prefixes_names_and_preserves_schema() -> None:
    provider = McpToolProvider(RecordingSession(), prefix="email_")
    defs = provider.tool_defs()
    assert [t["name"] for t in defs] == ["email_check_inbox", "email_send_reply"]
    assert defs[1]["input_schema"]["required"] == ["text"]


def test_provider_strips_prefix_when_calling_the_session() -> None:
    session = RecordingSession(result="2 new emails")
    provider = McpToolProvider(session, prefix="email_")
    assert provider.execute("email_check_inbox", {}) == "2 new emails"
    assert session.calls == [("check_inbox", {})]


def test_provider_serializes_non_string_results() -> None:
    provider = McpToolProvider(RecordingSession(result={"emails": []}), prefix="email_")
    assert json.loads(provider.execute("email_check_inbox", {})) == {"emails": []}


def test_provider_wraps_session_faults_as_error_results() -> None:
    provider = McpToolProvider(RecordingSession(result=RuntimeError("auth expired")))
    error = json.loads(provider.execute("check_inbox", {}))
    assert error == {"error": "auth expired"}


def test_provider_rejects_unknown_names() -> None:
    provider = McpToolProvider(RecordingSession(), prefix="email_")
    assert "error" in json.loads(provider.execute("email_nope", {}))


def test_agent_routes_provider_tools_and_advertises_them() -> None:
    session = RecordingSession(result="inbox empty")
    provider = McpToolProvider(session, prefix="email_")
    client = ScriptedToolClient([tool_reply(ToolCall("c1", "email_check_inbox", {})), done_reply()])
    agent = ToolLlmAgent(0, "test role", client, providers=[provider])
    actions = agent.act(make_observation(agent_id=0))

    assert actions == []  # provider tools never queue substrate ops
    assert session.calls == [("check_inbox", {})]
    result = client.requests[1][-1]["content"][0]
    assert result["tool_use_id"] == "c1" and result["content"] == "inbox empty"


def test_agent_rejects_tool_name_collisions() -> None:
    class CollidingSession(RecordingSession):
        def list_tools(self) -> list[dict[str, Any]]:
            return [{"name": "send_taunt", "description": "x", "input_schema": {}}]

    with pytest.raises(ValueError, match="duplicate tool name"):
        ToolLlmAgent(
            0,
            "test role",
            ScriptedToolClient([]),
            providers=[McpToolProvider(CollidingSession())],
        )
    assert any(t["name"] == "send_taunt" for t in TOOL_DEFS)  # why it collides
