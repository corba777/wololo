"""Experiment harness — batch scenario runs and aggregate protocol stats.

Game analogy: a tournament bracket with recorded games: play the same
matchup across many seeds, keep every chat log, and study the metagame
afterwards.  CS meaning: a multi-run driver producing per-run records
(outcome, tick count, full taunt log) plus cross-run n-gram aggregation —
the measurement instrument for protocol emergence.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from wololo.analysis import taunt_ngrams
from wololo.orchestrator.scenarios import Scenario, run_scenario
from wololo.substrate.interface import GOLD, TauntEvent


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One recorded game: outcome plus the full chat log."""

    scenario: str
    seed: int
    reached: bool
    ticks: int
    gold_total: int
    taunts: tuple[tuple[int, int, int], ...]  # (tick, sender, taunt)


def run_one(factory: Callable[[int], Scenario], seed: int) -> RunRecord:
    scenario = factory(seed)
    result = run_scenario(scenario)
    kernel = result.kernel
    return RunRecord(
        scenario=scenario.name,
        seed=seed,
        reached=result.reached,
        ticks=result.ticks,
        gold_total=sum(s.stockpile[GOLD] for s in kernel.agent_states.values()),
        taunts=tuple((e.tick, e.sender, e.taunt) for e in kernel.taunt_log),
    )


def run_batch(factory: Callable[[int], Scenario], seeds: Iterable[int]) -> list[RunRecord]:
    """Run the scenario once per seed; each run is fully independent."""
    return [run_one(factory, seed) for seed in seeds]


def aggregate_ngrams(records: Iterable[RunRecord], n: int = 2) -> Counter[tuple[int, ...]]:
    """Sum per-sender taunt n-grams across runs (n-grams never span runs)."""
    total: Counter[tuple[int, ...]] = Counter()
    for record in records:
        events = [
            TauntEvent(tick=tick, sender=sender, seq=i, taunt=taunt)
            for i, (tick, sender, taunt) in enumerate(record.taunts)
        ]
        total.update(taunt_ngrams(events, n))
    return total


def write_jsonl(records: Iterable[RunRecord], path: Path) -> None:
    """Persist run records, one JSON object per line."""
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(asdict(record)) + "\n")


def read_jsonl(path: Path) -> list[RunRecord]:
    """Load run records written by ``write_jsonl``."""
    records: list[RunRecord] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            data = json.loads(line)
            data["taunts"] = tuple(tuple(t) for t in data["taunts"])
            records.append(RunRecord(**data))
    return records
