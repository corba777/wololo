"""Taunt bus — the broadcast message channel.

Game analogy: numbered taunts land in everyone's chat; there is no whisper.
CS meaning: a broadcast bus over a 105-symbol alphabet.  Taunts sent in tick
*t* are visible to all agents (including the sender) in tick *t+1*, in stable
order: (sender id, per-sender sequence).
"""

from __future__ import annotations

from wololo.substrate.interface import TauntEvent

TAUNT_MIN = 1
TAUNT_MAX = 105


class TauntBus:
    """Collects taunts during the acting phase; delivers them at the tick."""

    def __init__(self) -> None:
        self._pending: list[tuple[int, int, int]] = []  # (sender, seq, taunt)
        self._seqs: dict[int, int] = {}

    def send(self, sender: int, taunt: int) -> None:
        if not isinstance(taunt, int) or isinstance(taunt, bool):
            raise ValueError(f"taunt must be an int, got {taunt!r}")
        if not TAUNT_MIN <= taunt <= TAUNT_MAX:
            raise ValueError(f"taunt {taunt} out of range {TAUNT_MIN}..{TAUNT_MAX}")
        seq = self._seqs.get(sender, 0)
        self._seqs[sender] = seq + 1
        self._pending.append((sender, seq, taunt))

    def flush(self, tick: int) -> tuple[TauntEvent, ...]:
        """Deliver everything pending, stably ordered by (sender, seq)."""
        events = tuple(
            TauntEvent(tick=tick, sender=s, seq=q, taunt=t) for s, q, t in sorted(self._pending)
        )
        self._pending.clear()
        return events
