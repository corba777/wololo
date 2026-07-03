"""Run the newsroom agents against the Streamlit exchange directory.

The other half of `apps/newsroom_app.py`: builds the newsroom pipeline
with file-backed tool sessions, then ticks forever.  The fact checker
polls the inbox the Streamlit form writes; the journalist appends to the
dashboard the Streamlit page renders.  Between them, claim text and
verdict travel as taunts — watch the tick log.

By default the taunt bus is the simulated kernel.  With ``--de`` it is a
live Age of Empires II DE match running `xs/wololo.xs`: every byte of the
claim scrolls through the match chat as taunt shouts.  ``--de-offline``
rehearses the same path against FakeDeGame with no game installed.

Usage:
    .venv/bin/python scripts/newsroom_demo.py                 # sim kernel
    .venv/bin/python scripts/newsroom_demo.py --llm           # + Ollama models
    .venv/bin/python scripts/newsroom_demo.py --anthropic     # + Anthropic API
    .venv/bin/python scripts/newsroom_demo.py --de            # live DE match
    .venv/bin/python scripts/newsroom_demo.py --de --llm      # the full show
    .venv/bin/python scripts/newsroom_demo.py --de-offline    # FakeDeGame
"""

from __future__ import annotations

import argparse
import atexit
import os
import sys
import time
from pathlib import Path

from wololo.agents.filters import ChannelFilter
from wololo.agents.llm import DEFAULT_MODEL as ANTHROPIC_DEFAULT_MODEL
from wololo.agents.llm import AnthropicClient
from wololo.agents.ollama import OllamaClient
from wololo.orchestrator.newsroom import build_newsroom_pipeline
from wololo.orchestrator.newsstore import (
    FileDashboardSession,
    FileDeskSession,
    append_log,
    clear_runner_pid,
    clear_runner_stop,
    pid_alive,
    read_runner_pid,
    request_runner_stop,
    stop_requested,
    write_runner_pid,
)
from wololo.orchestrator.scenarios import build_kernel
from wololo.orchestrator.supervisor import AgentSpec, Supervisor
from wololo.substrate.de.bridge import DeBridgeError, DeSubstrate
from wololo.substrate.de.fakegame import FakeDeGame
from wololo.substrate.de.locate import clear_command_file, command_path_for, find_state_file
from wololo.substrate.de.mailbox import FileMailbox
from wololo.substrate.interface import Substrate

DEFAULT_DIR = Path.home() / ".wololo" / "newsroom"
DEFAULT_OLLAMA_MODEL = "qwen3.6:35b"


def _default_ollama_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "localhost:11434")
    return host if "://" in host else f"http://{host}"


def make_de_bridge(directory: Path | None, timeout: float) -> DeSubstrate:
    """Bridge to a live match running xs/wololo.xs (see docs/de_bridge.md)."""
    state = find_state_file(directory, timeout=30.0)
    cmd = command_path_for(state)
    if clear_command_file(state):
        print(f"cleared stale command file: {cmd}")
    print(f"state file: {state}")
    print(f"command file: {cmd}")
    return DeSubstrate(
        FileMailbox(send_path=cmd, recv_path=state), agent_ids=[0, 1], timeout=timeout
    )


def make_fake_de_bridge() -> DeSubstrate:
    """Same wire path as --de, but against FakeDeGame in a temp folder."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="wololo_newsroom_de_"))
    cmd, state = tmp / "wololo_cmd.xsdat", tmp / "wololo_state.xsdat"
    game = FakeDeGame(cmd, state, {0: {}, 1: {}})
    bridge = DeSubstrate(
        FileMailbox(send_path=cmd, recv_path=state),
        agent_ids=[0, 1],
        timeout=5.0,
        sleep=lambda _s: game.step(),  # each poll advances the fake game
    )
    game.start()
    print(f"offline rehearsal against FakeDeGame in {tmp}")
    return bridge


def _log_taunts(directory: Path, epoch: int, taunts: list[tuple[int, int]]) -> None:
    """Summarize taunt bursts so the verbose panel stays readable."""
    if not taunts:
        append_log(directory, "tick", epoch=epoch, taunts=0)
        return
    by_agent: dict[int, list[int]] = {}
    for sender, taunt in taunts:
        by_agent.setdefault(sender, []).append(taunt)
    for agent, nums in sorted(by_agent.items()):
        if len(nums) == 1:
            append_log(directory, "taunt", epoch=epoch, agent=agent, taunt=nums[0])
        else:
            append_log(
                directory,
                "taunt_burst",
                epoch=epoch,
                agent=agent,
                count=len(nums),
                first=nums[0],
                last=nums[-1],
            )


def _release_runner(directory: Path) -> None:
    clear_runner_pid(directory)
    clear_runner_stop(directory)


def _acquire_runner(directory: Path, *, force: bool) -> bool:
    pid = read_runner_pid(directory)
    if pid is not None and pid_alive(pid):
        if not force:
            print(
                f"Runner already running (pid {pid}). "
                "Stop it from Streamlit or run with --force."
            )
            return False
        print(f"Stopping previous runner (pid {pid})…")
        request_runner_stop(directory)
    clear_runner_stop(directory)
    write_runner_pid(directory, os.getpid())
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="wololo newsroom agents (pair with Streamlit)")
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="exchange directory")
    parser.add_argument("--llm", action="store_true", help="drive agents with Ollama models")
    parser.add_argument(
        "--anthropic",
        action="store_true",
        help="drive agents with the Anthropic API (needs ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model name (Ollama tag with --llm, Claude id with --anthropic)",
    )
    parser.add_argument(
        "--ollama-url",
        default=_default_ollama_url(),
        help="Ollama server base URL for --llm (default: $OLLAMA_HOST or localhost)",
    )
    parser.add_argument("--de", action="store_true", help="taunt bus = live AoE II DE match")
    parser.add_argument("--de-dir", type=Path, default=None, help="folder with .xsdat files")
    parser.add_argument("--de-offline", action="store_true", help="taunt bus = FakeDeGame")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="per-epoch game ack timeout (s); default 300 with --llm/--anthropic else 60",
    )
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between ticks")
    parser.add_argument(
        "--force",
        action="store_true",
        help="stop a previous runner and take over the exchange directory",
    )
    args = parser.parse_args(argv)
    if args.llm and args.anthropic:
        parser.error("use --llm or --anthropic, not both")
    llm_backend = "ollama" if args.llm else ("anthropic" if args.anthropic else None)
    if args.timeout is None:
        args.timeout = 300.0 if llm_backend else 60.0

    client_factory = None
    model_name: str | None = None
    if llm_backend == "ollama":
        model_name = args.model or DEFAULT_OLLAMA_MODEL
        print(f"LLM agents: {model_name} via {args.ollama_url}")

        def client_factory() -> OllamaClient:
            return OllamaClient(model_name, base_url=args.ollama_url)
    elif llm_backend == "anthropic":
        model_name = args.model or ANTHROPIC_DEFAULT_MODEL
        print(f"LLM agents: {model_name} via Anthropic API")

        def client_factory() -> AnthropicClient:
            return AnthropicClient(model_name)

    args.dir.mkdir(parents=True, exist_ok=True)
    if not _acquire_runner(args.dir, force=args.force):
        return 1
    atexit.register(_release_runner, args.dir)

    mode = "de-offline" if args.de_offline else ("de" if args.de else "sim")
    append_log(
        args.dir,
        "runner_start",
        mode=mode,
        llm=llm_backend is not None,
        backend=llm_backend,
        model=model_name,
    )
    world = build_newsroom_pipeline(
        client_factory=client_factory,
        desk_session=FileDeskSession(args.dir),
        dashboard_session=FileDashboardSession(args.dir),
        log_directory=args.dir,
    )

    substrate: Substrate
    if args.de or args.de_offline:
        bridge = (
            make_fake_de_bridge() if args.de_offline else make_de_bridge(args.de_dir, args.timeout)
        )
        print("connecting (waiting for the game's state frame)...")
        bridge.connect()
        print("connected — watch the match chat for the claims flying by.")
        append_log(args.dir, "de_connected")
        substrate = bridge
    else:
        substrate = build_kernel(world.scenario)

    def wrapped(factory):  # the DE bridge carries taunt+market only
        return lambda: ChannelFilter(factory())

    supervisor = Supervisor(
        substrate,
        [AgentSpec(s.agent_id, wrapped(s.factory)) for s in world.scenario.agents],
    )

    print(f"exchange directory: {args.dir}")
    print("newsroom is open — submit claims from the Streamlit app. Ctrl-C to stop.")
    if args.de and not args.de_offline and llm_backend:
        print(
            "tip: in single-player AoE II DE pauses when the window loses focus — "
            "there is no setting to disable this. Keep the game visible/focused "
            "while the LLM thinks, or click back into the match if you see a timeout."
        )
    try:
        while not stop_requested(args.dir):
            try:
                tick = supervisor.run_tick()
            except DeBridgeError as exc:
                msg = str(exc)
                append_log(args.dir, "de_timeout", message=msg)
                print(f"\nWARNING: {msg}")
                print(
                    "  → game not responding. Is AoE II paused? "
                    "Click back into the match (SP auto-pauses on alt-tab), "
                    "then waiting 5s before retry…"
                )
                time.sleep(5.0)
                continue
            events = substrate.observe(0).taunts
            taunt_pairs = [(e.sender, e.taunt) for e in events]
            _log_taunts(args.dir, tick, taunt_pairs)
            for event in events:
                print(f"tick {tick:4d} | agent {event.sender} shouts {event.taunt}")
            time.sleep(args.interval)
        append_log(args.dir, "runner_stop", reason="stop file")
        print("\nnewsroom stopped (stop requested from Streamlit).")
        return 0
    except KeyboardInterrupt:
        append_log(args.dir, "runner_stop", reason="keyboard")
        print("\nnewsroom closed.")
        return 0
    finally:
        _release_runner(args.dir)


if __name__ == "__main__":
    sys.exit(main())
