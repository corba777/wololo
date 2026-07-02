"""MCP bridge — plug external tool servers into tool-using agents.

Game analogy: each player may bring their own advisors into the booth — a
mail clerk, a scribe with a ledger.  An advisor talks only to their own
player; players still talk to *each other* exclusively by shouting taunts.
CS meaning: an adapter exposing an MCP (Model Context Protocol) server's
tools as a ``ToolProvider`` for ``ToolLlmAgent``, giving each agent its own
scoped session (and thus its own credentials — the mail watcher cannot
write spreadsheets, the sheet scribe cannot read mail).

There is no hard dependency on the ``mcp`` SDK and no async: anything with
synchronous ``list_tools()`` / ``call_tool()`` satisfies ``McpSession``.
The official async client can be wrapped in a blocking portal by the
caller; tests and offline demos use in-memory fake sessions.
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class McpSession(Protocol):
    """Synchronous view of one MCP server connection.

    ``list_tools`` returns Anthropic-shaped descriptors
    (``{"name", "description", "input_schema"}``); ``call_tool`` returns the
    tool result (a string, or any JSON-serializable value).
    """

    def list_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class McpToolProvider:
    """Expose one MCP session's tools to an agent, name-prefixed per server.

    The prefix (e.g. ``"email_"``) keeps tool names from different servers
    from colliding and tells the model which advisor it is talking to.
    Server faults surface as ``{"error": ...}`` tool_results so the model
    can self-correct instead of crashing the agent.
    """

    def __init__(self, session: McpSession, *, prefix: str = "") -> None:
        self._session = session
        self._prefix = prefix
        self._defs: list[dict[str, Any]] = []
        self._names: set[str] = set()
        for tool in session.list_tools():
            exposed = dict(tool)
            exposed["name"] = prefix + tool["name"]
            self._defs.append(exposed)
            self._names.add(exposed["name"])

    def tool_defs(self) -> list[dict[str, Any]]:
        return [dict(tool) for tool in self._defs]

    def execute(self, name: str, payload: dict[str, Any]) -> str:
        if name not in self._names:
            return json.dumps({"error": f"unknown tool {name!r}"})
        try:
            result = self._session.call_tool(name.removeprefix(self._prefix), payload)
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        return result if isinstance(result, str) else json.dumps(result)
