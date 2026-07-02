"""Supervisor tests — let-it-crash respawn and action routing."""

from __future__ import annotations

from typing import Any

from wololo.agents.base import Action, MarketAction, TauntAction
from wololo.agents.fake import FakeLlm
from wololo.orchestrator.supervisor import AgentSpec, Supervisor
from wololo.substrate.interface import Observation
from wololo.substrate.sim.kernel import SimKernel


def make_kernel() -> SimKernel:
    kernel = SimKernel(map_size=(8, 8))
    kernel.add_agent(0, (1, 1), {"wood": 300})
    kernel.add_agent(1, (6, 6))
    return kernel


def test_scripted_agents_drive_the_world() -> None:
    kernel = make_kernel()
    supervisor = Supervisor(
        kernel,
        [
            AgentSpec(
                0,
                lambda: FakeLlm.from_script(
                    0, [[TauntAction(30), MarketAction("sell", "wood")], [TauntAction(11)]]
                ),
            ),
            AgentSpec(1, lambda: FakeLlm.from_script(1, [])),
        ],
    )
    supervisor.run(2)
    assert kernel.observe(0).stockpile["gold"] == 100
    assert [e.taunt for e in kernel.observe(1).taunts] == [11]
    assert supervisor.restarts == {0: 0, 1: 0}


def test_crashing_agent_is_respawned_with_fresh_memory() -> None:
    """Let-it-crash: the crash tick is skipped, memory resets on respawn."""
    kernel = make_kernel()

    def crashy_policy(obs: Observation, memory: dict[str, Any]) -> list[Action]:
        # Fresh memory crashes on its second act; a survivor would go on.
        step = memory.get("step", 0)
        memory["step"] = step + 1
        if step == 1:
            raise RuntimeError("monk dropped the relic")
        return [TauntAction(step + 1)]

    supervisor = Supervisor(
        kernel,
        [
            AgentSpec(0, lambda: FakeLlm(0, crashy_policy)),
            AgentSpec(1, lambda: FakeLlm.from_script(1, [])),
        ],
    )
    heard: list[int] = []
    for _ in range(4):
        supervisor.run_tick()
        heard.extend(e.taunt for e in kernel.observe(1).taunts)

    # Ticks: act(1), crash+respawn, act(1) with fresh memory, crash again.
    assert heard == [1, 1]
    assert supervisor.restarts[0] == 2
    assert supervisor.restarts[1] == 0


def test_stop_condition_halts_run() -> None:
    kernel = make_kernel()
    supervisor = Supervisor(kernel, [AgentSpec(0, lambda: FakeLlm.from_script(0, []))])
    last_tick = supervisor.run(max_ticks=50, stop=lambda: kernel.current_tick >= 3)
    assert last_tick == 3
