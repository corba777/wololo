"""DeSubstrate tests against FakeDeGame — the offline bridge contract.

The fake game is the executable spec for the in-game XS script; these
tests pin the semantics the real game side must reproduce.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wololo.agents.fake import FakeLlm
from wololo.orchestrator.supervisor import AgentSpec, Supervisor
from wololo.substrate.de.bridge import DeBridgeError, DeSubstrate
from wololo.substrate.de.fakegame import FakeDeGame
from wololo.substrate.de.mailbox import FileMailbox


def make_pair(tmp_path: Path, stockpiles: dict[int, dict[str, int]] | None = None):
    cmd = tmp_path / "wololo_cmd.xsdat"
    state = tmp_path / "wololo_state.xsdat"
    game = FakeDeGame(cmd, state, stockpiles or {0: {"wood": 400}, 1: {"stone": 400}})
    bridge = DeSubstrate(
        FileMailbox(send_path=cmd, recv_path=state),
        agent_ids=[0, 1],
        timeout=5.0,
        # Each poll advances the fake game one rule tick: no threads, no clock.
        sleep=lambda _s: game.step(),
        clock=_FakeClock(),
    )
    game.start()
    bridge.connect()
    return bridge, game


class _FakeClock:
    def __init__(self) -> None:
        self._now = 0.0

    def __call__(self) -> float:
        self._now += 0.01
        return self._now


def test_connect_builds_initial_observations(tmp_path: Path) -> None:
    bridge, _ = make_pair(tmp_path)
    obs = bridge.observe(0)
    assert obs.stockpile["wood"] == 400
    assert obs.prices == {"food": 100, "wood": 100, "stone": 100}
    assert obs.taunts == ()


def test_taunt_round_trip_over_files(tmp_path: Path) -> None:
    bridge, _ = make_pair(tmp_path)
    bridge.taunt(0, 31)
    bridge.taunt(0, 105)
    bridge.tick()
    for agent_id in (0, 1):
        events = bridge.observe(agent_id).taunts
        assert [(e.sender, e.taunt) for e in events] == [(0, 31), (0, 105)]
    bridge.tick()
    assert bridge.observe(1).taunts == ()  # no replay


def test_market_semantics_match_the_sim(tmp_path: Path) -> None:
    bridge, _ = make_pair(tmp_path)
    bridge.market_op(0, "sell", "wood")
    bridge.tick()
    obs = bridge.observe(0)
    assert obs.stockpile["wood"] == 300
    assert obs.stockpile["gold"] == 100  # sold at start price
    assert obs.prices["wood"] == 95  # drifted down, same step as the kernel


def test_unsupported_channels_raise(tmp_path: Path) -> None:
    bridge, _ = make_pair(tmp_path)
    with pytest.raises(DeBridgeError, match="relic"):
        bridge.relic_op(0, "grab", "r1")
    with pytest.raises(DeBridgeError, match="movement"):
        bridge.move_op(0, 1, 0)


def test_timeout_when_game_never_acks(tmp_path: Path) -> None:
    cmd = tmp_path / "cmd.xsdat"
    state = tmp_path / "state.xsdat"
    game = FakeDeGame(cmd, state, {0: {}})
    game.start()
    bridge = DeSubstrate(
        FileMailbox(send_path=cmd, recv_path=state),
        agent_ids=[0],
        timeout=1.0,
        sleep=lambda _s: None,  # game is frozen: never steps again
        clock=_FakeClock(),
    )
    bridge.connect()
    bridge.taunt(0, 1)
    with pytest.raises(DeBridgeError, match="did not acknowledge"):
        bridge.tick()


def test_observe_before_connect_raises(tmp_path: Path) -> None:
    bridge = DeSubstrate(
        FileMailbox(send_path=tmp_path / "c.xsdat", recv_path=tmp_path / "s.xsdat"),
        agent_ids=[0],
    )
    with pytest.raises(DeBridgeError, match="connect"):
        bridge.observe(0)


def test_supervisor_coordination_over_the_file_bridge(tmp_path: Path) -> None:
    """The Milestone 1 pattern, replayed over the DE mailbox: the follower
    works only after hearing taunt 31 — coordination crosses actual files."""
    bridge, _game = make_pair(tmp_path)

    def leader(obs, memory):
        from wololo.agents.base import MarketAction, TauntAction

        actions = []
        if not memory.get("announced"):
            memory["announced"] = True
            actions.append(TauntAction(31))
        if obs.stockpile["wood"] >= 100:
            actions.append(MarketAction("sell", "wood"))
        return actions

    def follower(obs, memory):
        from wololo.agents.base import MarketAction

        if any(e.sender == 0 and e.taunt == 31 for e in obs.taunts):
            memory["go"] = True
        if memory.get("go") and obs.stockpile["stone"] >= 100:
            return [MarketAction("sell", "stone")]
        return []

    supervisor = Supervisor(
        bridge,
        [
            AgentSpec(0, lambda: FakeLlm(0, leader)),
            AgentSpec(1, lambda: FakeLlm(1, follower)),
        ],
    )
    for _ in range(4):
        supervisor.run_tick()

    gold_0 = bridge.observe(0).stockpile["gold"]
    gold_1 = bridge.observe(1).stockpile["gold"]
    assert gold_0 > 0 and gold_1 > 0
    # The follower only started after hearing taunt 31 (one tick later), so
    # within 4 ticks it has sold one lot fewer than the leader.
    assert gold_0 > gold_1
    assert supervisor.restarts == {0: 0, 1: 0}
