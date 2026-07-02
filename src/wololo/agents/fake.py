"""FakeLlm — deterministic scripted agent for tests (Nexus pattern).

Game analogy: a scripted AI player following a build order.  CS meaning: a
stand-in for the LLM agent with fully deterministic decisions, so kernel and
protocol tests never touch the network.  The policy gets a persistent
``memory`` dict which is lost on respawn — exactly like a real agent losing
its conversation state when the supervisor restarts it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from wololo.agents.base import Action, Agent
from wololo.substrate.interface import Observation

Policy = Callable[[Observation, dict[str, Any]], list[Action]]


class FakeLlm(Agent):
    """Deterministic agent driven by a policy function with private memory."""

    def __init__(self, agent_id: int, policy: Policy) -> None:
        super().__init__(agent_id)
        self._policy = policy
        self._memory: dict[str, Any] = {}

    def act(self, observation: Observation) -> list[Action]:
        return self._policy(observation, self._memory)

    @classmethod
    def from_script(cls, agent_id: int, script: Sequence[list[Action]]) -> FakeLlm:
        """Agent that replays a fixed per-tick action script, then idles."""
        steps = [list(step) for step in script]

        def policy(_obs: Observation, memory: dict[str, Any]) -> list[Action]:
            i = memory.get("step", 0)
            memory["step"] = i + 1
            return steps[i] if i < len(steps) else []

        return cls(agent_id, policy)
