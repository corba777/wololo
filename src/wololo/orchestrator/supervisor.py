"""Supervisor — spawns, monitors, and respawns agents (let-it-crash).

Game analogy: the tournament admin: seats the players, enforces turn order,
and if a player rage-quits (raises), seats a fresh replacement who has lost
all mental state.  CS meaning: an Erlang-style supervisor around the
observe → think → act loop.  The supervisor may talk to agents privately;
agent↔agent traffic still flows only through the substrate.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from wololo.agents.base import (
    Action,
    Agent,
    MarketAction,
    MoveAction,
    RelicAction,
    TauntAction,
)
from wololo.substrate.interface import Substrate

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """How to (re)create one agent: its id plus a fresh-instance factory."""

    agent_id: int
    factory: Callable[[], Agent]


class Supervisor:
    """Drives the acting phase each tick and applies let-it-crash restarts.

    Agents act in ascending id order.  If an agent raises anywhere in its
    observe → act → submit sequence, its remaining actions are dropped, the
    exception is logged, and a fresh instance replaces it next tick.  Ops it
    already submitted this tick stand (a player disconnecting mid-command).
    """

    def __init__(self, substrate: Substrate, specs: Sequence[AgentSpec]) -> None:
        ids = [spec.agent_id for spec in specs]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate agent ids in specs")
        self._substrate = substrate
        self._specs = {spec.agent_id: spec for spec in specs}
        self._agents: dict[int, Agent] = {spec.agent_id: spec.factory() for spec in specs}
        self.restarts: dict[int, int] = dict.fromkeys(ids, 0)

    def run_tick(self) -> int:
        """One full cycle: every agent acts, then the world ticks."""
        for agent_id in sorted(self._agents):
            agent = self._agents[agent_id]
            try:
                observation = self._substrate.observe(agent_id)
                for action in agent.act(observation):
                    self._submit(agent_id, action)
            except Exception:
                logger.exception("agent %d crashed; respawning", agent_id)
                self.restarts[agent_id] += 1
                self._agents[agent_id] = self._specs[agent_id].factory()
        return self._substrate.tick()

    def run(self, max_ticks: int, stop: Callable[[], bool] | None = None) -> int:
        """Run up to ``max_ticks`` cycles; return the last tick number."""
        tick = 0
        for _ in range(max_ticks):
            tick = self.run_tick()
            if stop is not None and stop():
                break
        return tick

    def _submit(self, agent_id: int, action: Action) -> None:
        match action:
            case TauntAction(taunt=taunt):
                self._substrate.taunt(agent_id, taunt)
            case MarketAction(op=op, resource=resource):
                self._substrate.market_op(agent_id, op, resource)
            case RelicAction(op=op, relic_id=relic_id):
                self._substrate.relic_op(agent_id, op, relic_id)
            case MoveAction(dx=dx, dy=dy):
                self._substrate.move_op(agent_id, dx, dy)
            case _:
                raise TypeError(f"unknown action {action!r}")
