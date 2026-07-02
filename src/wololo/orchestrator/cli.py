"""CLI — run a scenario and print tick logs.

Game analogy: watching the recorded game with the chat overlay on.  CS
meaning: the only UI: a per-tick text log of prices, stockpiles, relic
holders, and taunt traffic.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wololo.analysis import format_top, taunt_ngrams
from wololo.orchestrator.harness import aggregate_ngrams, run_batch, write_jsonl
from wololo.orchestrator.scenarios import SCENARIOS, run_scenario
from wololo.substrate.interface import GOLD
from wololo.substrate.sim.kernel import SimKernel


def _print_tick(kernel: SimKernel) -> None:
    any_agent = min(kernel.agent_states)
    observation = kernel.observe(any_agent)
    gold = {aid: s.stockpile[GOLD] for aid, s in sorted(kernel.agent_states.items())}
    print(f"tick {kernel.current_tick:3d} | prices {kernel.market.prices} | gold {gold}")
    for event in observation.taunts:
        print(f"    taunt: agent {event.sender} shouts {event.taunt}")
    for relic in kernel.relics.all():
        if relic.owner is not None:
            print(f"    relic {relic.relic_id} held by agent {relic.owner}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wololo", description="Run a wololo scenario.")
    parser.add_argument("scenario", choices=sorted(SCENARIOS), help="scenario name")
    parser.add_argument("--seed", type=int, default=0, help="kernel seed (default 0)")
    parser.add_argument("--quiet", action="store_true", help="suppress tick logs")
    parser.add_argument(
        "--stats", action="store_true", help="print taunt n-gram stats (protocol emergence)"
    )
    parser.add_argument(
        "--runs", type=int, default=1, help="run N seeds (seed..seed+N-1) via the batch harness"
    )
    parser.add_argument(
        "--record", type=Path, default=None, help="write per-run JSONL records to this path"
    )
    args = parser.parse_args(argv)

    if args.runs > 1 or args.record is not None:
        return _run_batch(args)

    scenario = SCENARIOS[args.scenario](args.seed)
    result = run_scenario(scenario, on_tick=None if args.quiet else _print_tick)

    status = "GOAL REACHED" if result.reached else "goal not reached"
    print(f"{scenario.name}: {status} after {result.ticks} ticks (seed {scenario.seed})")
    if args.stats:
        for n in (2, 3):
            print(f"top taunt {n}-grams:")
            print(format_top(taunt_ngrams(result.kernel.taunt_log, n)))
    return 0 if result.reached else 1


def _run_batch(args: argparse.Namespace) -> int:
    records = run_batch(SCENARIOS[args.scenario], range(args.seed, args.seed + args.runs))
    for record in records:
        outcome = "reached" if record.reached else "FAILED"
        print(
            f"seed {record.seed}: {outcome} in {record.ticks} ticks, "
            f"gold {record.gold_total}, {len(record.taunts)} taunts"
        )
    if args.stats:
        for n in (2, 3):
            print(f"top taunt {n}-grams across {len(records)} runs:")
            print(format_top(aggregate_ngrams(records, n)))
    if args.record is not None:
        write_jsonl(records, args.record)
        print(f"wrote {len(records)} records to {args.record}")
    return 0 if all(record.reached for record in records) else 1


if __name__ == "__main__":
    sys.exit(main())
