"""Relic front page — newsroom with a distributed publish lock.

Game analogy: one monastery relic is the *front page* — only the agent
garrisoning it may send verified copy to the typesetter; debunked claims
still go straight to the Fake News column without touching the relic.
CS meaning: extends ``newsroom_pipeline`` with a ``relic`` mutex on the
sim kernel so agent↔agent data stays on taunts while *who may publish*
is decided by relic ownership.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from wololo.agents.llm import LlmReply, ToolCall
from wololo.agents.mcp import McpToolProvider
from wololo.agents.tools import ToolLlmAgent
from wololo.orchestrator.newsroom import (
    CHECKER_ROLE,
    FAKE_KIND,
    SAMPLE_CLAIMS,
    VERIFIED_KIND,
    CheckerModel,
    FakeDashboardSession,
    FakeDeskSession,
    NewsroomWorld,
    _done,
    _first_result,
    _reply,
)
from wololo.orchestrator.scenarios import AgentSetup, Scenario
from wololo.substrate.sim.kernel import SimKernel

FRONT_PAGE_RELIC = "front_page"
#: Between the two desks; visible to the journalist at (6, 6) with fog radius 2.
FRONT_PAGE_POS = (5, 5)


def _holds_front_page(observation_text: str, *, agent_id: int) -> bool:
    needle = f"{FRONT_PAGE_RELIC} at {FRONT_PAGE_POS} held by agent {agent_id}"
    return needle in observation_text


@dataclass
class RelicJournalistModel:
    """Newsroom journalist that grabs ``front_page`` before verified publish."""

    _seq: int = 0
    _pending: list[dict[str, Any]] = field(default_factory=list)
    _buffer: list[int] = field(default_factory=list)
    _verified_text: str | None = None
    _waiting_for_relic: bool = False

    def _id(self) -> str:
        self._seq += 1
        return f"rj{self._seq}"

    def _queue_verified(self, text: str) -> LlmReply:
        self._verified_text = text
        self._waiting_for_relic = True
        return _reply(
            ToolCall(self._id(), "relic", {"op": "grab", "relic_id": FRONT_PAGE_RELIC})
        )

    def _publish_verified(self, text: str) -> LlmReply:
        return _reply(
            ToolCall(
                self._id(),
                "dash_publish_news",
                {"headline": f"Confirmed: {text.rstrip('.')}", "body": text},
            ),
            ToolCall(self._id(), "relic", {"op": "release", "relic_id": FRONT_PAGE_RELIC}),
        )

    def _next_publish(self) -> LlmReply:
        message = self._pending.pop(0)
        text = message["text"] or ""
        if message["kind"] == VERIFIED_KIND:
            return self._queue_verified(text)
        return _reply(ToolCall(self._id(), "dash_flag_fake", {"claim": text}))

    def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmReply:
        last = messages[-1]
        if isinstance(last["content"], str):
            heard = re.search(r"from 0: ([\d ]+)", last["content"])
            if heard:
                self._buffer.extend(int(t) for t in heard.group(1).split())
            if self._buffer:
                return _reply(
                    ToolCall(self._id(), "decode_taunts", {"taunts": self._buffer})
                )
            if (
                self._waiting_for_relic
                and self._verified_text
                and _holds_front_page(last["content"], agent_id=1)
            ):
                text = self._verified_text
                self._verified_text = None
                self._waiting_for_relic = False
                return self._publish_verified(text)
            if self._waiting_for_relic and self._verified_text:
                return _reply(
                    ToolCall(
                        self._id(), "relic", {"op": "grab", "relic_id": FRONT_PAGE_RELIC}
                    )
                )
            if self._pending:
                return self._next_publish()
            return _done()
        payload = _first_result(last)
        if payload and "messages" in payload:
            self._pending.extend(
                m for m in payload["messages"] if m["kind"] in (VERIFIED_KIND, FAKE_KIND)
            )
            self._buffer = list(payload.get("remainder", []))
        if self._pending:
            head = self._pending[0]
            if head["kind"] == FAKE_KIND or not self._waiting_for_relic:
                return self._next_publish()
        return _done()


JOURNALIST_ROLE_RELIC = """\
You are the journalist of a two-agent newsroom. Listen for taunts from
agent 0 and decode them with decode_taunts. Each decoded message carries a
claim as text. If its kind is {verified}, the claim is fact-checked TRUE:
you must **grab relic `{relic}`**, then write a short punchy news story
about that exact claim text and publish with dash_publish_news (headline +
body), then **release relic `{relic}`**. If its kind is {fake}, the claim
is FALSE: call dash_flag_fake with the claim text verbatim — no relic
needed for fake news.

CRITICAL rules:
- Only act after decode_taunts returns a message with non-empty text.
- Never publish verified news without holding `{relic}` (check the
  observation: "held by agent <you>").
- If taunts heard is none, or decode returns no complete messages, do
  nothing — never invent stories about "no claims" or the newsroom itself.
- Handle each decoded claim exactly once; ignore duplicates from history.
- Only decode taunts from the CURRENT observation. Never send taunts."""


def build_relic_front_page_pipeline(
    seed: int = 0,
    client_factory: Callable[[], Any] | None = None,
    *,
    desk_session: FakeDeskSession | None = None,
    dashboard_session: FakeDashboardSession | None = None,
    max_ticks: int = 20,
) -> NewsroomWorld:
    """Newsroom plus ``front_page`` relic — verified stories need the lock."""
    desk = desk_session if desk_session is not None else FakeDeskSession(list(SAMPLE_CLAIMS))
    dashboard = (
        dashboard_session if dashboard_session is not None else FakeDashboardSession()
    )
    desk_tools = McpToolProvider(desk, prefix="desk_")
    dash_tools = McpToolProvider(dashboard, prefix="dash_")

    checker_client: Callable[[], Any] = (
        client_factory if client_factory is not None else CheckerModel
    )
    journalist_client: Callable[[], Any] = (
        client_factory if client_factory is not None else RelicJournalistModel
    )

    journalist_role = JOURNALIST_ROLE_RELIC.format(
        verified=VERIFIED_KIND, fake=FAKE_KIND, relic=FRONT_PAGE_RELIC
    )

    def goal(_: SimKernel) -> bool:
        return bool(getattr(dashboard, "news", ())) and bool(getattr(dashboard, "fakes", ()))

    return NewsroomWorld(
        scenario=Scenario(
            name="relic_front_page",
            map_size=(8, 8),
            seed=seed,
            max_ticks=max_ticks,
            relics=((FRONT_PAGE_RELIC, FRONT_PAGE_POS),),
            agents=(
                AgentSetup(
                    agent_id=0,
                    pos=(1, 1),
                    stockpile={},
                    factory=lambda: ToolLlmAgent(
                        0,
                        CHECKER_ROLE.format(verified=VERIFIED_KIND, fake=FAKE_KIND),
                        checker_client(),
                        providers=[desk_tools],
                    ),
                ),
                AgentSetup(
                    agent_id=1,
                    pos=(6, 6),
                    stockpile={},
                    factory=lambda: ToolLlmAgent(
                        1,
                        journalist_role,
                        journalist_client(),
                        providers=[dash_tools],
                    ),
                ),
            ),
            goal=goal,
        ),
        desk=desk,
        dashboard=dashboard,
    )
