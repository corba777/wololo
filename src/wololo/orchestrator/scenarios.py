"""Scenarios — map + goals + agent roster.

Game analogy: a custom scenario from the map editor: starting positions,
starting resources, relics on the map, and a victory trigger.  CS meaning:
a reproducible experiment definition; ``run_scenario`` builds the kernel,
wires the goal as a trigger, and drives the supervisor loop.

Includes ``coop_gather`` — the Milestone 1 definition-of-done scenario: two
scripted agents must reach a *team* gold goal, and the work split between
them is negotiated exclusively over the taunt channel (leader proposes a
resource split with a codec message; the follower stays idle until the
proposal arrives, then acknowledges and sells its assigned resource).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from wololo.agents.base import Action, Agent, MarketAction, TauntAction
from wololo.agents.fake import FakeLlm
from wololo.agents.llm import AnthropicClient, LlmAgent
from wololo.agents.tools import ToolLlmAgent
from wololo.codec import Message, encode_message, split_frames
from wololo.orchestrator.supervisor import AgentSpec, Supervisor
from wololo.substrate.interface import GOLD, Observation, Resource
from wololo.substrate.sim.kernel import SimKernel
from wololo.substrate.sim.market import LOT_SIZE
from wololo.substrate.sim.triggers import Trigger

GOAL_FLAG = "goal_reached"


@dataclass(frozen=True, slots=True)
class AgentSetup:
    """One roster slot: spawn point, starting stock, and the agent factory."""

    agent_id: int
    pos: tuple[int, int]
    stockpile: Mapping[str, int]
    factory: Callable[[], Agent]


@dataclass(frozen=True, slots=True)
class Scenario:
    """A reproducible experiment: map, roster, relics, goal, tick budget."""

    name: str
    map_size: tuple[int, int]
    agents: tuple[AgentSetup, ...]
    goal: Callable[[SimKernel], bool]
    relics: tuple[tuple[str, tuple[int, int]], ...] = ()
    max_ticks: int = 100
    seed: int = 0
    fog_radius: int = 2


@dataclass(slots=True)
class ScenarioResult:
    reached: bool
    ticks: int
    kernel: SimKernel
    supervisor: Supervisor


def build_kernel(scenario: Scenario) -> SimKernel:
    kernel = SimKernel(
        seed=scenario.seed, map_size=scenario.map_size, fog_radius=scenario.fog_radius
    )
    for relic_id, pos in scenario.relics:
        kernel.add_relic(relic_id, pos)
    for setup in scenario.agents:
        kernel.add_agent(setup.agent_id, setup.pos, dict(setup.stockpile))
    return kernel


def run_scenario(
    scenario: Scenario,
    on_tick: Callable[[SimKernel], None] | None = None,
) -> ScenarioResult:
    """Build the world, wire the goal as a trigger, and run the loop."""
    kernel = build_kernel(scenario)

    def goal_effect(k: SimKernel) -> None:
        k.flags[GOAL_FLAG] = True

    kernel.triggers.add(Trigger(name="goal", condition=scenario.goal, effect=goal_effect))

    supervisor = Supervisor(kernel, [AgentSpec(s.agent_id, s.factory) for s in scenario.agents])
    ticks = 0
    for _ in range(scenario.max_ticks):
        ticks = supervisor.run_tick()
        if on_tick is not None:
            on_tick(kernel)
        if kernel.flags.get(GOAL_FLAG):
            break
    return ScenarioResult(
        reached=bool(kernel.flags.get(GOAL_FLAG)),
        ticks=ticks,
        kernel=kernel,
        supervisor=supervisor,
    )


# ---------------------------------------------------------------------------
# coop_gather — Milestone 1 definition-of-done scenario
# ---------------------------------------------------------------------------

#: Protocol message kinds spoken over the taunt channel.
KIND_PROPOSE_SPLIT = 1
KIND_ACK = 2

_RESOURCE_ID: dict[Resource, int] = {"food": 0, "wood": 1, "stone": 2}
_ID_RESOURCE: dict[int, Resource] = {v: k for k, v in _RESOURCE_ID.items()}


def _leader_policy(obs: Observation, memory: dict[str, Any]) -> list[Action]:
    """Propose the split once (over taunts), then sell own resource each tick."""
    actions: list[Action] = []
    if not memory.get("proposed"):
        proposal = Message(KIND_PROPOSE_SPLIT, (_RESOURCE_ID["wood"], _RESOURCE_ID["stone"]))
        actions.extend(TauntAction(t) for t in encode_message(proposal))
        memory["proposed"] = True
    if obs.stockpile["wood"] >= LOT_SIZE:
        actions.append(MarketAction("sell", "wood"))
    return actions


def _follower_policy(obs: Observation, memory: dict[str, Any]) -> list[Action]:
    """Idle until a split proposal arrives over taunts; then ACK and work."""
    buffers: dict[int, list[int]] = memory.setdefault("buffers", {})
    for event in obs.taunts:
        if event.sender != obs.agent_id:
            buffers.setdefault(event.sender, []).append(event.taunt)

    actions: list[Action] = []
    for sender in sorted(buffers):
        messages, buffers[sender] = split_frames(buffers[sender])
        for message in messages:
            if message.kind == KIND_PROPOSE_SPLIT and "role" not in memory:
                mine = message.args[1]  # second slot of the split is ours
                memory["role"] = _ID_RESOURCE[mine]
                ack = Message(KIND_ACK, (mine,))
                actions.extend(TauntAction(t) for t in encode_message(ack))

    role: Resource | None = memory.get("role")
    if role is not None and obs.stockpile[role] >= LOT_SIZE:
        actions.append(MarketAction("sell", role))
    return actions


def coop_gather(seed: int = 0) -> Scenario:
    """Two agents, one team gold goal, negotiation via taunts only."""
    goal_gold = 600

    def goal(kernel: SimKernel) -> bool:
        return sum(s.stockpile[GOLD] for s in kernel.agent_states.values()) >= goal_gold

    return Scenario(
        name="coop_gather",
        map_size=(8, 8),
        seed=seed,
        max_ticks=30,
        agents=(
            AgentSetup(
                agent_id=0,
                pos=(1, 1),
                stockpile={"wood": 400},
                factory=lambda: FakeLlm(0, _leader_policy),
            ),
            AgentSetup(
                agent_id=1,
                pos=(6, 6),
                stockpile={"stone": 400},
                factory=lambda: FakeLlm(1, _follower_policy),
            ),
        ),
        goal=goal,
    )


# ---------------------------------------------------------------------------
# llm_gather — Milestone 2: two LLM agents negotiate the split over taunts
# ---------------------------------------------------------------------------

_LLM_GOAL_GOLD = 600

_LLM_ROLE_TEMPLATE = """\
You are on a two-agent team. TEAM GOAL: the combined gold of both agents
must reach {goal} as fast as possible. You start with {stock} and no gold.
You cannot see your teammate's stockpile, only global market prices and
taunts. Selling a resource lowers its price for everyone, so duplicated
work wastes gold: coordinate who sells what using taunts. Agree on your own
taunt conventions.\
"""


def _llm_role(stock: str) -> str:
    return _LLM_ROLE_TEMPLATE.format(goal=_LLM_GOAL_GOLD, stock=stock)


def llm_gather(
    seed: int = 0,
    client_factory: Callable[[], Any] | None = None,
    *,
    tools: bool = False,
) -> Scenario:
    """Cooperative gathering with LLM players (Anthropic API by default).

    With ``tools=True`` agents use the tool-use harness (including the codec
    helper tools) instead of raw JSON replies.  Tests inject deterministic
    stub clients via ``client_factory``; the CLI uses the real API (requires
    ANTHROPIC_API_KEY and ``wololo[llm]``).
    """
    factory: Callable[[], Any] = client_factory or AnthropicClient

    def goal(kernel: SimKernel) -> bool:
        return sum(s.stockpile[GOLD] for s in kernel.agent_states.values()) >= _LLM_GOAL_GOLD

    def make_agent(agent_id: int, stock: str) -> Agent:
        role = _llm_role(stock)
        if tools:
            return ToolLlmAgent(agent_id, role, factory())
        return LlmAgent(agent_id, role, factory())

    return Scenario(
        name="llm_gather_tools" if tools else "llm_gather",
        map_size=(8, 8),
        seed=seed,
        max_ticks=30,
        agents=(
            AgentSetup(
                agent_id=0,
                pos=(1, 1),
                stockpile={"wood": 400},
                factory=lambda: make_agent(0, "400 wood"),
            ),
            AgentSetup(
                agent_id=1,
                pos=(6, 6),
                stockpile={"stone": 400},
                factory=lambda: make_agent(1, "400 stone"),
            ),
        ),
        goal=goal,
    )


def _shipping_pipeline(seed: int = 0) -> Scenario:
    # Local import: shipping.py imports Scenario/AgentSetup from this module.
    from wololo.orchestrator.shipping import build_shipping_pipeline

    return build_shipping_pipeline(seed).scenario


def _newsroom_pipeline(seed: int = 0) -> Scenario:
    # Local import: newsroom.py imports Scenario/AgentSetup from this module.
    from wololo.orchestrator.newsroom import build_newsroom_pipeline

    return build_newsroom_pipeline(seed).scenario


def _relic_front_page(seed: int = 0) -> Scenario:
    from wololo.orchestrator.relic_front_page import build_relic_front_page_pipeline

    return build_relic_front_page_pipeline(seed).scenario


SCENARIOS: dict[str, Callable[[int], Scenario]] = {
    "coop_gather": coop_gather,
    "llm_gather": llm_gather,
    "llm_gather_tools": lambda seed=0: llm_gather(seed, tools=True),
    "shipping_pipeline": _shipping_pipeline,
    "newsroom_pipeline": _newsroom_pipeline,
    "relic_front_page": _relic_front_page,
}
