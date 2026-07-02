"""FakeDeGame — an offline stand-in for the game half of the bridge.

Game analogy: a sparring dummy dressed as Age of Empires II.  CS meaning:
implements the exact mailbox behavior the in-game XS script must have —
poll the command file, apply new commands, rewrite the state file with an
incremented seq and an ack of the last command frame processed.  It reuses
the sim market rules so bridge tests can assert the same economics as the
kernel.  This class is the executable specification for ``wololo.xs``.
"""

from __future__ import annotations

from pathlib import Path

from wololo.substrate.de.mailbox import FileMailbox
from wololo.substrate.de.protocol import (
    CMD_MARKET,
    CMD_TAUNT,
    OP_BUY,
    RESOURCE_IDS,
    ST_PRICE,
    ST_STOCK,
    ST_TAUNT,
    Frame,
    Record,
)
from wololo.substrate.interface import GOLD, RESOURCES
from wololo.substrate.sim.market import LOT_SIZE, Market


class FakeDeGame:
    """The XS side of the mailbox, in Python, for tests and protocol work."""

    def __init__(
        self,
        cmd_path: Path,
        state_path: Path,
        stockpiles: dict[int, dict[str, int]],
        *,
        market: Market | None = None,
    ) -> None:
        # Mirror of the bridge's mailbox: we receive commands, send state.
        self._mailbox = FileMailbox(send_path=state_path, recv_path=cmd_path)
        self.market = market if market is not None else Market()
        self.stockpiles = {
            agent_id: {**dict.fromkeys([*RESOURCES, GOLD], 0), **stock}
            for agent_id, stock in stockpiles.items()
        }
        self._epoch = 0
        self._acked = 0

    def start(self) -> None:
        """Write the initial state frame (epoch 0 world, nothing acked)."""
        self._write_state(taunts=[])

    def step(self) -> None:
        """One game rule tick: apply any new command frame, publish state."""
        frame = self._mailbox.try_receive()
        taunts: list[tuple[int, int]] = []
        if frame is not None:
            for record in frame.records:
                if record.type == CMD_TAUNT:
                    sender, taunt = record.fields
                    taunts.append((sender, taunt))
                elif record.type == CMD_MARKET:
                    self._apply_market(*record.fields)
            self._acked = frame.seq
        self._write_state(taunts=taunts)

    def _apply_market(self, agent_id: int, op_code: int, resource_id: int) -> None:
        resource = {0: "food", 1: "wood", 2: "stone"}[resource_id]
        stock = self.stockpiles[agent_id]
        if op_code == OP_BUY:
            price = self.market.price(resource)
            if stock[GOLD] >= price:
                stock[GOLD] -= self.market.buy(resource)
                stock[resource] += LOT_SIZE
        elif stock[resource] >= LOT_SIZE:
            stock[resource] -= LOT_SIZE
            stock[GOLD] += self.market.sell(resource)

    def _write_state(self, taunts: list[tuple[int, int]]) -> None:
        self._epoch += 1
        records: list[Record] = [
            Record(ST_PRICE, (RESOURCE_IDS[r], self.market.price(r))) for r in RESOURCES
        ]
        for agent_id, stock in sorted(self.stockpiles.items()):
            records.extend(
                Record(ST_STOCK, (agent_id, RESOURCE_IDS[key], amount))
                for key, amount in sorted(stock.items(), key=lambda kv: RESOURCE_IDS[kv[0]])
            )
        records.extend(Record(ST_TAUNT, (sender, taunt)) for sender, taunt in taunts)
        self._mailbox.send(Frame(seq=self._epoch, ack=self._acked, records=tuple(records)))
