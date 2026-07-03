"""Relic front page — verified publish requires the front_page relic lock."""

from __future__ import annotations

from wololo.orchestrator.newsroom import FAKE_KIND, SAMPLE_CLAIMS, VERIFIED_KIND
from wololo.orchestrator.relic_front_page import (
    FRONT_PAGE_RELIC,
    RelicJournalistModel,
    build_relic_front_page_pipeline,
)
from wololo.orchestrator.scenarios import run_scenario

TRUE_CLAIM, FAKE_CLAIM = SAMPLE_CLAIMS


def test_relic_front_page_reaches_dashboard() -> None:
    world = build_relic_front_page_pipeline()
    result = run_scenario(world.scenario)
    assert result.reached, f"goal not met after {result.ticks} ticks"
    assert world.dashboard.news == [
        {"headline": f"Confirmed: {TRUE_CLAIM.rstrip('.')}", "body": TRUE_CLAIM}
    ]
    assert world.dashboard.fakes == [FAKE_CLAIM]
    relic = result.kernel.relics.get(FRONT_PAGE_RELIC)
    assert relic is not None and relic.owner is None


def test_verified_publish_grabs_and_releases_relic() -> None:
    model = RelicJournalistModel()
    obs = (
        "tick 2\nyou are agent 1 at (6, 6)\nstockpile: \nprices: food=100 wood=100 stone=100\n"
        f"taunts heard: none\nrelics visible: {FRONT_PAGE_RELIC} at (5, 5) held by agent 1\n"
        "Reply with a JSON array of actions."
    )
    model._waiting_for_relic = True
    model._verified_text = TRUE_CLAIM
    publish = model.complete_tools(
        system="",
        messages=[{"role": "user", "content": obs}],
        tools=[],
    )
    names = [c.name for c in publish.tool_calls]
    assert names == ["dash_publish_news", "relic"]
    assert publish.tool_calls[1].input == {"op": "release", "relic_id": FRONT_PAGE_RELIC}


def test_fake_claim_skips_relic() -> None:
    model = RelicJournalistModel()
    obs = "tick 1\nyou are agent 1 at (6, 6)\nstockpile: \nprices: \ntaunts heard: none\n"
    verified_payload = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "x",
                "content": '{"messages": [{"kind": '
                + str(FAKE_KIND)
                + ', "text": "'
                + FAKE_CLAIM
                + '"}], "remainder": []}',
            }
        ],
    }
    flag = model.complete_tools(
        system="",
        messages=[{"role": "user", "content": obs}, verified_payload],
        tools=[],
    )
    assert flag.tool_calls[0].name == "dash_flag_fake"
    assert not any(c.name == "relic" for c in flag.tool_calls)
