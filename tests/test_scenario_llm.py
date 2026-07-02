"""llm_gather end-to-end with deterministic stub "models" — no network.

The stubs behave like cooperative LLM players: the leader announces its
claim over taunts and works; the follower stays idle until it hears the
leader, then takes the other resource.  This exercises the full path
prompt rendering → reply parsing → supervisor → kernel.
"""

from __future__ import annotations

from wololo.orchestrator.scenarios import llm_gather, run_scenario
from wololo.substrate.interface import GOLD


class LeaderModel:
    """Claims wood with taunt 31, then sells wood while it lasts."""

    def complete(self, *, system: str, messages: list[dict[str, str]]) -> str:
        prompt = messages[-1]["content"]
        actions = []
        if len(messages) == 1:  # first turn: announce the claim
            actions.append('{"type": "taunt", "taunt": 31}')
        if "wood=0" not in prompt.split("prices:")[0]:
            actions.append('{"type": "market", "op": "sell", "resource": "wood"}')
        return "[" + ", ".join(actions) + "]"


class FollowerModel:
    """Waits until it hears taunt 31, acknowledges, then sells stone."""

    def __init__(self) -> None:
        self._heard = False

    def complete(self, *, system: str, messages: list[dict[str, str]]) -> str:
        prompt = messages[-1]["content"]
        actions = []
        if not self._heard and "from 0: 31" in prompt:
            self._heard = True
            actions.append('{"type": "taunt", "taunt": 32}')
        if self._heard and "stone=0" not in prompt.split("prices:")[0]:
            actions.append('{"type": "market", "op": "sell", "resource": "stone"}')
        return "[" + ", ".join(actions) + "]"


def make_scenario(seed: int = 0):
    models = iter([LeaderModel(), FollowerModel()])
    return llm_gather(seed=seed, client_factory=lambda: next(models))


def test_llm_gather_reaches_goal_with_stub_models() -> None:
    result = run_scenario(make_scenario())
    assert result.reached, f"goal not reached in {result.ticks} ticks"
    total = sum(s.stockpile[GOLD] for s in result.kernel.agent_states.values())
    assert total >= 600
    assert all(s.stockpile[GOLD] > 0 for s in result.kernel.agent_states.values())
    assert result.supervisor.restarts == {0: 0, 1: 0}


def test_llm_gather_negotiation_is_on_the_record() -> None:
    """The whole coordination shows up in the taunt log: 31 then 32."""
    result = run_scenario(make_scenario())
    log = [(e.sender, e.taunt) for e in result.kernel.taunt_log]
    assert (0, 31) in log
    assert (1, 32) in log
    assert log.index((0, 31)) < log.index((1, 32))
