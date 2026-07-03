"""OllamaClient tests — transport injected, no network."""

from __future__ import annotations

from typing import Any

import pytest

from wololo.agents.llm import LlmAgent
from wololo.agents.ollama import OllamaClient, OllamaError
from wololo.substrate.interface import Observation


def make_obs() -> Observation:
    return Observation(
        tick=1,
        agent_id=0,
        pos=(0, 0),
        stockpile={"wood": 400, "gold": 0},
        prices={"food": 100, "wood": 100, "stone": 100},
        taunts=(),
        relics=(),
        rejections=(),
        explored=frozenset(),
    )


def test_complete_builds_chat_payload_and_returns_content() -> None:
    seen: dict[str, Any] = {}

    def transport(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen["url"] = url
        seen["payload"] = payload
        return {"message": {"role": "assistant", "content": '[{"type": "taunt", "taunt": 31}]'}}

    client = OllamaClient("test-model", base_url="http://box:11434", transport=transport)
    reply = client.complete(system="sys", messages=[{"role": "user", "content": "obs"}])

    assert reply == '[{"type": "taunt", "taunt": 31}]'
    assert seen["url"] == "http://box:11434/api/chat"
    assert seen["payload"]["model"] == "test-model"
    assert seen["payload"]["stream"] is False
    assert seen["payload"]["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["payload"]["messages"][1] == {"role": "user", "content": "obs"}


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("192.168.0.1:11434", "http://192.168.0.1:11434/api/chat"),
        ("192.168.0.1", "http://192.168.0.1:11434/api/chat"),
        ("my-box", "http://my-box:11434/api/chat"),
        ("http://my-box:8080/", "http://my-box:8080/api/chat"),
    ],
)
def test_base_url_shorthand_is_normalized(base_url: str, expected: str) -> None:
    seen: dict[str, str] = {}

    def transport(url: str, _payload: dict[str, Any]) -> dict[str, Any]:
        seen["url"] = url
        return {"message": {"content": "ok"}}

    client = OllamaClient("m", base_url=base_url, transport=transport)
    client.complete(system="s", messages=[])
    assert seen["url"] == expected


def test_malformed_response_raises() -> None:
    client = OllamaClient(transport=lambda _url, _payload: {"oops": True})
    with pytest.raises(OllamaError, match="malformed"):
        client.complete(system="s", messages=[])


def test_llm_agent_runs_on_ollama_client() -> None:
    """OllamaClient satisfies the LlmClient protocol end to end."""

    def transport(_url: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"message": {"content": '[{"type": "market", "op": "sell", "resource": "wood"}]'}}

    agent = LlmAgent(0, "role", OllamaClient(transport=transport))
    actions = agent.act(make_obs())
    assert len(actions) == 1
