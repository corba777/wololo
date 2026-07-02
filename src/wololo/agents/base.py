"""Agent ABC — a player at the keyboard.

Game analogy: a player looks at their screen (observation) and issues
commands (actions); the engine executes them at the next tick.  CS meaning:
the observe → think → act loop.  Actions are the *only* output channel an
agent has; there is no side-channel to other agents.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from wololo.substrate.interface import MarketOpKind, Observation, RelicOpKind, Resource


@dataclass(frozen=True, slots=True)
class TauntAction:
    """Shout one taunt (1..105)."""

    taunt: int


@dataclass(frozen=True, slots=True)
class MarketAction:
    """Buy or sell one lot of a resource at the global market."""

    op: MarketOpKind
    resource: Resource


@dataclass(frozen=True, slots=True)
class RelicAction:
    """Grab (lock) or release (unlock) a relic."""

    op: RelicOpKind
    relic_id: str


@dataclass(frozen=True, slots=True)
class MoveAction:
    """Move by (dx, dy) on the map, exploring as you go."""

    dx: int
    dy: int


Action = TauntAction | MarketAction | RelicAction | MoveAction


class Agent(ABC):
    """One player: holds an id, turns observations into actions."""

    def __init__(self, agent_id: int) -> None:
        self.agent_id = agent_id

    @abstractmethod
    def act(self, observation: Observation) -> list[Action]:
        """Decide this tick's actions.  May raise — the supervisor respawns."""
