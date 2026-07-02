"""llm_gather_tools end-to-end with stub tool-using "models" — no network.

The stubs drive the full tool loop: the leader *encodes* its proposal with
the encode_message helper and shouts the resulting taunts; the follower
*decodes* what it heard with decode_taunts and only then starts working.
The negotiation therefore round-trips through the real codec, over the real
taunt bus, via the real tool harness.
"""

from __future__ import annotations

import json
import re

from wololo.agents.llm import LlmReply, ToolCall
from wololo.orchestrator.scenarios import llm_gather, run_scenario
from wololo.substrate.interface import GOLD

PROPOSE = 1  # kind: "I take arg0, you take arg1" (resource ids: wood=1, stone=2)


def tool_reply(*calls: ToolCall) -> LlmReply:
    raw = [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
    return LlmReply(raw_content=raw, tool_calls=calls)


def done_reply() -> LlmReply:
    return LlmReply(raw_content=[{"type": "text", "text": "done"}])


def parse_result(content: str) -> dict:
    """Helper results are JSON; action acks ('queued for next tick') are not."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def latest_observation(messages: list[dict]) -> str:
    """The current tick's observation is the last string user message."""
    for message in reversed(messages):
        if message["role"] == "user" and isinstance(message["content"], str):
            return message["content"]
    raise AssertionError("no observation in request")


class LeaderToolModel:
    """Encodes PROPOSE(wood, stone) via the helper, shouts it, sells wood."""

    def __init__(self) -> None:
        self._ids = iter(range(1000))
        self._taunts_to_send: list[int] | None = None
        self._announced = False

    def complete_tools(self, *, system: str, messages: list[dict], tools: list[dict]) -> LlmReply:
        last = messages[-1]
        # A helper result just arrived: read the encoded taunts.
        if last["role"] == "user" and isinstance(last["content"], list):
            payload = parse_result(last["content"][0]["content"])
            if "taunts" in payload:
                self._taunts_to_send = payload["taunts"]
                calls = [
                    ToolCall(f"t{next(self._ids)}", "send_taunt", {"taunt": t})
                    for t in self._taunts_to_send
                ] + [ToolCall(f"t{next(self._ids)}", "trade", {"op": "sell", "resource": "wood"})]
                self._announced = True
                return tool_reply(*calls)
            return done_reply()  # acks for queued actions: stop for this tick
        # New tick observation.
        if not self._announced:
            return tool_reply(
                ToolCall(f"t{next(self._ids)}", "encode_message", {"kind": PROPOSE, "args": [1, 2]})
            )
        if "wood=0" not in latest_observation(messages).split("prices:")[0]:
            return tool_reply(
                ToolCall(f"t{next(self._ids)}", "trade", {"op": "sell", "resource": "wood"})
            )
        return done_reply()


class FollowerToolModel:
    """Decodes heard taunts via the helper; works only after PROPOSE."""

    def __init__(self) -> None:
        self._ids = iter(range(1000))
        self._role: str | None = None

    def complete_tools(self, *, system: str, messages: list[dict], tools: list[dict]) -> LlmReply:
        last = messages[-1]
        # A helper result just arrived: adopt the proposed role if complete.
        if last["role"] == "user" and isinstance(last["content"], list):
            payload = parse_result(last["content"][0]["content"])
            for message in payload.get("messages", []):
                if message["kind"] == PROPOSE:
                    self._role = {1: "wood", 2: "stone"}[message["args"][1]]
                    return tool_reply(
                        ToolCall(
                            f"f{next(self._ids)}",
                            "trade",
                            {"op": "sell", "resource": self._role},
                        )
                    )
            return done_reply()
        # New tick observation.
        obs = latest_observation(messages)
        if self._role is None:
            heard = re.search(r"from 0: ([\d ]+)", obs)
            if heard:
                taunts = [int(t) for t in heard.group(1).split()]
                return tool_reply(
                    ToolCall(f"f{next(self._ids)}", "decode_taunts", {"taunts": taunts})
                )
            return done_reply()
        if f"{self._role}=0" not in obs.split("prices:")[0]:
            return tool_reply(
                ToolCall(f"f{next(self._ids)}", "trade", {"op": "sell", "resource": self._role})
            )
        return done_reply()


def make_scenario(seed: int = 0):
    models = iter([LeaderToolModel(), FollowerToolModel()])
    return llm_gather(seed=seed, client_factory=lambda: next(models), tools=True)


def test_tools_scenario_reaches_goal_through_codec() -> None:
    result = run_scenario(make_scenario())
    assert result.reached, f"goal not reached in {result.ticks} ticks"
    total = sum(s.stockpile[GOLD] for s in result.kernel.agent_states.values())
    assert total >= 600
    assert all(s.stockpile[GOLD] > 0 for s in result.kernel.agent_states.values())
    assert result.supervisor.restarts == {0: 0, 1: 0}


def test_tools_scenario_taunt_log_contains_codec_frame() -> None:
    from wololo.codec import Message, encode_message

    result = run_scenario(make_scenario())
    leader_taunts = [e.taunt for e in result.kernel.taunt_log if e.sender == 0]
    wire = encode_message(Message(PROPOSE, (1, 2)))
    assert leader_taunts[: len(wire)] == wire  # the proposal went out verbatim
