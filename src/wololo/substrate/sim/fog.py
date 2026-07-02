"""Fog of war — per-agent partial observability.

Game analogy: the map starts black; terrain scouted once stays "explored" on
your screen.  CS meaning: a per-agent visibility mask restricting which parts
of shared state the kernel includes in that agent's observations.

Simplification for Milestone 1: explored tiles show *current* state (no
"last seen" ghosting).
"""

from __future__ import annotations


class FogOfWar:
    """Tracks each agent's explored tiles on a ``width x height`` grid."""

    def __init__(self, width: int, height: int, radius: int = 2) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"map size must be positive, got {width}x{height}")
        if radius < 0:
            raise ValueError(f"radius must be non-negative, got {radius}")
        self.width = width
        self.height = height
        self.radius = radius
        self._explored: dict[int, set[tuple[int, int]]] = {}

    def add_agent(self, agent_id: int) -> None:
        self._explored.setdefault(agent_id, set())

    def clamp(self, pos: tuple[int, int]) -> tuple[int, int]:
        """Clip a position to the map bounds (units can't walk off the edge)."""
        x, y = pos
        return (min(max(x, 0), self.width - 1), min(max(y, 0), self.height - 1))

    def reveal(self, agent_id: int, pos: tuple[int, int]) -> None:
        """Reveal all tiles within Chebyshev ``radius`` of ``pos``."""
        x0, y0 = pos
        tiles = self._explored[agent_id]
        for x in range(max(0, x0 - self.radius), min(self.width, x0 + self.radius + 1)):
            for y in range(max(0, y0 - self.radius), min(self.height, y0 + self.radius + 1)):
                tiles.add((x, y))

    def sees(self, agent_id: int, pos: tuple[int, int]) -> bool:
        return pos in self._explored[agent_id]

    def explored(self, agent_id: int) -> frozenset[tuple[int, int]]:
        return frozenset(self._explored[agent_id])
