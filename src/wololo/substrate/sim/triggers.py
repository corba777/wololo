"""Trigger engine — condition → effect rules.

Game analogy: AoE II scenario-editor triggers ("if player 1 owns 3 relics,
declare victory"), optionally looping.  CS meaning: the kernel's internal
event bus / rule engine, evaluated once at the end of every tick.  Used by
the kernel and scenarios — never by agents directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class Trigger[C]:
    """One rule: when ``condition(ctx)`` holds, run ``effect(ctx)``.

    Non-looping triggers deactivate after firing once; looping triggers fire
    on every tick whose condition holds.
    """

    name: str
    condition: Callable[[C], bool]
    effect: Callable[[C], None]
    loop: bool = False
    active: bool = True


class TriggerEngine[C]:
    """Evaluates triggers in registration order, once per tick."""

    def __init__(self) -> None:
        self._triggers: list[Trigger[C]] = []

    def add(self, trigger: Trigger[C]) -> None:
        self._triggers.append(trigger)

    def evaluate(self, ctx: C) -> list[str]:
        """Run all active triggers whose condition holds; return fired names."""
        fired: list[str] = []
        for trigger in self._triggers:
            if trigger.active and trigger.condition(ctx):
                trigger.effect(ctx)
                fired.append(trigger.name)
                if not trigger.loop:
                    trigger.active = False
        return fired
