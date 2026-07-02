"""CLI — run a scenario and print tick logs.

Game analogy: watching the recorded game with the chat overlay on.  CS
meaning: the only UI: a per-tick text log of prices, stockpiles, relic
holders, and taunt traffic.
"""

from __future__ import annotations

import argparse
import sys

from wololo.analysis import format_top, taunt_ngrams
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
    args = parser.parse_args(argv)

    scenario = SCENARIOS[args.scenario](args.seed)
    result = run_scenario(scenario, on_tick=None if args.quiet else _print_tick)

    status = "GOAL REACHED" if result.reached else "goal not reached"
    print(f"{scenario.name}: {status} after {result.ticks} ticks (seed {scenario.seed})")
    if args.stats:
        for n in (2, 3):
            print(f"top taunt {n}-grams:")
            print(format_top(taunt_ngrams(result.kernel.taunt_log, n)))
    return 0 if result.reached else 1


if __name__ == "__main__":
    sys.exit(main())
