"""Kernel mechanics tests, including the adversarial cases from CLAUDE.md:
relic contention, market race, taunt flood, fog boundary."""

from __future__ import annotations

import pytest

from wololo.substrate.sim.kernel import SimKernel
from wololo.substrate.sim.market import LOT_SIZE, Market
from wololo.substrate.sim.triggers import Trigger


def make_kernel(**kwargs) -> SimKernel:
    kernel = SimKernel(map_size=(10, 10), fog_radius=2, **kwargs)
    kernel.add_agent(0, (1, 1), {"wood": 400, "gold": 500})
    kernel.add_agent(1, (8, 8), {"stone": 400, "gold": 500})
    return kernel


# ---------------------------------------------------------------------------
# Taunts: t+1 visibility, ordering, flood
# ---------------------------------------------------------------------------


def test_taunt_visible_next_tick_to_everyone_including_sender() -> None:
    kernel = make_kernel()
    kernel.taunt(0, 30)
    assert kernel.observe(1).taunts == ()  # not yet delivered
    kernel.tick()
    for agent_id in (0, 1):
        taunts = kernel.observe(agent_id).taunts
        assert [(e.sender, e.taunt) for e in taunts] == [(0, 30)]
    kernel.tick()
    assert kernel.observe(1).taunts == ()  # chat does not replay


def test_taunt_flood_preserves_per_sender_order() -> None:
    """Adversarial: two agents flood the bus in interleaved submission order."""
    kernel = make_kernel()
    for i in range(50):
        kernel.taunt(1, (7 * i) % 105 + 1)
        kernel.taunt(0, (3 * i) % 105 + 1)
    kernel.tick()
    events = kernel.observe(0).taunts
    assert len(events) == 100
    # Stable global order: all of agent 0's taunts (by seq), then agent 1's.
    assert [e.sender for e in events] == [0] * 50 + [1] * 50
    assert [e.taunt for e in events if e.sender == 0] == [(3 * i) % 105 + 1 for i in range(50)]
    assert [e.taunt for e in events if e.sender == 1] == [(7 * i) % 105 + 1 for i in range(50)]


def test_invalid_taunt_rejected_at_submission() -> None:
    kernel = make_kernel()
    for bad in (0, 106, -5):
        with pytest.raises(ValueError):
            kernel.taunt(0, bad)


# ---------------------------------------------------------------------------
# Market: drift, funds checks, race determinism
# ---------------------------------------------------------------------------


def test_market_sell_and_buy_drift_prices() -> None:
    kernel = make_kernel()
    kernel.market_op(0, "sell", "wood")
    kernel.tick()
    obs = kernel.observe(0)
    assert obs.stockpile["wood"] == 300
    assert obs.stockpile["gold"] == 500 + 100  # sold at start price 100
    assert obs.prices["wood"] == 95  # sell pushed the price down

    kernel.market_op(0, "buy", "wood")
    kernel.tick()
    obs = kernel.observe(0)
    assert obs.stockpile["wood"] == 400
    assert obs.stockpile["gold"] == 600 - 95  # bought at the drifted price
    assert obs.prices["wood"] == 100


def test_market_race_is_deterministic_by_agent_id() -> None:
    """Adversarial: both agents sell the same resource in the same tick."""
    kernel = SimKernel(map_size=(10, 10))
    kernel.add_agent(0, (1, 1), {"wood": 200})
    kernel.add_agent(1, (8, 8), {"wood": 200})
    kernel.market_op(1, "sell", "wood")  # submitted first,
    kernel.market_op(0, "sell", "wood")  # but agent 0 still resolves first
    kernel.tick()
    assert kernel.observe(0).stockpile["gold"] == 100  # start price
    assert kernel.observe(1).stockpile["gold"] == 95  # post-drift price


def test_market_insufficient_stock_and_gold_rejected() -> None:
    kernel = SimKernel(map_size=(10, 10))
    kernel.add_agent(0, (1, 1))  # empty stockpile
    kernel.market_op(0, "sell", "wood")
    kernel.market_op(0, "buy", "wood")
    kernel.tick()
    rejections = kernel.observe(0).rejections
    assert {r.op for r in rejections} == {"market_sell", "market_buy"}
    assert kernel.observe(0).stockpile["wood"] == 0


def test_market_price_clamped_at_min() -> None:
    market = Market(start_price=30, step=20, min_price=20)
    market.sell("wood")
    assert market.price("wood") == 20
    market.sell("wood")
    assert market.price("wood") == 20


# ---------------------------------------------------------------------------
# Relics: locks, contention, fog gating
# ---------------------------------------------------------------------------


def test_relic_contention_lowest_id_wins_loser_rejected() -> None:
    """Adversarial: contested grab in one tick resolves by agent id."""
    kernel = SimKernel(map_size=(10, 10), fog_radius=10)  # whole map visible
    kernel.add_agent(0, (1, 1))
    kernel.add_agent(1, (8, 8))
    kernel.add_relic("r1", (5, 5))
    kernel.relic_op(1, "grab", "r1")  # submitted first, still loses
    kernel.relic_op(0, "grab", "r1")
    kernel.tick()
    assert kernel.relics.get("r1").owner == 0
    assert kernel.observe(0).rejections == ()
    (rej,) = kernel.observe(1).rejections
    assert rej.op == "relic_grab" and "agent 0" in rej.detail


def test_relic_release_then_regrab() -> None:
    kernel = SimKernel(map_size=(10, 10), fog_radius=10)
    kernel.add_agent(0, (1, 1))
    kernel.add_agent(1, (8, 8))
    kernel.add_relic("r1", (5, 5))
    kernel.relic_op(0, "grab", "r1")
    kernel.tick()
    # Owner releases and the other agent grabs in the same tick.
    kernel.relic_op(0, "release", "r1")
    kernel.relic_op(1, "grab", "r1")
    kernel.tick()
    assert kernel.relics.get("r1").owner == 1


def test_relic_release_not_owner_rejected() -> None:
    kernel = SimKernel(map_size=(10, 10), fog_radius=10)
    kernel.add_agent(0, (1, 1))
    kernel.add_agent(1, (8, 8))
    kernel.add_relic("r1", (5, 5))
    kernel.relic_op(0, "grab", "r1")
    kernel.tick()
    kernel.relic_op(1, "release", "r1")
    kernel.tick()
    assert kernel.relics.get("r1").owner == 0
    (rej,) = kernel.observe(1).rejections
    assert rej.op == "relic_release"


def test_relic_unknown_id_rejected() -> None:
    kernel = SimKernel(map_size=(10, 10))
    kernel.add_agent(0, (1, 1))
    kernel.relic_op(0, "grab", "ghost")
    kernel.tick()
    (rej,) = kernel.observe(0).rejections
    assert "no such relic" in rej.detail


# ---------------------------------------------------------------------------
# Fog of war: boundary, exploration, relic visibility gating
# ---------------------------------------------------------------------------


def test_fog_boundary_relic_hidden_until_explored() -> None:
    """Adversarial: relic exactly one tile beyond the visibility radius."""
    kernel = SimKernel(map_size=(10, 10), fog_radius=2)
    kernel.add_agent(0, (0, 0))
    kernel.add_relic("far", (3, 0))  # radius 2 reveals x<=2: one tile short
    assert kernel.observe(0).relics == ()

    kernel.relic_op(0, "grab", "far")  # grabbing the unseen -> rejected
    kernel.tick()
    (rej,) = kernel.observe(0).rejections
    assert "not explored" in rej.detail

    kernel.move_op(0, 1, 0)  # step to (1,0): reveals x<=3
    kernel.tick()
    obs = kernel.observe(0)
    assert [r.relic_id for r in obs.relics] == ["far"]

    kernel.relic_op(0, "grab", "far")  # explored once -> grab allowed
    kernel.tick()
    assert kernel.relics.get("far").owner == 0


def test_move_clamped_to_map_edges() -> None:
    kernel = SimKernel(map_size=(4, 4))
    kernel.add_agent(0, (0, 0))
    kernel.move_op(0, -5, -5)
    kernel.tick()
    assert kernel.observe(0).pos == (0, 0)
    kernel.move_op(0, 99, 99)
    kernel.tick()
    assert kernel.observe(0).pos == (3, 3)


def test_explored_tiles_accumulate() -> None:
    kernel = SimKernel(map_size=(10, 10), fog_radius=1)
    kernel.add_agent(0, (0, 0))
    before = kernel.observe(0).explored
    kernel.move_op(0, 3, 0)
    kernel.tick()
    after = kernel.observe(0).explored
    assert before < after  # old tiles stay explored, new ones added


# ---------------------------------------------------------------------------
# Triggers and determinism
# ---------------------------------------------------------------------------


def test_trigger_once_vs_loop() -> None:
    kernel = make_kernel()
    counts = {"once": 0, "loop": 0}
    kernel.triggers.add(
        Trigger("once", lambda k: True, lambda k: counts.__setitem__("once", counts["once"] + 1))
    )
    kernel.triggers.add(
        Trigger(
            "loop",
            lambda k: True,
            lambda k: counts.__setitem__("loop", counts["loop"] + 1),
            loop=True,
        )
    )
    for _ in range(3):
        kernel.tick()
    assert counts == {"once": 1, "loop": 3}


def test_same_seed_same_script_identical_run() -> None:
    def run() -> list[tuple]:
        kernel = make_kernel(seed=42)
        log: list[tuple] = []
        for i in range(5):
            kernel.taunt(0, i + 1)
            kernel.market_op(0, "sell", "wood")
            kernel.market_op(1, "sell", "stone")
            kernel.move_op(1, -1, 0)
            kernel.tick()
            obs = kernel.observe(1)
            log.append((obs.tick, obs.pos, tuple(sorted(obs.stockpile.items())), obs.taunts))
        return log

    assert run() == run()


def test_unknown_agent_fails_fast() -> None:
    kernel = make_kernel()
    with pytest.raises(KeyError):
        kernel.observe(99)
    with pytest.raises(KeyError):
        kernel.taunt(99, 1)


def test_lot_size_is_market_unit() -> None:
    kernel = make_kernel()
    kernel.market_op(0, "sell", "wood")
    kernel.tick()
    assert kernel.observe(0).stockpile["wood"] == 400 - LOT_SIZE
