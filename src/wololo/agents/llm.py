"""LLM-backed agent — a language model at the keyboard.

Game analogy: a human-like player who reads the screen, thinks, and issues
commands — and can only talk to teammates by shouting numbered taunts.  CS
meaning: an agent whose think-step is a chat completion.  The model sees a
text rendering of its observation and must reply with a JSON array of
actions (agent↔orchestrator traffic, which is private and allowed); all
agent↔agent traffic still flows through the substrate.

LLM calls happen strictly between ticks: the supervisor calls ``act()``
outside the kernel.  The ``anthropic`` dependency is confined to this module
and imported lazily, so the rest of the package stays stdlib-only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from wololo.agents.base import (
    Action,
    Agent,
    MarketAction,
    MoveAction,
    RelicAction,
    TauntAction,
)
from wololo.substrate.interface import Observation

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 1024

#: Keep the last N chat messages (user/assistant alternating) as memory.
HISTORY_LIMIT = 24


class LlmClient(Protocol):
    """Minimal chat-completion interface; tests provide deterministic stubs."""

    def complete(self, *, system: str, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool invocation requested by the model."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LlmReply:
    """A model turn in tool mode: raw content blocks plus parsed tool calls.

    ``raw_content`` is replayed verbatim into the conversation history so
    the provider sees its own tool_use blocks exactly as it produced them.
    """

    raw_content: list[dict[str, Any]]
    tool_calls: tuple[ToolCall, ...] = ()
    text: str = ""


class AnthropicClient:
    """Real client backed by the Anthropic API (requires ``wololo[llm]``)."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        api_key: str | None = None,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise RuntimeError(
                "the 'anthropic' package is required for LLM agents; "
                "install with: pip install 'wololo[llm]'"
            ) from exc
        kwargs = {"api_key": api_key} if api_key else {}
        self._client = anthropic.Anthropic(**kwargs)
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, *, system: str, messages: list[dict[str, str]]) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=messages,
        )
        return "".join(block.text for block in response.content if block.type == "text")

    def complete_tools(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LlmReply:
        """Tool-mode turn: returns raw content blocks plus parsed tool calls."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )
        return LlmReply(
            raw_content=[block.model_dump(exclude_none=True) for block in response.content],
            tool_calls=tuple(
                ToolCall(id=block.id, name=block.name, input=dict(block.input))
                for block in response.content
                if block.type == "tool_use"
            ),
            text="".join(block.text for block in response.content if block.type == "text"),
        )


#: Shared world description, reused by the JSON-mode and tool-mode prompts.
WORLD_RULES = """\
You are agent {agent_id} in "wololo", a cooperative match where the ONLY way
to communicate with other agents is by shouting numbered taunts (1..105).
There is no other channel; invent or follow whatever taunt conventions help.

World rules:
- Taunts you send this tick are heard by ALL agents (including you) next tick.
- Market: buy or sell one lot (100 units) of food/wood/stone for gold at a
  single global price per resource; every buy raises that price, every sell
  lowers it. Prices are visible to everyone.
- Relics: named relics can be grabbed (locked) by exactly one agent at a
  time and released. You must have explored a relic's tile to grab it.
- Moving by (dx, dy) reveals the map around you.
- Invalid ops come back as rejections in your next observation.

Your role:
{role}
"""

_SYSTEM_TEMPLATE = (
    WORLD_RULES
    + """
Each turn you receive your current observation. Reply with ONLY a JSON array
of actions and no other text. Action schemas:
  {{"type": "taunt", "taunt": 1-105}}
  {{"type": "market", "op": "buy"|"sell", "resource": "food"|"wood"|"stone"}}
  {{"type": "relic", "op": "grab"|"release", "relic_id": "<id>"}}
  {{"type": "move", "dx": <int>, "dy": <int>}}
An empty array [] means do nothing this tick.
"""
)


def render_observation(obs: Observation) -> str:
    """Render one observation as the model's turn prompt (stable format)."""
    lines = [
        f"tick {obs.tick}",
        f"you are agent {obs.agent_id} at {obs.pos}",
        "stockpile: " + " ".join(f"{k}={v}" for k, v in sorted(obs.stockpile.items())),
        "prices: " + " ".join(f"{k}={v}" for k, v in sorted(obs.prices.items())),
    ]
    if obs.taunts:
        heard: dict[int, list[int]] = {}
        for event in obs.taunts:
            heard.setdefault(event.sender, []).append(event.taunt)
        lines.append(
            "taunts heard: "
            + " | ".join(
                f"from {sender}: {' '.join(map(str, taunts))}"
                for sender, taunts in sorted(heard.items())
            )
        )
    else:
        lines.append("taunts heard: none")
    if obs.relics:
        lines.append(
            "relics visible: "
            + " | ".join(
                f"{r.relic_id} at {r.pos} "
                + ("unheld" if r.owner is None else f"held by agent {r.owner}")
                for r in obs.relics
            )
        )
    for rejection in obs.rejections:
        lines.append(f"rejected: {rejection.op}: {rejection.detail}")
    lines.append("Reply with a JSON array of actions.")
    return "\n".join(lines)


def parse_actions(text: str) -> list[Action]:
    """Parse the model's reply into actions; raise ValueError on garbage.

    Let-it-crash: a malformed reply raises, the supervisor respawns the
    agent.  We tolerate prose/fences around the array but nothing inside it.
    """
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON array in reply: {text!r}")
    try:
        raw = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"bad JSON in reply: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("reply is not a JSON array")

    actions: list[Action] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"action is not an object: {item!r}")
        match item.get("type"):
            case "taunt":
                actions.append(TauntAction(_int(item, "taunt")))
            case "market":
                actions.append(MarketAction(item["op"], item["resource"]))
            case "relic":
                actions.append(RelicAction(item["op"], str(item["relic_id"])))
            case "move":
                actions.append(MoveAction(_int(item, "dx"), _int(item, "dy")))
            case unknown:
                raise ValueError(f"unknown action type {unknown!r}")
    return actions


def _int(item: dict, key: str) -> int:
    value = item[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an int, got {value!r}")
    return value


class LlmAgent(Agent):
    """Agent whose decisions come from a chat model, with rolling memory.

    The conversation history is the agent's whole mental state; when the
    supervisor respawns a crashed LlmAgent, that history is gone (Nexus
    pattern, same as FakeLlm's memory dict).
    """

    def __init__(self, agent_id: int, role: str, client: LlmClient) -> None:
        super().__init__(agent_id)
        self._client = client
        self._system = _SYSTEM_TEMPLATE.format(agent_id=agent_id, role=role)
        self._history: list[dict[str, str]] = []

    def act(self, observation: Observation) -> list[Action]:
        self._history.append({"role": "user", "content": render_observation(observation)})
        if len(self._history) > HISTORY_LIMIT:
            self._history = self._history[-HISTORY_LIMIT:]
            while self._history and self._history[0]["role"] != "user":
                self._history.pop(0)
        reply = self._client.complete(system=self._system, messages=list(self._history))
        self._history.append({"role": "assistant", "content": reply})
        return parse_actions(reply)
