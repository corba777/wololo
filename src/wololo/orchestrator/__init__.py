"""Orchestrator — spawns agents, drives the tick loop, defines scenarios."""

from __future__ import annotations

from wololo.orchestrator.scenarios import (
    SCENARIOS,
    AgentSetup,
    Scenario,
    ScenarioResult,
    run_scenario,
)
from wololo.orchestrator.supervisor import AgentSpec, Supervisor

__all__ = [
    "SCENARIOS",
    "AgentSetup",
    "AgentSpec",
    "Scenario",
    "ScenarioResult",
    "Supervisor",
    "run_scenario",
]
