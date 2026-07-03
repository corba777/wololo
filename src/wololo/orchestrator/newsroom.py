"""Newsroom pipeline — fact-check desk to dashboard over the taunt channel.

Game analogy: two players with private advisors: a *fact checker* whose
clerk brings reader-submitted claims, and a *journalist* whose typesetter
owns the dashboard.  The checker reads each claim, decides whether it is
true, and shouts the claim text across the map as a codec message whose
kind is the verdict; the journalist hears it, decodes it, and either writes
the story or pins the claim to the Fake News board.  CS meaning: same
pattern as ``shipping.py`` — external I/O through per-agent tools, ALL
agent↔agent traffic (the claim text *and* the verdict flag) across the
105-taunt substrate.

Offline by default: in-memory desk/dashboard sessions and deterministic
stand-in models, so tests and the CLI run with no network.  The live
Streamlit front end and the Ollama-backed models plug into the same seams
(see ``scripts/newsroom_demo.py`` and ``apps/newsroom_app.py``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wololo.agents.base import Action, TauntAction
from wololo.agents.llm import LlmReply, ToolCall
from wololo.agents.mcp import McpSession, McpToolProvider
from wololo.agents.tools import ToolLlmAgent, ToolProvider
from wololo.orchestrator.scenarios import AgentSetup, Scenario
from wololo.substrate.interface import Observation
from wololo.substrate.sim.kernel import SimKernel

#: Codec message kinds: the verdict is the kind, the claim text is the args.
VERIFIED_KIND = 11
FAKE_KIND = 12

#: Agent 1 status taunts — echoed in game chat (see xs/wololo.xs).
JOURNALIST_ACK_HEARD = 101
JOURNALIST_ACK_RECEIVED = 102
JOURNALIST_ACK_PUBLISH = 103
JOURNALIST_ACK_FAKE = 104

SAMPLE_CLAIMS: tuple[str, ...] = (
    "Water boils at 100 degrees Celsius at sea level.",
    "The Great Wall of China is visible from the Moon.",
)

#: The offline fact checker's whole knowledge base.
KNOWN_TRUE: frozenset[str] = frozenset({SAMPLE_CLAIMS[0]})


class FakeDeskSession:
    """In-memory news-desk MCP session: one submitted claim per poll."""

    def __init__(self, claims: list[str]) -> None:
        self._queue = list(claims)

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "fetch_claims",
                "description": "Return reader-submitted claims since the last check.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        assert name == "fetch_claims"
        claims = [self._queue.pop(0)] if self._queue else []
        return json.dumps({"claims": claims})


class FakeDashboardSession:
    """In-memory dashboard MCP session: verified stories and a fake-news column."""

    def __init__(self) -> None:
        self.news: list[dict[str, str]] = []
        self.fakes: list[str] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "publish_news",
                "description": "Publish one verified story to the dashboard.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "headline": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["headline", "body"],
                },
            },
            {
                "name": "flag_fake",
                "description": "Pin one debunked claim to the Fake News column.",
                "input_schema": {
                    "type": "object",
                    "properties": {"claim": {"type": "string"}},
                    "required": ["claim"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "publish_news":
            self.news.append({"headline": arguments["headline"], "body": arguments["body"]})
            return json.dumps({"published": len(self.news)})
        assert name == "flag_fake"
        self.fakes.append(arguments["claim"])
        return json.dumps({"flagged": len(self.fakes)})


# ---------------------------------------------------------------------------
# Deterministic stand-in models (offline mode); live runs use OllamaClient
# ---------------------------------------------------------------------------


def _reply(*calls: ToolCall) -> LlmReply:
    raw = [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
    return LlmReply(raw_content=raw, tool_calls=calls)


def _done() -> LlmReply:
    return LlmReply(raw_content=[{"type": "text", "text": "done"}])


def _first_result(message: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return json.loads(message["content"][0]["content"])
    except (json.JSONDecodeError, TypeError):
        return None


@dataclass
class CheckerModel:
    """Polls the desk; shouts each claim with its verdict as the message kind."""

    known_true: frozenset[str] = KNOWN_TRUE
    _seq: int = 0
    _pending: list[str] = field(default_factory=list)

    def _id(self) -> str:
        self._seq += 1
        return f"c{self._seq}"

    def _next_encode(self) -> LlmReply:
        claim = self._pending.pop(0)
        kind = VERIFIED_KIND if claim in self.known_true else FAKE_KIND
        return _reply(ToolCall(self._id(), "encode_text_message", {"kind": kind, "text": claim}))

    def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmReply:
        last = messages[-1]
        if isinstance(last["content"], str):  # new tick: poll the desk
            return _reply(ToolCall(self._id(), "desk_fetch_claims", {}))
        payload = _first_result(last)
        if payload is not None and "claims" in payload:
            self._pending.extend(payload["claims"])
        if payload is not None and "taunts" in payload:  # encoded frame: shout it
            return _reply(ToolCall(self._id(), "send_taunts", {"taunts": payload["taunts"]}))
        if self._pending:
            return self._next_encode()
        return _done()


@dataclass
class JournalistModel:
    """Decodes taunts from the checker; publishes stories or flags fakes.

    Keeps an undecoded-taunt buffer across ticks: over the DE bridge a long
    claim can span epochs (the XS script caps records per frame), so a
    message may arrive in parts.
    """

    _seq: int = 0
    _pending: list[dict[str, Any]] = field(default_factory=list)
    _buffer: list[int] = field(default_factory=list)

    def _id(self) -> str:
        self._seq += 1
        return f"j{self._seq}"

    def _next_publish(self) -> LlmReply:
        message = self._pending.pop(0)
        text = message["text"] or ""
        if message["kind"] == VERIFIED_KIND:
            return _reply(
                ToolCall(
                    self._id(),
                    "dash_publish_news",
                    {"headline": f"Confirmed: {text.rstrip('.')}", "body": text},
                )
            )
        return _reply(ToolCall(self._id(), "dash_flag_fake", {"claim": text}))

    def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmReply:
        last = messages[-1]
        if isinstance(last["content"], str):  # new tick: anything from agent 0?
            heard = re.search(r"from 0: ([\d ]+)", last["content"])
            if heard:
                self._buffer.extend(int(t) for t in heard.group(1).split())
            if self._buffer:
                return _reply(ToolCall(self._id(), "decode_taunts", {"taunts": self._buffer}))
            return _done()
        payload = _first_result(last)
        if payload and "messages" in payload:
            self._pending.extend(
                m for m in payload["messages"] if m["kind"] in (VERIFIED_KIND, FAKE_KIND)
            )
            self._buffer = list(payload.get("remainder", []))
        if self._pending:
            return self._next_publish()
        return _done()


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

CHECKER_ROLE = """\
You are the fact checker of a two-agent newsroom. Each tick, call
desk_fetch_claims once. For every claim, decide whether it is factually
TRUE or FALSE using your own knowledge. Then transmit the claim text to
the journalist (agent 1): call encode_text_message with kind {verified}
if the claim is true or kind {fake} if it is false, and shout the
resulting taunt numbers with send_taunts. The taunt channel is your ONLY
link to the journalist. Do not use the market."""

JOURNALIST_ROLE = """\
You are the journalist of a two-agent newsroom. Listen for taunts from
agent 0 and decode them with decode_taunts. Each decoded message carries a
claim as text. If its kind is {verified}, the claim is fact-checked TRUE:
write a short punchy news story *about that exact claim text* and publish
with dash_publish_news (headline + body). If its kind is {fake}, the claim
is FALSE: call dash_flag_fake with the claim text verbatim.

CRITICAL rules:
- Only act after decode_taunts returns a message with non-empty text.
- If taunts heard is none, or decode returns no complete messages, do
  nothing — never invent stories about "no claims" or the newsroom itself.
- The headline and body must refer to the decoded claim text, not meta
  commentary about the pipeline.
- Handle each decoded claim exactly once; ignore duplicates from history.
- Only decode taunts from the CURRENT observation. Never send taunts."""


def _journalist_tool_callback(
    directory: Path, ack_taunts: list[int]
) -> Callable[[str, dict[str, Any], str], None]:
    """Log decode steps and queue in-game ack taunts for the Streamlit panel."""

    def callback(name: str, payload: dict[str, Any], result: str) -> None:
        from wololo.orchestrator.newsstore import append_log

        if name == "decode_taunts":
            taunts = payload.get("taunts") or []
            append_log(directory, "journalist_decoding", taunts=len(taunts))
            try:
                data = json.loads(result)
            except json.JSONDecodeError:
                return
            for message in data.get("messages", []):
                kind = message.get("kind")
                if kind not in (VERIFIED_KIND, FAKE_KIND):
                    continue
                text = message.get("text") or ""
                verdict = "verified" if kind == VERIFIED_KIND else "fake"
                append_log(directory, "journalist_received", text=text, verdict=verdict)
                ack_taunts.append(JOURNALIST_ACK_RECEIVED)
            return
        if name == "dash_publish_news":
            ack_taunts.append(JOURNALIST_ACK_PUBLISH)
        elif name == "dash_flag_fake":
            ack_taunts.append(JOURNALIST_ACK_FAKE)

    return callback


class LoggingJournalistAgent(ToolLlmAgent):
    """Journalist with activity lines for the newsroom UI."""

    def __init__(
        self,
        agent_id: int,
        role: str,
        client: Any,
        providers: Sequence[ToolProvider],
        *,
        log_directory: Path,
    ) -> None:
        self._log_directory = log_directory
        self._ack_taunts: list[int] = []
        super().__init__(
            agent_id,
            role,
            client,
            providers,
            tool_callback=_journalist_tool_callback(log_directory, self._ack_taunts),
        )

    def act(self, observation: Observation) -> list[Action]:
        from wololo.orchestrator.newsstore import append_log

        self._ack_taunts.clear()
        heard = [event for event in observation.taunts if event.sender == 0]
        if heard:
            append_log(
                self._log_directory,
                "journalist_heard",
                count=len(heard),
                epoch=observation.tick,
            )
            self._ack_taunts.append(JOURNALIST_ACK_HEARD)
        actions = super().act(observation)
        return actions + [TauntAction(t) for t in self._ack_taunts]


@dataclass(slots=True)
class NewsroomWorld:
    """A built pipeline: the scenario plus handles to its sessions."""

    scenario: Scenario
    desk: McpSession
    dashboard: McpSession


def build_newsroom_pipeline(
    seed: int = 0,
    client_factory: Callable[[], Any] | None = None,
    *,
    desk_session: McpSession | None = None,
    dashboard_session: McpSession | None = None,
    log_directory: Path | None = None,
    max_ticks: int = 16,
) -> NewsroomWorld:
    """Assemble the pipeline; every argument can be swapped for the real thing."""
    desk = desk_session if desk_session is not None else FakeDeskSession(list(SAMPLE_CLAIMS))
    dashboard = dashboard_session if dashboard_session is not None else FakeDashboardSession()
    desk_tools = McpToolProvider(desk, prefix="desk_")
    dash_tools = McpToolProvider(dashboard, prefix="dash_")

    checker_client: Callable[[], Any] = client_factory or CheckerModel
    journalist_client: Callable[[], Any] = client_factory or JournalistModel

    def journalist_factory() -> ToolLlmAgent:
        agent = LoggingJournalistAgent if log_directory is not None else ToolLlmAgent
        kwargs: dict[str, Any] = {}
        if log_directory is not None:
            kwargs["log_directory"] = log_directory
        return agent(
            1,
            JOURNALIST_ROLE.format(verified=VERIFIED_KIND, fake=FAKE_KIND),
            journalist_client(),
            [dash_tools],
            **kwargs,
        )

    def goal(_: SimKernel) -> bool:
        return bool(getattr(dashboard, "news", ())) and bool(getattr(dashboard, "fakes", ()))

    return NewsroomWorld(
        scenario=Scenario(
            name="newsroom_pipeline",
            map_size=(8, 8),
            seed=seed,
            max_ticks=max_ticks,
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
                    factory=journalist_factory,
                ),
            ),
            goal=goal,
        ),
        desk=desk,
        dashboard=dashboard,
    )
