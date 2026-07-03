"""Channel filters — muzzles for agents on narrow substrates.

Game analogy: a referee standing behind the player who blocks button
presses the current match does not allow.  CS meaning: an ``Agent``
decorator that drops action types the target substrate does not carry
(the DE bridge speaks taunt + market only).  Local models occasionally
emit unsupported actions despite the role prompt; letting them through
would crash the agent (DeBridgeError) and wipe its memory, so demos
filter instead of let-it-crash.
"""

from __future__ import annotations

from wololo.agents.base import Action, Agent, MarketAction, TauntAction
from wololo.substrate.interface import Observation

BRIDGE_ACTIONS: tuple[type, ...] = (TauntAction, MarketAction)


class ChannelFilter(Agent):
    """Pass through only the given action types; report what got dropped."""

    def __init__(self, inner: Agent, allowed: tuple[type, ...] = BRIDGE_ACTIONS) -> None:
        super().__init__(inner.agent_id)
        self._inner = inner
        self._allowed = allowed

    def act(self, observation: Observation) -> list[Action]:
        actions = self._inner.act(observation)
        kept = [a for a in actions if isinstance(a, self._allowed)]
        for dropped in set(map(type, actions)) - set(map(type, kept)):
            print(f"    (agent {self.agent_id}: dropped unsupported {dropped.__name__})")
        return kept
