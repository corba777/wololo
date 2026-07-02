"""Shipping pipeline — real-world tools demo over the taunt channel.

Game analogy: two players with private advisors: a *mail watcher* whose
clerk reads the inbox, and a *sheet scribe* whose bookkeeper holds the
ledger.  When an Amazon shipping notice arrives, the watcher shouts the
parsed facts across the map as a codec message; the scribe hears it,
decodes it, and writes the row.  CS meaning: an integration demo where
external I/O happens through per-agent (MCP-style) tools while ALL
agent↔agent traffic still crosses the 105-taunt substrate — the control
*and* data plane is the codec.

Offline by default: in-memory email/sheet sessions and deterministic
stand-in models, so tests and the CLI run with no network.  For a real
deployment, pass ``client_factory=AnthropicClient`` and real MCP sessions
(Gmail / Google Sheets servers) — the role prompts below double as the
instructions a real model follows.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from wololo.agents.llm import LlmReply, ToolCall
from wololo.agents.mcp import McpSession, McpToolProvider
from wololo.agents.tools import ToolLlmAgent
from wololo.orchestrator.scenarios import AgentSetup, Scenario
from wololo.substrate.sim.kernel import SimKernel

#: Codec message kind for "a shipment is on its way": args = [order, tracking].
SHIPPING_KIND = 7

SAMPLE_EMAILS: tuple[dict[str, str], ...] = (
    {
        "from": "newsletter@example.com",
        "subject": "Weekly digest",
        "body": "Ten hot takes about castles.",
    },
    {
        "from": "ship-confirm@amazon.com",
        "subject": "Your package has shipped",
        "body": "Order #123-4567890 has shipped. Tracking 9400123456789.",
    },
    {
        "from": "noreply@github.com",
        "subject": "PR merged",
        "body": "Your pull request was merged.",
    },
)


class FakeInboxSession:
    """In-memory Gmail-shaped MCP session: one new email per poll."""

    def __init__(self, emails: list[dict[str, str]]) -> None:
        self._queue = list(emails)

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "check_inbox",
                "description": "Return emails that arrived since the last check.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        assert name == "check_inbox"
        emails = [self._queue.pop(0)] if self._queue else []
        return json.dumps({"emails": emails})


class FakeSheetSession:
    """In-memory Google-Sheets-shaped MCP session: append-only rows."""

    def __init__(self) -> None:
        self.rows: list[list[str]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "append_row",
                "description": "Append one row of string cells to the spreadsheet.",
                "input_schema": {
                    "type": "object",
                    "properties": {"values": {"type": "array", "items": {"type": "string"}}},
                    "required": ["values"],
                },
            }
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        assert name == "append_row"
        self.rows.append([str(v) for v in arguments["values"]])
        return json.dumps({"appended": len(self.rows)})


# ---------------------------------------------------------------------------
# Deterministic stand-in models (offline mode); real runs use AnthropicClient
# ---------------------------------------------------------------------------


def _reply(*calls: ToolCall) -> LlmReply:
    raw = [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
    return LlmReply(raw_content=raw, tool_calls=calls)


def _done() -> LlmReply:
    return LlmReply(raw_content=[{"type": "text", "text": "done"}])


def _first_result(message: dict[str, Any]) -> dict[str, Any] | None:
    """Parse the first tool_result of a turn; None for plain-text acks."""
    try:
        return json.loads(message["content"][0]["content"])
    except (json.JSONDecodeError, TypeError):
        return None


@dataclass
class WatcherModel:
    """Polls the inbox; on an Amazon shipping email, encodes and shouts it."""

    _seq: int = 0

    def _id(self) -> str:
        self._seq += 1
        return f"w{self._seq}"

    def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmReply:
        last = messages[-1]
        if isinstance(last["content"], str):  # new tick: poll the inbox
            return _reply(ToolCall(self._id(), "email_check_inbox", {}))
        payload = _first_result(last)
        if payload is None:  # acks for queued taunts: done for this tick
            return _done()
        if "emails" in payload:
            for email in payload["emails"]:
                if "amazon" in email["from"] and "shipped" in email["body"].lower():
                    order = int(re.sub(r"\D", "", re.search(r"#([\d-]+)", email["body"])[1]))
                    tracking = int(re.search(r"Tracking (\d+)", email["body"])[1])
                    return _reply(
                        ToolCall(
                            self._id(),
                            "encode_message",
                            {"kind": SHIPPING_KIND, "args": [order, tracking]},
                        )
                    )
            return _done()
        if "taunts" in payload:  # encoded frame ready: shout it
            return _reply(
                *(
                    ToolCall(self._id(), "send_taunt", {"taunt": taunt})
                    for taunt in payload["taunts"]
                )
            )
        return _done()


@dataclass
class ScribeModel:
    """Decodes taunts from the watcher; writes shipping rows to the sheet."""

    _seq: int = 0

    def _id(self) -> str:
        self._seq += 1
        return f"s{self._seq}"

    def complete_tools(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmReply:
        last = messages[-1]
        if isinstance(last["content"], str):  # new tick: anything from agent 0?
            heard = re.search(r"from 0: ([\d ]+)", last["content"])
            if heard:
                taunts = [int(t) for t in heard.group(1).split()]
                return _reply(ToolCall(self._id(), "decode_taunts", {"taunts": taunts}))
            return _done()
        payload = _first_result(last)
        if payload:
            for message in payload.get("messages", []):
                if message["kind"] == SHIPPING_KIND:
                    order, tracking = message["args"]
                    return _reply(
                        ToolCall(
                            self._id(),
                            "sheets_append_row",
                            {"values": [f"order {order}", f"tracking {tracking}"]},
                        )
                    )
        return _done()


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------

_WATCHER_ROLE = """\
You are the mail watcher. Each tick, call email_check_inbox once. Ignore
everything except shipping confirmations from Amazon. When one arrives,
extract the order number and tracking number as integers (digits only),
encode them with encode_message as kind {kind} with args [order, tracking],
and shout the resulting taunts with send_taunt. Do not use the market."""

_SCRIBE_ROLE = """\
You are the sheet scribe. Listen for taunts from agent 0. Decode them with
decode_taunts. When a message of kind {kind} arrives, its args are
[order, tracking]: append a row to the spreadsheet with sheets_append_row,
values ["order <order>", "tracking <tracking>"]. Do nothing else."""


@dataclass(slots=True)
class ShippingWorld:
    """A built pipeline: the scenario plus handles to its fake sessions."""

    scenario: Scenario
    inbox: FakeInboxSession
    sheet: FakeSheetSession


def build_shipping_pipeline(
    seed: int = 0,
    client_factory: Callable[[], Any] | None = None,
    *,
    email_session: McpSession | None = None,
    sheet_session: McpSession | None = None,
) -> ShippingWorld:
    """Assemble the pipeline; every argument can be swapped for the real thing."""
    inbox = email_session if email_session is not None else FakeInboxSession(list(SAMPLE_EMAILS))
    sheet = sheet_session if sheet_session is not None else FakeSheetSession()
    email_tools = McpToolProvider(inbox, prefix="email_")
    sheet_tools = McpToolProvider(sheet, prefix="sheets_")

    watcher_client: Callable[[], Any] = client_factory or WatcherModel
    scribe_client: Callable[[], Any] = client_factory or ScribeModel

    def goal(_: SimKernel) -> bool:
        return bool(getattr(sheet, "rows", ()))

    return ShippingWorld(
        scenario=Scenario(
            name="shipping_pipeline",
            map_size=(8, 8),
            seed=seed,
            max_ticks=12,
            agents=(
                AgentSetup(
                    agent_id=0,
                    pos=(1, 1),
                    stockpile={},
                    factory=lambda: ToolLlmAgent(
                        0,
                        _WATCHER_ROLE.format(kind=SHIPPING_KIND),
                        watcher_client(),
                        providers=[email_tools],
                    ),
                ),
                AgentSetup(
                    agent_id=1,
                    pos=(6, 6),
                    stockpile={},
                    factory=lambda: ToolLlmAgent(
                        1,
                        _SCRIBE_ROLE.format(kind=SHIPPING_KIND),
                        scribe_client(),
                        providers=[sheet_tools],
                    ),
                ),
            ),
            goal=goal,
        ),
        inbox=inbox,
        sheet=sheet,
    )
