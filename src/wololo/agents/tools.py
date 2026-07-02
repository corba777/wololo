"""Tool harness for LLM agents — schemas, executors, and the tool loop.

Game analogy: hotkeys plus a court scribe.  Instead of typing free text, the
player presses buttons (action tools) to queue commands for the next tick,
and can ask the scribe (codec helper tools) to encode or decode structured
taunt messages — a local service that never touches the world.  CS meaning:
an Anthropic tool-use harness around the observe → think → act loop.  Action
tools accumulate substrate ops; helper tools are pure computation whose
results are fed back as tool_results.  All calls still happen between ticks.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol

from wololo.agents.base import (
    Action,
    Agent,
    MarketAction,
    MoveAction,
    RelicAction,
    TauntAction,
)
from wololo.agents.llm import WORLD_RULES, LlmReply, render_observation
from wololo.codec import (
    TAUNT_MAX,
    TAUNT_MIN,
    Message,
    TauntDecodeError,
    encode_message,
    split_frames,
)
from wololo.substrate.interface import Observation

#: Safety valve: max model turns (tool rounds) per tick.
MAX_TOOL_ROUNDS = 8

#: Rolling memory: keep the last N tick-turns (each turn = one observation
#: plus all its tool rounds).  Trimming whole turns never splits a tool_use
#: from its tool_result.
HISTORY_TURNS = 8

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "send_taunt",
        "description": "Queue shouting one numbered taunt. Everyone (including you) "
        "hears it next tick. This is your only channel to other agents.",
        "input_schema": {
            "type": "object",
            "properties": {"taunt": {"type": "integer", "minimum": 1, "maximum": 105}},
            "required": ["taunt"],
        },
    },
    {
        "name": "trade",
        "description": "Queue buying or selling one lot (100 units) of a resource "
        "for gold at the global market price.",
        "input_schema": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["buy", "sell"]},
                "resource": {"type": "string", "enum": ["food", "wood", "stone"]},
            },
            "required": ["op", "resource"],
        },
    },
    {
        "name": "relic",
        "description": "Queue grabbing (locking) or releasing a named relic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["grab", "release"]},
                "relic_id": {"type": "string"},
            },
            "required": ["op", "relic_id"],
        },
    },
    {
        "name": "move",
        "description": "Queue moving by (dx, dy); reveals the map around you.",
        "input_schema": {
            "type": "object",
            "properties": {"dx": {"type": "integer"}, "dy": {"type": "integer"}},
            "required": ["dx", "dy"],
        },
    },
    {
        "name": "encode_message",
        "description": "Codec helper (local, free, invisible to others): encode a "
        "structured message — kind (int >= 0) plus integer args — into the taunt "
        "numbers to shout, using the shared self-delimiting codec.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "integer", "minimum": 0},
                "args": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "decode_taunts",
        "description": "Codec helper (local, free): decode a sequence of taunt "
        "numbers into structured messages. Returns complete messages plus the "
        "undecoded remainder (a message still in flight).",
        "input_schema": {
            "type": "object",
            "properties": {"taunts": {"type": "array", "items": {"type": "integer"}}},
            "required": ["taunts"],
        },
    },
]

ACTION_TOOL_NAMES = frozenset({"send_taunt", "trade", "relic", "move"})
HELPER_TOOL_NAMES = frozenset({"encode_message", "decode_taunts"})


def action_from_tool(name: str, payload: dict[str, Any]) -> Action:
    """Turn an action-tool call into an Action; raise ValueError on bad input."""
    try:
        match name:
            case "send_taunt":
                return TauntAction(_int(payload["taunt"], "taunt"))
            case "trade":
                return MarketAction(
                    _choice(payload["op"], ("buy", "sell"), "op"),
                    _choice(payload["resource"], ("food", "wood", "stone"), "resource"),
                )
            case "relic":
                return RelicAction(
                    _choice(payload["op"], ("grab", "release"), "op"),
                    str(payload["relic_id"]),
                )
            case "move":
                return MoveAction(_int(payload["dx"], "dx"), _int(payload["dy"], "dy"))
            case _:
                raise ValueError(f"not an action tool: {name}")
    except KeyError as exc:
        raise ValueError(f"missing field {exc} for tool {name}") from exc


def run_helper_tool(name: str, payload: dict[str, Any]) -> str:
    """Execute a codec helper locally; bad model input comes back as an error
    tool_result (so the model can correct itself) instead of crashing."""
    try:
        if name == "encode_message":
            kind = _int(payload["kind"], "kind")
            args = tuple(_int(a, "args[]") for a in payload.get("args", []))
            return json.dumps({"taunts": encode_message(Message(kind, args))})
        if name == "decode_taunts":
            taunts = [_int(t, "taunts[]") for t in payload["taunts"]]
            for taunt in taunts:
                if not TAUNT_MIN <= taunt <= TAUNT_MAX:
                    raise ValueError(f"taunt {taunt} out of range {TAUNT_MIN}..{TAUNT_MAX}")
            messages, remainder = split_frames(taunts)
            return json.dumps(
                {
                    "messages": [{"kind": m.kind, "args": list(m.args)} for m in messages],
                    "remainder": remainder,
                }
            )
    except (KeyError, ValueError, TauntDecodeError) as exc:
        return json.dumps({"error": str(exc)})
    raise ValueError(f"not a helper tool: {name}")


def _int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an int, got {value!r}")
    return value


def _choice(value: Any, allowed: tuple[str, ...], name: str) -> Any:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, got {value!r}")
    return value


class ToolLlmClient(Protocol):
    """Chat client with tool use; tests provide deterministic stubs."""

    def complete_tools(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LlmReply: ...


class ToolProvider(Protocol):
    """A pluggable source of extra tools for one agent (e.g. an MCP server).

    Provider tools connect an agent privately to the outside world (email,
    spreadsheets, ...).  They must never carry agent↔agent traffic — that
    stays on the substrate.  ``execute`` returns the tool_result string;
    faults should come back as an ``{"error": ...}`` JSON string so the
    model can self-correct.
    """

    def tool_defs(self) -> list[dict[str, Any]]: ...

    def execute(self, name: str, payload: dict[str, Any]) -> str: ...


_TOOL_SYSTEM_TEMPLATE = (
    WORLD_RULES
    + """
Each turn you receive your current observation. Use the tools to act:
- Action tools (send_taunt, trade, relic, move) queue ops that all execute
  together at the next tick, in the order you called them.
- Codec helpers (encode_message, decode_taunts) run locally and cost
  nothing; use them to speak structured messages over the taunt channel.
When you are done for this tick, reply without calling any tool.
"""
)


class ToolLlmAgent(Agent):
    """Tool-using LLM agent: an agentic loop within each tick.

    Per act(): the model may take up to MAX_TOOL_ROUNDS turns, mixing helper
    calls (answered immediately) and action calls (queued, acknowledged);
    the tick's actions are whatever got queued when the model stops calling
    tools.  History is the agent's whole mental state — a respawn wipes it.
    """

    def __init__(
        self,
        agent_id: int,
        role: str,
        client: ToolLlmClient,
        providers: Sequence[ToolProvider] = (),
    ) -> None:
        super().__init__(agent_id)
        self._client = client
        self._system = _TOOL_SYSTEM_TEMPLATE.format(agent_id=agent_id, role=role)
        self._turns: list[list[dict[str, Any]]] = []
        self._providers: dict[str, ToolProvider] = {}
        self._tool_defs = list(TOOL_DEFS)
        reserved = ACTION_TOOL_NAMES | HELPER_TOOL_NAMES
        for provider in providers:
            for tool in provider.tool_defs():
                name = tool["name"]
                if name in reserved or name in self._providers:
                    raise ValueError(f"duplicate tool name {name!r}")
                self._providers[name] = provider
                self._tool_defs.append(tool)

    def act(self, observation: Observation) -> list[Action]:
        group: list[dict[str, Any]] = [{"role": "user", "content": render_observation(observation)}]
        actions: list[Action] = []
        for _ in range(MAX_TOOL_ROUNDS):
            reply = self._client.complete_tools(
                system=self._system,
                messages=self._flatten() + group,
                tools=self._tool_defs,
            )
            group.append({"role": "assistant", "content": reply.raw_content})
            if not reply.tool_calls:
                break
            results: list[dict[str, Any]] = []
            for call in reply.tool_calls:
                if call.name in HELPER_TOOL_NAMES:
                    content = run_helper_tool(call.name, call.input)
                elif call.name in ACTION_TOOL_NAMES:
                    try:
                        actions.append(action_from_tool(call.name, call.input))
                        content = "queued for next tick"
                    except ValueError as exc:
                        content = json.dumps({"error": str(exc)})
                elif (provider := self._providers.get(call.name)) is not None:
                    content = provider.execute(call.name, call.input)
                else:
                    content = json.dumps({"error": f"unknown tool {call.name!r}"})
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": content})
            group.append({"role": "user", "content": results})
        self._turns.append(group)
        del self._turns[:-HISTORY_TURNS]
        return actions

    def _flatten(self) -> list[dict[str, Any]]:
        return [message for turn in self._turns for message in turn]
