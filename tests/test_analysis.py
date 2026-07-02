"""Taunt n-gram statistics tests."""

from __future__ import annotations

import pytest

from wololo.analysis import format_top, taunt_ngrams
from wololo.substrate.interface import TauntEvent


def events(*items: tuple[int, int]) -> list[TauntEvent]:
    """Build a chat log from (sender, taunt) pairs, in order."""
    return [
        TauntEvent(tick=i + 1, sender=sender, seq=i, taunt=taunt)
        for i, (sender, taunt) in enumerate(items)
    ]


def test_bigrams_counted_within_sender_streams() -> None:
    # Sender 0 says 1 2 1 2; sender 1 interleaves with 9 9.
    log = events((0, 1), (1, 9), (0, 2), (1, 9), (0, 1), (0, 2))
    counts = taunt_ngrams(log, 2)
    assert counts[(1, 2)] == 2
    assert counts[(9, 9)] == 1
    # No phantom cross-sender grams from the interleaving.
    assert counts[(1, 9)] == 0 and counts[(2, 9)] == 0


def test_global_stream_when_per_sender_disabled() -> None:
    log = events((0, 1), (1, 9), (0, 2))
    counts = taunt_ngrams(log, 2, per_sender=False)
    assert counts[(1, 9)] == 1 and counts[(9, 2)] == 1


def test_unigrams_and_empty_log() -> None:
    assert taunt_ngrams([], 2) == {}
    log = events((0, 5), (0, 5), (0, 7))
    counts = taunt_ngrams(log, 1)
    assert counts[(5,)] == 2 and counts[(7,)] == 1


def test_ngram_longer_than_stream_counts_nothing() -> None:
    log = events((0, 1), (0, 2))
    assert taunt_ngrams(log, 3) == {}


def test_invalid_n_rejected() -> None:
    with pytest.raises(ValueError):
        taunt_ngrams([], 0)


def test_format_top_ranks_by_count_then_gram() -> None:
    log = events((0, 1), (0, 2), (0, 1), (0, 2), (0, 1))
    text = format_top(taunt_ngrams(log, 2), k=2)
    lines = text.splitlines()
    assert lines[0].strip() == "1 2: 2"
    assert lines[1].strip() == "2 1: 2"
    assert format_top(taunt_ngrams([], 2)) == "  (no taunts)"
