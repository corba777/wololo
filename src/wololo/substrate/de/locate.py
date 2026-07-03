"""Locating a live game's .xsdat files — where does DE keep its saddlebags?

Game analogy: finding the courier drop point outside the city walls.  CS
meaning: filesystem discovery for the DE bridge.  The state file is named
after the scenario (``wololo_state.xsdat``); the editor's *test* mode names
it ``default<N>.xsdat`` (N = player slot) instead, so both are accepted.
Only a *fresh* file counts — a stale one means the scenario is not running
(or the game paused on focus loss).
"""

from __future__ import annotations

import time
from pathlib import Path

#: Default macOS (Feral port) profile folder where .xsdat files live.
FERAL_GAMES_DIR = Path(
    "~/Library/Application Support/Feral Interactive/Age Of Empires II"
    "/VFS/User/Games/Age of Empires 2 DE"
).expanduser()

STATE_GLOBS = ("wololo_state.xsdat", "default*.xsdat")

#: A state file older than this is presumed dead (the XS rule ticks every 2s).
FRESH_SECONDS = 30.0


def command_path_for(state: Path) -> Path:
    return state.parent / "wololo_cmd.xsdat"


def clear_command_file(state: Path) -> bool:
    """Remove a leftover wololo_cmd.xsdat (prevents ghost taunts on scenario start)."""
    cmd = command_path_for(state)
    if cmd.exists():
        cmd.unlink()
        return True
    return False


def find_state_file(directory: Path | None, timeout: float) -> Path:
    """Locate the game's freshly-written state .xsdat, or exit with advice."""
    roots = [directory] if directory else [FERAL_GAMES_DIR]
    deadline = time.monotonic() + timeout
    while True:
        candidates = [
            path
            for root in roots
            if root.is_dir()
            for pattern in STATE_GLOBS
            for path in root.rglob(pattern)
        ]
        if candidates:
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            age = time.time() - newest.stat().st_mtime
            if age < FRESH_SECONDS:
                clear_command_file(newest)
                return newest
            clear_command_file(newest)
            print(f"found {newest} but it is stale ({age:.0f}s old); waiting for a fresh write...")
        if time.monotonic() >= deadline:
            searched = ", ".join(str(r) for r in roots)
            raise SystemExit(
                f"no fresh state file ({' / '.join(STATE_GLOBS)}) under {searched}.\n"
                "Is the wololo scenario running in the game? See docs/de_bridge.md."
            )
        time.sleep(1.0)
