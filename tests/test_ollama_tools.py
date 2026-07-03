"""OllamaClient tool mode — Anthropic-shaped history ⇄ Ollama wire format."""

from __future__ import annotations

from typing import Any

from wololo.agents.ollama import OllamaClient

TOOLS = [
    {
        "name": "send_taunt",
        "description": "shout",
        "input_schema": {"type": "object", "properties": {"taunt": {"type": "integer"}}},
    }
]


def test_complete_tools_translates_both_directions() -> None:
    seen: dict[str, Any] = {}

    def transport(_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen["payload"] = payload
        return {
            "message": {
                "role": "assistant",
                "content": "shouting",
                "tool_calls": [{"function": {"name": "send_taunt", "arguments": {"taunt": 31}}}],
            }
        }

    client = OllamaClient("m", transport=transport)
    reply = client.complete_tools(
        system="sys", messages=[{"role": "user", "content": "obs"}], tools=TOOLS
    )

    # Outbound: tool schema converted to Ollama function-calling format.
    assert seen["payload"]["tools"][0]["function"]["name"] == "send_taunt"
    # Inbound: reply parsed into Anthropic-shaped blocks and ToolCalls.
    assert reply.text == "shouting"
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == "send_taunt"
    assert reply.tool_calls[0].input == {"taunt": 31}
    assert reply.raw_content[0] == {"type": "text", "text": "shouting"}
    assert reply.raw_content[1]["type"] == "tool_use"


def test_history_with_tool_results_is_replayed_as_tool_roles() -> None:
    transcripts: list[list[dict[str, Any]]] = []

    def transport(_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        transcripts.append(payload["messages"])
        return {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "send_taunt", "arguments": {"taunt": 1}}}],
            }
        }

    client = OllamaClient("m", transport=transport)
    first = client.complete_tools(
        system="s", messages=[{"role": "user", "content": "obs"}], tools=TOOLS
    )
    call_id = first.tool_calls[0].id

    history = [
        {"role": "user", "content": "obs"},
        {"role": "assistant", "content": first.raw_content},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "queued"}],
        },
    ]
    client.complete_tools(system="s", messages=history, tools=TOOLS)

    replayed = transcripts[-1]
    assert replayed[0] == {"role": "system", "content": "s"}
    assert replayed[1] == {"role": "user", "content": "obs"}
    assert replayed[2]["role"] == "assistant"
    assert replayed[2]["tool_calls"][0]["function"]["arguments"] == {"taunt": 1}
    assert replayed[3] == {"role": "tool", "tool_name": "send_taunt", "content": "queued"}


def test_string_encoded_arguments_are_parsed() -> None:
    def transport(_url: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "send_taunt", "arguments": '{"taunt": 5}'}}],
            }
        }

    client = OllamaClient("m", transport=transport)
    reply = client.complete_tools(system="s", messages=[], tools=TOOLS)
    assert reply.tool_calls[0].input == {"taunt": 5}
