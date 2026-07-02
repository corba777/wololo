"""Simulated kernel — the deterministic tick-based world.

Game analogy: the game engine itself: it owns the map, the market, the
relics, and the chat, and advances the match one tick at a time.  CS
meaning: the reference `Substrate` implementation.  Fully deterministic
given a seed and the agents' decisions; all randomness flows through one
seeded ``random.Random`` owned here (unused so far, reserved for map
generation and similar).

Per-tick resolution order (each stage deterministic):

1. moves, sorted by (agent id, submission seq) — fog reveals as units move;
2. relic ops — releases, then grabs (lowest agent id wins contested grabs);
3. market ops, sorted by (agent id, submission seq);
4. taunt delivery — everything shouted since the last tick, ordered by
   (sender, seq), becomes visible to everyone;
5. trigger evaluation;
6. observation rebuild for every agent.

LLM calls happen strictly *between* ticks; nothing here awaits a model.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from wololo.substrate.interface import (
    GOLD,
    RESOURCES,
    MarketOpKind,
    Observation,
    Rejection,
    RelicOpKind,
    RelicView,
    Resource,
    Substrate,
    TauntEvent,
)
from wololo.substrate.sim.fog import FogOfWar
from wololo.substrate.sim.market import LOT_SIZE, Market
from wololo.substrate.sim.relics import RelicRegistry
from wololo.substrate.sim.taunts import TauntBus
from wololo.substrate.sim.triggers import TriggerEngine


def _empty_stockpile() -> dict[str, int]:
    return dict.fromkeys([*RESOURCES, GOLD], 0)


@dataclass(slots=True)
class AgentState:
    """One player's private state: position and stockpile."""

    agent_id: int
    pos: tuple[int, int]
    stockpile: dict[str, int] = field(default_factory=_empty_stockpile)


class SimKernel(Substrate):
    """Deterministic simulated substrate (see module docstring)."""

    def __init__(
        self,
        *,
        seed: int = 0,
        map_size: tuple[int, int] = (16, 16),
        fog_radius: int = 2,
        market: Market | None = None,
    ) -> None:
        self.rng = random.Random(seed)
        self.fog = FogOfWar(map_size[0], map_size[1], radius=fog_radius)
        self.market = market if market is not None else Market()
        self.relics = RelicRegistry()
        self.taunt_bus = TauntBus()
        self.triggers: TriggerEngine[SimKernel] = TriggerEngine()
        self.agent_states: dict[int, AgentState] = {}
        self.flags: dict[str, bool] = {}
        self.current_tick = 0
        self._seq = 0
        self._pending_moves: list[tuple[int, int, int, int]] = []  # (seq, agent, dx, dy)
        self._pending_relics: list[tuple[int, int, RelicOpKind, str]] = []
        self._pending_market: list[tuple[int, int, MarketOpKind, Resource]] = []
        self._observations: dict[int, Observation] = {}

    # -- world setup (orchestrator-only, before or between ticks) ----------

    def add_agent(
        self,
        agent_id: int,
        pos: tuple[int, int],
        stockpile: dict[str, int] | None = None,
    ) -> None:
        if agent_id in self.agent_states:
            raise ValueError(f"duplicate agent id {agent_id}")
        state = AgentState(agent_id=agent_id, pos=self.fog.clamp(pos))
        if stockpile:
            state.stockpile.update(stockpile)
        self.agent_states[agent_id] = state
        self.fog.add_agent(agent_id)
        self.fog.reveal(agent_id, state.pos)
        self._observations[agent_id] = self._build_observation(agent_id, (), ())

    def add_relic(self, relic_id: str, pos: tuple[int, int]) -> None:
        self.relics.add(relic_id, self.fog.clamp(pos))

    # -- Substrate ops (queued; resolved at the next tick) ------------------

    def taunt(self, agent_id: int, taunt: int) -> None:
        self._require_agent(agent_id)
        self.taunt_bus.send(agent_id, taunt)

    def market_op(self, agent_id: int, op: MarketOpKind, resource: Resource) -> None:
        self._require_agent(agent_id)
        if op not in ("buy", "sell"):
            raise ValueError(f"unknown market op {op!r}")
        if resource not in RESOURCES:
            raise ValueError(f"unknown resource {resource!r}")
        self._pending_market.append((self._next_seq(), agent_id, op, resource))

    def relic_op(self, agent_id: int, op: RelicOpKind, relic_id: str) -> None:
        self._require_agent(agent_id)
        if op not in ("grab", "release"):
            raise ValueError(f"unknown relic op {op!r}")
        self._pending_relics.append((self._next_seq(), agent_id, op, relic_id))

    def move_op(self, agent_id: int, dx: int, dy: int) -> None:
        self._require_agent(agent_id)
        self._pending_moves.append((self._next_seq(), agent_id, dx, dy))

    def observe(self, agent_id: int) -> Observation:
        self._require_agent(agent_id)
        return self._observations[agent_id]

    # -- tick ---------------------------------------------------------------

    def tick(self) -> int:
        self.current_tick += 1
        tick = self.current_tick
        rejections: dict[int, list[Rejection]] = {aid: [] for aid in self.agent_states}

        for _, agent_id, dx, dy in sorted(self._pending_moves, key=lambda m: (m[1], m[0])):
            state = self.agent_states[agent_id]
            state.pos = self.fog.clamp((state.pos[0] + dx, state.pos[1] + dy))
            self.fog.reveal(agent_id, state.pos)

        releases: list[tuple[int, str]] = []
        grabs: list[tuple[int, str]] = []
        for _, agent_id, op, relic_id in sorted(self._pending_relics, key=lambda r: (r[1], r[0])):
            relic = self.relics.get(relic_id)
            if relic is None:
                rejections[agent_id].append(
                    Rejection(tick, f"relic_{op}", f"{relic_id}: no such relic")
                )
            elif op == "grab" and not self.fog.sees(agent_id, relic.pos):
                rejections[agent_id].append(
                    Rejection(tick, "relic_grab", f"{relic_id}: not explored")
                )
            elif op == "release":
                releases.append((agent_id, relic_id))
            else:
                grabs.append((agent_id, relic_id))
        for agent_id, rejection in self.relics.resolve(tick, releases, grabs):
            rejections[agent_id].append(rejection)

        for _, agent_id, op, resource in sorted(self._pending_market, key=lambda m: (m[1], m[0])):
            stock = self.agent_states[agent_id].stockpile
            if op == "buy":
                price = self.market.price(resource)
                if stock[GOLD] < price:
                    rejections[agent_id].append(
                        Rejection(tick, "market_buy", f"{resource}: need {price} gold")
                    )
                else:
                    stock[GOLD] -= self.market.buy(resource)
                    stock[resource] += LOT_SIZE
            else:
                if stock[resource] < LOT_SIZE:
                    rejections[agent_id].append(
                        Rejection(tick, "market_sell", f"{resource}: need {LOT_SIZE} {resource}")
                    )
                else:
                    stock[resource] -= LOT_SIZE
                    stock[GOLD] += self.market.sell(resource)

        taunts = self.taunt_bus.flush(tick)
        self.triggers.evaluate(self)

        for agent_id in self.agent_states:
            self._observations[agent_id] = self._build_observation(
                agent_id, taunts, tuple(rejections[agent_id])
            )

        self._pending_moves.clear()
        self._pending_relics.clear()
        self._pending_market.clear()
        return tick

    # -- internals ------------------------------------------------------------

    def _require_agent(self, agent_id: int) -> None:
        if agent_id not in self.agent_states:
            raise KeyError(f"unknown agent id {agent_id}")

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _build_observation(
        self,
        agent_id: int,
        taunts: tuple[TauntEvent, ...],
        rejections: tuple[Rejection, ...],
    ) -> Observation:
        state = self.agent_states[agent_id]
        visible_relics = tuple(
            RelicView(r.relic_id, r.pos, r.owner)
            for r in self.relics.all()
            if self.fog.sees(agent_id, r.pos)
        )
        return Observation(
            tick=self.current_tick,
            agent_id=agent_id,
            pos=state.pos,
            stockpile=dict(state.stockpile),
            prices=self.market.prices,
            taunts=taunts,
            relics=visible_relics,
            rejections=rejections,
            explored=self.fog.explored(agent_id),
        )
