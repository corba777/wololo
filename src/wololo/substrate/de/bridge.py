"""DeSubstrate — the Substrate implementation backed by a running game.

Game analogy: instead of our simulated world, an actual Age of Empires II
DE match is the world; our tick is a real-time epoch of that match.  CS
meaning: `Substrate` over the file mailbox.  Unlike the sim kernel this is
inherently wall-clock bound (the game runs at its own pace), so `tick()`
blocks until the game acknowledges the epoch — but time sources are
injected, so tests run instantly against `FakeDeGame`.

Channel support (Milestone 3, step 1): taunts and market ops.  Relic ops,
movement, and fog need in-game counterparts that don't exist yet;
attempting them raises `DeBridgeError` rather than silently degrading.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from wololo.substrate.de.mailbox import FileMailbox
from wololo.substrate.de.protocol import (
    CMD_MARKET,
    CMD_TAUNT,
    OP_BUY,
    OP_SELL,
    RESOURCE_NAMES,
    ST_PRICE,
    ST_STOCK,
    ST_TAUNT,
    Frame,
    Record,
)
from wololo.substrate.interface import (
    MarketOpKind,
    Observation,
    RelicOpKind,
    Resource,
    Substrate,
    TauntEvent,
)

DEFAULT_TIMEOUT = 30.0
DEFAULT_POLL_INTERVAL = 0.2


class DeBridgeError(RuntimeError):
    """The game side misbehaved, timed out, or the channel is unsupported."""


class DeSubstrate(Substrate):
    """Substrate over a live DE match via the file mailbox."""

    def __init__(
        self,
        mailbox: FileMailbox,
        agent_ids: list[int],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._mailbox = mailbox
        self._agent_ids = sorted(agent_ids)
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._clock = clock
        self._seq = 0
        self._last_state_seq = 0
        self._pending: list[Record] = []
        self._observations: dict[int, Observation] = {}
        self._connected = False

    # -- lifecycle -----------------------------------------------------------

    def connect(self) -> None:
        """Wait for the game's first state frame and build tick-0 observations."""
        frame = self._await_frame(min_ack=0)
        self._apply_state(frame)
        self._connected = True

    # -- Substrate ops --------------------------------------------------------

    def taunt(self, agent_id: int, taunt: int) -> None:
        self._require(agent_id)
        if not 1 <= taunt <= 105:
            raise ValueError(f"taunt {taunt} out of range 1..105")
        self._pending.append(Record(CMD_TAUNT, (agent_id, taunt)))

    def market_op(self, agent_id: int, op: MarketOpKind, resource: Resource) -> None:
        self._require(agent_id)
        op_code = {"buy": OP_BUY, "sell": OP_SELL}.get(op)
        if op_code is None:
            raise ValueError(f"unknown market op {op!r}")
        resource_id = {"food": 0, "wood": 1, "stone": 2}.get(resource)
        if resource_id is None:
            raise ValueError(f"unknown resource {resource!r}")
        self._pending.append(Record(CMD_MARKET, (agent_id, op_code, resource_id)))

    def relic_op(self, agent_id: int, op: RelicOpKind, relic_id: str) -> None:
        raise DeBridgeError("relic channel is not implemented by the DE bridge yet")

    def move_op(self, agent_id: int, dx: int, dy: int) -> None:
        raise DeBridgeError("movement channel is not implemented by the DE bridge yet")

    def observe(self, agent_id: int) -> Observation:
        self._require(agent_id)
        if not self._connected:
            raise DeBridgeError("not connected; call connect() first")
        return self._observations[agent_id]

    def tick(self) -> int:
        """Send the epoch's commands; block until the game acknowledges them."""
        if not self._connected:
            raise DeBridgeError("not connected; call connect() first")
        self._seq += 1
        self._mailbox.send(
            Frame(seq=self._seq, ack=self._last_state_seq, records=tuple(self._pending))
        )
        self._pending.clear()
        frame = self._await_frame(min_ack=self._seq)
        self._apply_state(frame)
        return self._seq

    # -- internals -------------------------------------------------------------

    def _require(self, agent_id: int) -> None:
        if agent_id not in self._agent_ids:
            raise KeyError(f"unknown agent id {agent_id}")

    def _await_frame(self, min_ack: int) -> Frame:
        deadline = self._clock() + self._timeout
        while True:
            frame = self._mailbox.try_receive()
            if frame is not None and frame.ack >= min_ack:
                return frame
            if self._clock() >= deadline:
                raise DeBridgeError(
                    f"game did not acknowledge frame {min_ack} within {self._timeout}s"
                )
            self._sleep(self._poll_interval)

    def _apply_state(self, frame: Frame) -> None:
        self._last_state_seq = frame.seq
        prices: dict[str, int] = {}
        stock: dict[int, dict[str, int]] = {aid: {} for aid in self._agent_ids}
        taunts: list[TauntEvent] = []
        for record in frame.records:
            if record.type == ST_PRICE:
                resource_id, price = record.fields
                prices[RESOURCE_NAMES[resource_id]] = price
            elif record.type == ST_STOCK:
                agent_id, resource_id, amount = record.fields
                if agent_id in stock:
                    stock[agent_id][RESOURCE_NAMES[resource_id]] = amount
            elif record.type == ST_TAUNT:
                sender, taunt = record.fields
                taunts.append(
                    TauntEvent(tick=frame.seq, sender=sender, seq=len(taunts), taunt=taunt)
                )
            # unknown record types are ignored: forward compatibility
        for agent_id in self._agent_ids:
            self._observations[agent_id] = Observation(
                tick=frame.seq,
                agent_id=agent_id,
                pos=(0, 0),  # no positional channel over the bridge yet
                stockpile=dict(stock[agent_id]),
                prices=dict(prices),
                taunts=tuple(taunts),
                relics=(),
                rejections=(),
                explored=frozenset(),
            )
