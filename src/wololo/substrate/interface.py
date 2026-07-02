"""Substrate interface — the only surface agents may touch.

Game analogy: this is the game client.  A player can shout numbered taunts,
trade at the market, garrison/eject relics, move around the map, and look at
their own (fog-limited) screen.  CS meaning: the coordination substrate ABC
implemented by the simulated kernel (Milestone 1) and, later, by a bridge to
actual AoE II DE.  Agent↔agent traffic goes through these ops only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

Resource = Literal["food", "wood", "stone"]
RESOURCES: tuple[Resource, ...] = ("food", "wood", "stone")

#: Gold is the currency of the market, not a tradable commodity.
GOLD = "gold"

MarketOpKind = Literal["buy", "sell"]
RelicOpKind = Literal["grab", "release"]


@dataclass(frozen=True, slots=True)
class TauntEvent:
    """One taunt heard in chat — a single symbol on the message bus.

    ``seq`` preserves the order of taunts from the same sender within a tick,
    so multi-taunt codec messages arrive unscrambled.
    """

    tick: int
    sender: int
    seq: int
    taunt: int


@dataclass(frozen=True, slots=True)
class Rejection:
    """An op the kernel refused — the "unit shakes its head" feedback.

    CS meaning: a failed acquire / insufficient funds / invalid-op signal,
    delivered privately to the issuing agent in its next observation.
    """

    tick: int
    op: str
    detail: str


@dataclass(frozen=True, slots=True)
class RelicView:
    """A relic as seen on an agent's screen (position and current holder)."""

    relic_id: str
    pos: tuple[int, int]
    owner: int | None


@dataclass(frozen=True, slots=True)
class Observation:
    """What one agent sees after a tick — its own screen, under fog of war.

    Prices and taunts are global (everyone hears chat, everyone sees the
    market).  Relics are fog-filtered.  Stockpile and rejections are private.
    """

    tick: int
    agent_id: int
    pos: tuple[int, int]
    stockpile: dict[str, int]
    prices: dict[str, int]
    taunts: tuple[TauntEvent, ...]
    relics: tuple[RelicView, ...]
    rejections: tuple[Rejection, ...]
    explored: frozenset[tuple[int, int]]


class Substrate(ABC):
    """Coordination substrate ABC.

    Ops submitted between ticks are queued; ``tick()`` resolves them all
    atomically and deterministically, then refreshes every agent's
    observation.  Implementations must never call an LLM.
    """

    @abstractmethod
    def tick(self) -> int:
        """Advance the world by one tick; return the new tick number."""

    @abstractmethod
    def taunt(self, agent_id: int, taunt: int) -> None:
        """Shout taunt 1..105 — broadcast one symbol on the message bus."""

    @abstractmethod
    def market_op(self, agent_id: int, op: MarketOpKind, resource: Resource) -> None:
        """Buy or sell one lot at the global market — nudge shared state."""

    @abstractmethod
    def relic_op(self, agent_id: int, op: RelicOpKind, relic_id: str) -> None:
        """Garrison or eject a relic — acquire or release a distributed lock."""

    @abstractmethod
    def move_op(self, agent_id: int, dx: int, dy: int) -> None:
        """Move on the map — expand this agent's explored region (fog)."""

    @abstractmethod
    def observe(self, agent_id: int) -> Observation:
        """Return the agent's latest observation (snapshot at last tick)."""
