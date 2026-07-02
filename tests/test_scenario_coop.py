"""Milestone 1 definition-of-done: two agents coordinate a resource goal
via taunts only, deterministically, goal reached within the tick budget."""

from __future__ import annotations

from wololo.orchestrator.scenarios import coop_gather, run_scenario
from wololo.substrate.interface import GOLD


def test_coop_gather_reaches_goal_via_taunts_only() -> None:
    result = run_scenario(coop_gather(seed=7))
    assert result.reached, f"goal not reached in {result.ticks} ticks"
    total_gold = sum(s.stockpile[GOLD] for s in result.kernel.agent_states.values())
    assert total_gold >= 600
    # Both agents contributed: the split was actually negotiated and followed.
    assert all(s.stockpile[GOLD] > 0 for s in result.kernel.agent_states.values())
    assert result.supervisor.restarts == {0: 0, 1: 0}


def test_follower_is_idle_until_proposal_arrives() -> None:
    """Coordination happens over the taunt channel, not out-of-band.

    Timeline: leader shouts the proposal while acting in tick 1; the taunt
    bus delivers it with tick 1's observations; the follower first *sees* it
    when acting in tick 2.  So after tick 1 the follower must not have
    traded, and by tick 2 it must have started.
    """
    scenario = coop_gather(seed=0)
    kernels = []

    def capture(kernel) -> None:
        follower = kernel.agent_states[1]
        kernels.append((kernel.current_tick, follower.stockpile[GOLD], follower.stockpile["stone"]))

    run_scenario(scenario, on_tick=capture)

    tick1 = kernels[0]
    assert tick1 == (1, 0, 400)  # untouched: proposal not yet seen
    tick2 = kernels[1]
    assert tick2[1] > 0 or tick2[2] < 400  # first sale resolved in tick 2


def test_coop_gather_is_deterministic() -> None:
    def fingerprint() -> tuple:
        result = run_scenario(coop_gather(seed=3))
        kernel = result.kernel
        return (
            result.reached,
            result.ticks,
            tuple(sorted(kernel.market.prices.items())),
            tuple(
                (aid, tuple(sorted(s.stockpile.items())))
                for aid, s in sorted(kernel.agent_states.items())
            ),
        )

    assert fingerprint() == fingerprint()
