"""Experiment harness tests — batch runs, aggregation, JSONL round-trip."""

from __future__ import annotations

from pathlib import Path

from wololo.analysis import taunt_ngrams
from wololo.orchestrator.harness import (
    RunRecord,
    aggregate_ngrams,
    read_jsonl,
    run_batch,
    write_jsonl,
)
from wololo.orchestrator.scenarios import coop_gather
from wololo.substrate.interface import TauntEvent


def test_run_batch_over_seeds() -> None:
    records = run_batch(coop_gather, seeds=range(3))
    assert len(records) == 3
    assert [r.seed for r in records] == [0, 1, 2]
    assert all(r.reached for r in records)
    assert all(r.scenario == "coop_gather" for r in records)
    assert all(r.gold_total >= 600 for r in records)
    assert all(r.taunts for r in records)  # negotiation is on the record


def test_batch_runs_are_independent_and_deterministic() -> None:
    a = run_batch(coop_gather, seeds=[5, 5])
    assert a[0] == a[1]  # same seed twice -> identical records


def test_aggregate_ngrams_sums_per_run_counts() -> None:
    records = run_batch(coop_gather, seeds=range(2))
    total = aggregate_ngrams(records, 2)
    per_run = [
        taunt_ngrams(
            [TauntEvent(tick=t, sender=s, seq=i, taunt=x) for i, (t, s, x) in enumerate(r.taunts)],
            2,
        )
        for r in records
    ]
    assert total == per_run[0] + per_run[1]


def test_jsonl_round_trip(tmp_path: Path) -> None:
    records = run_batch(coop_gather, seeds=range(2))
    path = tmp_path / "runs.jsonl"
    write_jsonl(records, path)
    loaded = read_jsonl(path)
    assert loaded == records
    assert isinstance(loaded[0], RunRecord)
    assert path.read_text().count("\n") == 2
