"""LlmAgent tests — all with stub clients, no network."""

from __future__ import annotations

import pytest

from wololo.agents.base import MarketAction, MoveAction, RelicAction, TauntAction
from wololo.agents.llm import (
    HISTORY_LIMIT,
    LlmAgent,
    parse_actions,
    render_observation,
)
from wololo.substrate.interface import Observation, Rejection, RelicView, TauntEvent


def make_observation(**overrides) -> Observation:
    defaults = dict(
        tick=3,
        agent_id=1,
        pos=(6, 6),
        stockpile={"food": 0, "wood": 0, "stone": 300, "gold": 100},
        prices={"food": 100, "wood": 90, "stone": 95},
        taunts=(),
        relics=(),
        rejections=(),
        explored=frozenset({(6, 6)}),
    )
    defaults.update(overrides)
    return Observation(**defaults)


class ScriptedClient:
    """Returns canned replies in order; records every request it gets."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.requests: list[list[dict[str, str]]] = []

    def complete(self, *, system: str, messages: list[dict[str, str]]) -> str:
        assert system  # the role prompt must always be present
        self.requests.append([dict(m) for m in messages])
        return self._replies.pop(0)


# ---------------------------------------------------------------------------
# parse_actions
# ---------------------------------------------------------------------------


def test_parse_all_action_types() -> None:
    reply = """[
        {"type": "taunt", "taunt": 30},
        {"type": "market", "op": "sell", "resource": "stone"},
        {"type": "relic", "op": "grab", "relic_id": "r1"},
        {"type": "move", "dx": -1, "dy": 2}
    ]"""
    assert parse_actions(reply) == [
        TauntAction(30),
        MarketAction("sell", "stone"),
        RelicAction("grab", "r1"),
        MoveAction(-1, 2),
    ]


def test_parse_tolerates_prose_and_fences() -> None:
    reply = 'Sure! Here is my move:\n```json\n[{"type": "taunt", "taunt": 11}]\n```\nGood luck!'
    assert parse_actions(reply) == [TauntAction(11)]


def test_parse_empty_array_is_idle() -> None:
    assert parse_actions("[]") == []


@pytest.mark.parametrize(
    "reply",
    [
        "I attack the wolf.",  # no array at all
        '[{"type": "wololo"}]',  # unknown action type
        '[{"type": "taunt", "taunt": "thirty"}]',  # non-int taunt
        '[{"type": "taunt", "taunt": true}]',  # bool is not an int
        '["just a string"]',  # non-object item
        '[{"type": "move", "dx": 1}]',  # missing field
    ],
)
def test_parse_garbage_raises(reply: str) -> None:
    with pytest.raises((ValueError, KeyError)):
        parse_actions(reply)


# ---------------------------------------------------------------------------
# render_observation
# ---------------------------------------------------------------------------


def test_render_includes_key_facts() -> None:
    obs = make_observation(
        taunts=(
            TauntEvent(tick=3, sender=0, seq=0, taunt=2),
            TauntEvent(tick=3, sender=0, seq=1, taunt=105),
        ),
        relics=(RelicView("r1", (5, 5), owner=0),),
        rejections=(Rejection(3, "market_sell", "stone: need 100 stone"),),
    )
    text = render_observation(obs)
    assert "tick 3" in text
    assert "you are agent 1" in text
    assert "gold=100" in text and "stone=300" in text
    assert "wood=90" in text
    assert "from 0: 2 105" in text
    assert "r1 at (5, 5) held by agent 0" in text
    assert "rejected: market_sell" in text


def test_render_no_taunts_is_explicit() -> None:
    assert "taunts heard: none" in render_observation(make_observation())


# ---------------------------------------------------------------------------
# LlmAgent loop: memory, trimming
# ---------------------------------------------------------------------------


def test_agent_keeps_conversation_memory() -> None:
    client = ScriptedClient(['[{"type": "taunt", "taunt": 5}]', "[]"])
    agent = LlmAgent(1, "test role", client)
    agent.act(make_observation(tick=1))
    agent.act(make_observation(tick=2))

    second_request = client.requests[1]
    # user(tick1), assistant(reply1), user(tick2)
    assert [m["role"] for m in second_request] == ["user", "assistant", "user"]
    assert "tick 1" in second_request[0]["content"]
    assert second_request[1]["content"] == '[{"type": "taunt", "taunt": 5}]'
    assert "tick 2" in second_request[2]["content"]


def test_history_is_trimmed_and_starts_with_user() -> None:
    ticks = HISTORY_LIMIT  # enough exchanges to overflow the window
    client = ScriptedClient(["[]"] * ticks)
    agent = LlmAgent(0, "test role", client)
    for i in range(ticks):
        agent.act(make_observation(tick=i + 1))
    last_request = client.requests[-1]
    assert len(last_request) <= HISTORY_LIMIT
    assert last_request[0]["role"] == "user"
    assert last_request[-1]["role"] == "user"


def test_malformed_reply_raises_for_supervisor() -> None:
    client = ScriptedClient(["I refuse to play."])
    agent = LlmAgent(0, "test role", client)
    with pytest.raises(ValueError):
        agent.act(make_observation())
