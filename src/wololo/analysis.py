"""Protocol emergence metrics — taunt n-gram statistics.

Game analogy: reading the match's chat log afterwards and noticing that
"11 3" always preceded an attack.  CS meaning: n-gram frequency counts over
each sender's taunt stream, used to detect recurring conventions (an
emergent protocol) across runs.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from wololo.substrate.interface import TauntEvent


def taunt_ngrams(
    events: Sequence[TauntEvent],
    n: int = 2,
    *,
    per_sender: bool = True,
) -> Counter[tuple[int, ...]]:
    """Count taunt n-grams in a run's chat log.

    With ``per_sender`` (default) n-grams are counted within each sender's
    own consecutive stream — a protocol is what one speaker says in order —
    so interleaved chatter from other agents doesn't create phantom grams.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    streams: list[list[int]]
    if per_sender:
        by_sender: dict[int, list[int]] = {}
        for event in events:
            by_sender.setdefault(event.sender, []).append(event.taunt)
        streams = [by_sender[s] for s in sorted(by_sender)]
    else:
        streams = [[event.taunt for event in events]]

    counts: Counter[tuple[int, ...]] = Counter()
    for stream in streams:
        for i in range(len(stream) - n + 1):
            counts[tuple(stream[i : i + n])] += 1
    return counts


def format_top(counts: Counter[tuple[int, ...]], k: int = 10) -> str:
    """Human-readable top-k n-grams, most common first (ties by gram)."""
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    if not ranked:
        return "  (no taunts)"
    return "\n".join(f"  {' '.join(map(str, gram))}: {count}" for gram, count in ranked)
