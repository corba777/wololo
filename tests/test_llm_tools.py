"""Tool harness tests — schemas, executors, and the ToolLlmAgent loop."""

from __future__ import annotations

import json

import pytest

from tests.test_llm_agent import make_observation
from wololo.agents.base import MarketAction, MoveAction, RelicAction, TauntAction
from wololo.agents.llm import LlmReply, ToolCall
from wololo.agents.tools import (
    HISTORY_TURNS,
    MAX_TOOL_ROUNDS,
    TOOL_DEFS,
    ToolLlmAgent,
    action_from_tool,
    run_helper_tool,
)
from wololo.codec import Message, encode_message


def tool_reply(*calls: ToolCall, text: str = "") -> LlmReply:
    raw: list[dict] = []
    if text:
        raw.append({"type": "text", "text": text})
    raw.extend({"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls)
    return LlmReply(raw_content=raw, tool_calls=calls, text=text)


def done_reply(text: str = "done") -> LlmReply:
    return LlmReply(raw_content=[{"type": "text", "text": text}], text=text)


class ScriptedToolClient:
    """Returns canned LlmReply objects in order; records every request."""

    def __init__(self, replies: list[LlmReply]) -> None:
        self._replies = list(replies)
        self.requests: list[list[dict]] = []

    def complete_tools(self, *, system: str, messages: list[dict], tools: list[dict]) -> LlmReply:
        assert system
        assert {t["name"] for t in TOOL_DEFS} <= {t["name"] for t in tools}
        self.requests.append([dict(m) for m in messages])
        return self._replies.pop(0)


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def test_action_from_tool_all_types() -> None:
    assert action_from_tool("send_taunt", {"taunt": 30}) == TauntAction(30)
    assert action_from_tool("trade", {"op": "sell", "resource": "stone"}) == MarketAction(
        "sell", "stone"
    )
    assert action_from_tool("relic", {"op": "grab", "relic_id": "r1"}) == RelicAction("grab", "r1")
    assert action_from_tool("move", {"dx": -1, "dy": 2}) == MoveAction(-1, 2)


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("send_taunt", {}),  # missing field
        ("send_taunt", {"taunt": "thirty"}),  # wrong type
        ("send_taunt", {"taunt": True}),  # bool is not an int
        ("trade", {"op": "steal", "resource": "wood"}),  # bad enum
        ("trade", {"op": "buy", "resource": "relics"}),  # bad enum
        ("move", {"dx": 1}),  # missing field
        ("encode_message", {"kind": 1}),  # helper, not an action
    ],
)
def test_action_from_tool_rejects_bad_input(name: str, payload: dict) -> None:
    with pytest.raises(ValueError):
        action_from_tool(name, payload)


def test_helper_tools_codec_round_trip() -> None:
    encoded = json.loads(run_helper_tool("encode_message", {"kind": 7, "args": [1, -2, 300]}))
    assert encoded["taunts"] == encode_message(Message(7, (1, -2, 300)))
    decoded = json.loads(run_helper_tool("decode_taunts", {"taunts": encoded["taunts"]}))
    assert decoded == {"messages": [{"kind": 7, "args": [1, -2, 300]}], "remainder": []}


def test_helper_tools_report_errors_instead_of_crashing() -> None:
    assert "error" in json.loads(run_helper_tool("encode_message", {"kind": -1}))
    assert "error" in json.loads(run_helper_tool("encode_message", {}))
    assert "error" in json.loads(run_helper_tool("decode_taunts", {"taunts": [999]}))
    partial = json.loads(run_helper_tool("decode_taunts", {"taunts": [53]}))
    assert partial == {"messages": [], "remainder": [53]}  # in-flight, not an error


# ---------------------------------------------------------------------------
# ToolLlmAgent loop
# ---------------------------------------------------------------------------


def test_agent_loop_helpers_then_actions_then_stop() -> None:
    client = ScriptedToolClient(
        [
            tool_reply(ToolCall("c1", "encode_message", {"kind": 1, "args": [2]})),
            tool_reply(
                ToolCall("c2", "send_taunt", {"taunt": 31}),
                ToolCall("c3", "trade", {"op": "sell", "resource": "wood"}),
            ),
            done_reply(),
        ]
    )
    agent = ToolLlmAgent(0, "test role", client)
    actions = agent.act(make_observation(agent_id=0))

    assert actions == [TauntAction(31), MarketAction("sell", "wood")]
    assert len(client.requests) == 3

    # Round 2 request must contain the helper's tool_result with matching id.
    second = client.requests[1]
    results = second[-1]["content"]
    assert second[-1]["role"] == "user"
    assert results[0]["tool_use_id"] == "c1"
    assert json.loads(results[0]["content"])["taunts"] == encode_message(Message(1, (2,)))

    # Round 3 request acknowledges both queued actions.
    third_results = client.requests[2][-1]["content"]
    assert [r["tool_use_id"] for r in third_results] == ["c2", "c3"]
    assert all(r["content"] == "queued for next tick" for r in third_results)


def test_agent_loop_bad_action_input_becomes_error_result() -> None:
    client = ScriptedToolClient(
        [
            tool_reply(ToolCall("c1", "trade", {"op": "steal", "resource": "wood"})),
            done_reply(),
        ]
    )
    agent = ToolLlmAgent(0, "test role", client)
    actions = agent.act(make_observation(agent_id=0))
    assert actions == []  # nothing queued
    error = json.loads(client.requests[1][-1]["content"][0]["content"])
    assert "op must be one of" in error["error"]


def test_agent_loop_capped_at_max_rounds() -> None:
    replies = [tool_reply(ToolCall(f"c{i}", "send_taunt", {"taunt": 1})) for i in range(50)]
    client = ScriptedToolClient(replies)
    agent = ToolLlmAgent(0, "test role", client)
    actions = agent.act(make_observation(agent_id=0))
    assert len(actions) == MAX_TOOL_ROUNDS
    assert len(client.requests) == MAX_TOOL_ROUNDS


def test_history_trimmed_by_whole_turns() -> None:
    n_ticks = HISTORY_TURNS + 4
    client = ScriptedToolClient([done_reply() for _ in range(n_ticks)])
    agent = ToolLlmAgent(0, "test role", client)
    for i in range(n_ticks):
        agent.act(make_observation(agent_id=0, tick=i + 1))
    last_request = client.requests[-1]
    # Each finished turn contributes 2 messages (obs + no-tool reply); the
    # request holds at most HISTORY_TURNS old turns plus the current obs.
    assert len(last_request) <= HISTORY_TURNS * 2 + 1
    assert last_request[0]["role"] == "user"
    assert isinstance(last_request[0]["content"], str)  # an observation, not tool results
