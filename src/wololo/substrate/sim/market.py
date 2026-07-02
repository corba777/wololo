"""Global market — shared scalar state with price drift.

Game analogy: the AoE II market: one global price per commodity, every buy
pushes it up, every sell pushes it down.  CS meaning: the only globally
visible mutable scalars — a slow-consensus / signalling channel that agents
can move only by paying (trading).
"""

from __future__ import annotations

from wololo.substrate.interface import RESOURCES, Resource

#: One market transaction trades this many units of a resource.
LOT_SIZE = 100

DEFAULT_START_PRICE = 100
DEFAULT_STEP = 5
DEFAULT_MIN_PRICE = 20
DEFAULT_MAX_PRICE = 9999


class Market:
    """Single global price per resource, drifting a fixed step per trade."""

    def __init__(
        self,
        *,
        start_price: int = DEFAULT_START_PRICE,
        step: int = DEFAULT_STEP,
        min_price: int = DEFAULT_MIN_PRICE,
        max_price: int = DEFAULT_MAX_PRICE,
    ) -> None:
        if not 0 < min_price <= start_price <= max_price:
            raise ValueError("require 0 < min_price <= start_price <= max_price")
        self._step = step
        self._min = min_price
        self._max = max_price
        self._prices: dict[str, int] = {r: start_price for r in RESOURCES}

    @property
    def prices(self) -> dict[str, int]:
        return dict(self._prices)

    def price(self, resource: Resource) -> int:
        return self._prices[resource]

    def buy(self, resource: Resource) -> int:
        """Buy one lot: return gold cost at the current price; price drifts up."""
        price = self._prices[resource]
        self._prices[resource] = min(price + self._step, self._max)
        return price

    def sell(self, resource: Resource) -> int:
        """Sell one lot: return gold gained at the current price; price drifts down."""
        price = self._prices[resource]
        self._prices[resource] = max(price - self._step, self._min)
        return price
