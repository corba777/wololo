"""Relics — distributed mutexes.

Game analogy: one relic fits one monastery; whoever garrisons it holds it
until they eject it.  CS meaning: named locks.  Contested grabs within a
tick resolve by deterministic priority (lowest agent id wins); every loser
gets a private rejection observation.
"""

from __future__ import annotations

from dataclasses import dataclass

from wololo.substrate.interface import Rejection


@dataclass(slots=True)
class Relic:
    relic_id: str
    pos: tuple[int, int]
    owner: int | None = None


class RelicRegistry:
    """Owns all relics; resolves grab/release batches atomically per tick."""

    def __init__(self) -> None:
        self._relics: dict[str, Relic] = {}

    def add(self, relic_id: str, pos: tuple[int, int]) -> None:
        if relic_id in self._relics:
            raise ValueError(f"duplicate relic id {relic_id!r}")
        self._relics[relic_id] = Relic(relic_id, pos)

    def get(self, relic_id: str) -> Relic | None:
        return self._relics.get(relic_id)

    def all(self) -> tuple[Relic, ...]:
        return tuple(self._relics[k] for k in sorted(self._relics))

    def resolve(
        self,
        tick: int,
        releases: list[tuple[int, str]],
        grabs: list[tuple[int, str]],
    ) -> list[tuple[int, Rejection]]:
        """Apply one tick's relic ops atomically; return (agent, rejection) pairs.

        Releases are applied before grabs so release-and-regrab chains within
        one tick behave predictably.  Grabs are processed sorted by
        (relic_id, agent_id): per relic, the lowest agent id wins.
        """
        rejections: list[tuple[int, Rejection]] = []

        for agent_id, relic_id in sorted(releases):
            relic = self._relics[relic_id]
            if relic.owner != agent_id:
                rejections.append(
                    (
                        agent_id,
                        Rejection(tick, "relic_release", f"{relic_id}: not the owner"),
                    )
                )
            else:
                relic.owner = None

        for agent_id, relic_id in sorted(grabs, key=lambda g: (g[1], g[0])):
            relic = self._relics[relic_id]
            if relic.owner is None:
                relic.owner = agent_id
            elif relic.owner == agent_id:
                rejections.append(
                    (
                        agent_id,
                        Rejection(tick, "relic_grab", f"{relic_id}: already garrisoned by you"),
                    )
                )
            else:
                rejections.append(
                    (
                        agent_id,
                        Rejection(
                            tick,
                            "relic_grab",
                            f"{relic_id}: garrisoned by agent {relic.owner}",
                        ),
                    )
                )
        return rejections
