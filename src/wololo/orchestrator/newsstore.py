"""Newsroom file stores — the disk seam between the UI and the agents.

Game analogy: the reader's letterbox and the town notice board.  CS meaning:
two append-only JSONL files.  The Streamlit app writes claims to the inbox
and renders the dashboard; the fact checker's tool session *reads* the
inbox, the journalist's tool session *writes* the dashboard.  The two
agents never share a store — the claim text crosses between them on the
taunt channel only.

Writes are single-line appends (atomic enough for a demo); readers tolerate
a torn trailing line by skipping anything that does not parse.
"""

from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

INBOX_FILE = "newsroom_inbox.jsonl"
DASHBOARD_FILE = "newsroom_dashboard.jsonl"
LOG_FILE = "newsroom_log.jsonl"
RUNNER_PID_FILE = "runner.pid"
RUNNER_STOP_FILE = "runner.stop"
DESK_CURSOR_FILE = "desk.cursor"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # torn trailing write: pick it up on the next poll
    return entries


def _append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def append_log(directory: Path, event: str, **fields: Any) -> None:
    """Append one activity line for the Streamlit verbose panel."""
    from datetime import UTC, datetime

    _append_jsonl(
        directory / LOG_FILE,
        {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "event": event, **fields},
    )


def read_log(directory: Path, *, limit: int = 80) -> list[dict[str, Any]]:
    """Most recent log entries (tail), oldest first within the window."""
    entries = _read_jsonl(directory / LOG_FILE)
    return entries[-limit:]


def clear_log(directory: Path) -> None:
    """Wipe the activity log (Streamlit 'Clear log' button)."""
    path = directory / LOG_FILE
    if path.exists():
        path.unlink()


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_runner_pid(directory: Path) -> int | None:
    path = directory / RUNNER_PID_FILE
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def write_runner_pid(directory: Path, pid: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / RUNNER_PID_FILE).write_text(str(pid), encoding="utf-8")


def clear_runner_pid(directory: Path) -> None:
    path = directory / RUNNER_PID_FILE
    if path.exists():
        path.unlink()


def runner_is_alive(directory: Path) -> bool:
    pid = read_runner_pid(directory)
    return pid is not None and pid_alive(pid)


def stop_requested(directory: Path) -> bool:
    return (directory / RUNNER_STOP_FILE).exists()


def clear_runner_stop(directory: Path) -> None:
    path = directory / RUNNER_STOP_FILE
    if path.exists():
        path.unlink()


def request_runner_stop(directory: Path, *, wait_s: float = 3.0) -> bool:
    """Ask the background runner to exit; SIGTERM if it does not stop in time."""
    (directory / RUNNER_STOP_FILE).touch()
    pid = read_runner_pid(directory)
    if pid is None or not pid_alive(pid):
        clear_runner_pid(directory)
        clear_runner_stop(directory)
        return True
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            clear_runner_pid(directory)
            clear_runner_stop(directory)
            return True
        time.sleep(0.2)
    return False


def read_desk_cursor(directory: Path) -> int:
    path = directory / DESK_CURSOR_FILE
    if not path.exists():
        return 0
    try:
        return max(0, int(path.read_text(encoding="utf-8").strip()))
    except ValueError:
        return 0


def write_desk_cursor(directory: Path, cursor: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / DESK_CURSOR_FILE).write_text(str(cursor), encoding="utf-8")


def log_for_display(
    log: list[dict[str, Any]], directory: Path | None = None
) -> list[dict[str, Any]]:
    """Hide finished runs; follow the live runner process when one is active."""
    if directory is not None and runner_is_alive(directory):
        last_start: int | None = None
        for i, entry in enumerate(log):
            if entry.get("event") == "runner_start":
                last_start = i
        if last_start is not None:
            return log[last_start:]

    last_start = None
    last_stop = None
    for i, entry in enumerate(log):
        if entry.get("event") == "runner_start":
            last_start = i
        elif entry.get("event") == "runner_stop":
            last_stop = i

    if last_start is not None and (last_stop is None or last_stop < last_start):
        return log[last_start:]

    cutoff = (last_stop + 1) if last_stop is not None else 0
    return [e for e in log[cutoff:] if e.get("event") == "claim_submitted"]


def read_status(directory: Path) -> dict[str, Any]:
    """Snapshot for the UI: queue depth, outputs, runner heartbeat."""
    inbox = _read_jsonl(directory / INBOX_FILE)
    news, fakes = read_dashboard(directory)
    log = read_log(directory, limit=1)
    last_ts = log[-1]["ts"] if log else None
    return {
        "inbox_pending": len(inbox),
        "news_count": len(news),
        "fake_count": len(fakes),
        "last_log_ts": last_ts,
    }


def submit_claim(directory: Path, text: str) -> None:
    """UI side: drop one reader claim into the inbox."""
    _append_jsonl(directory / INBOX_FILE, {"text": text})
    append_log(directory, "claim_submitted", text=text)


def read_dashboard(directory: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """UI side: (published stories, fake-news column) for rendering."""
    entries = _read_jsonl(directory / DASHBOARD_FILE)
    news = [e for e in entries if e.get("type") == "news"]
    fakes = [e["claim"] for e in entries if e.get("type") == "fake"]
    return news, fakes


def clear_dashboard(directory: Path) -> None:
    """Wipe published stories and fake-news entries (Streamlit 'Clear news')."""
    path = directory / DASHBOARD_FILE
    if path.exists():
        path.unlink()


@dataclass
class FileDeskSession:
    """News-desk MCP session over the inbox file (fact checker's advisor)."""

    directory: Path
    _cursor: int = field(default=-1, init=False, repr=False)

    def __post_init__(self) -> None:
        if self._cursor < 0:
            self._cursor = read_desk_cursor(self.directory)

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
        entries = _read_jsonl(self.directory / INBOX_FILE)
        new = entries[self._cursor :]
        self._cursor = len(entries)
        write_desk_cursor(self.directory, self._cursor)
        claims = [e["text"] for e in new]
        if claims:
            append_log(self.directory, "claim_fetched", claims=claims)
        return json.dumps({"claims": claims})


@dataclass
class FileDashboardSession:
    """Dashboard MCP session over the dashboard file (journalist's typesetter)."""

    directory: Path

    @property
    def news(self) -> list[dict[str, Any]]:
        return read_dashboard(self.directory)[0]

    @property
    def fakes(self) -> list[str]:
        return read_dashboard(self.directory)[1]

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
        path = self.directory / DASHBOARD_FILE
        if name == "publish_news":
            headline = str(arguments["headline"])
            body = str(arguments["body"])
            append_log(self.directory, "journalist_writing", action="publish", text=body)
            _append_jsonl(
                path,
                {
                    "type": "news",
                    "headline": headline,
                    "body": body,
                },
            )
            append_log(self.directory, "published", headline=headline, body=body)
            return json.dumps({"published": True})
        assert name == "flag_fake"
        claim = str(arguments["claim"])
        append_log(self.directory, "journalist_writing", action="flag", text=claim)
        _append_jsonl(path, {"type": "fake", "claim": claim})
        append_log(self.directory, "fake_flagged", claim=claim)
        return json.dumps({"flagged": True})
