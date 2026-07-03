"""Two agents coordinating through a live Age of Empires II DE match.

Game analogy: two scripted players in an actual game, negotiating a work
split by shouting numbered taunts at each other.  CS meaning: the coop_gather
protocol (propose-split / ack over the taunt codec) driven over `DeSubstrate`
and the file mailbox, with `wololo.xs` running inside the game as the other
endpoint.  Run `--offline` to rehearse against `FakeDeGame` with no game.

With ``--llm`` the scripted policies are replaced by real language models
served by a local Ollama box: the agents read their observations and invent
their own taunt conventions instead of replaying the fixed protocol.

Usage:
    .venv/bin/python scripts/de_demo.py --offline
    .venv/bin/python scripts/de_demo.py            # game running wololo.xs
    .venv/bin/python scripts/de_demo.py --dir /path/to/profile/folder
    .venv/bin/python scripts/de_demo.py --llm --model gpt-oss:20b
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from wololo.agents.base import Action, Agent, MarketAction, TauntAction
from wololo.agents.fake import FakeLlm
from wololo.agents.filters import ChannelFilter
from wololo.agents.llm import LlmAgent
from wololo.agents.ollama import OllamaClient
from wololo.codec import Message, encode_message, split_frames
from wololo.orchestrator.supervisor import AgentSpec, Supervisor
from wololo.substrate.de.bridge import DeSubstrate
from wololo.substrate.de.fakegame import FakeDeGame
from wololo.substrate.de.locate import clear_command_file, command_path_for, find_state_file
from wololo.substrate.de.mailbox import FileMailbox
from wololo.substrate.interface import GOLD, Observation, Resource

GOAL_GOLD = 600
KIND_PROPOSE_SPLIT = 1
KIND_ACK = 2

_RESOURCE_ID: dict[Resource, int] = {"food": 0, "wood": 1, "stone": 2}
_ID_RESOURCE: dict[int, Resource] = {v: k for k, v in _RESOURCE_ID.items()}


def _default_ollama_url() -> str:
    """Ollama server for --llm: $OLLAMA_HOST (scheme optional), else localhost."""
    host = os.environ.get("OLLAMA_HOST", "localhost:11434")
    return host if "://" in host else f"http://{host}"


DEFAULT_OLLAMA_URL = _default_ollama_url()

#: Role prompt for --llm mode.  Only the channels the DE bridge implements
#: are mentioned: a relic/move action would raise DeBridgeError and cost the
#: agent its memory (let-it-crash respawn).
_LLM_ROLE = """\
You are on a two-agent team playing over a live Age of Empires II match.
TEAM GOAL: the combined gold of both agents must reach {goal} as fast as
possible. You start with {stock} and no gold. You cannot see your teammate's
stockpile, only global market prices and taunts.

Available actions in this match: "taunt" and "market" ONLY (no relics, no
movement). Selling a resource lowers its price for everyone, so duplicated
work wastes gold: coordinate who sells what using taunts. Agree on your own
taunt conventions.\
"""


def leader_policy(obs: Observation, memory: dict[str, Any]) -> list[Action]:
    """Propose the split once (codec message over taunts), then sell wood."""
    actions: list[Action] = []
    if not memory.get("proposed"):
        proposal = Message(KIND_PROPOSE_SPLIT, (_RESOURCE_ID["wood"], _RESOURCE_ID["stone"]))
        actions.extend(TauntAction(t) for t in encode_message(proposal))
        memory["proposed"] = True
    if obs.stockpile.get("wood", 0) >= 100:
        actions.append(MarketAction("sell", "wood"))
    return actions


def follower_policy(obs: Observation, memory: dict[str, Any]) -> list[Action]:
    """Idle until the proposal arrives over taunts; then ACK and sell."""
    buffer: list[int] = memory.setdefault("buffer", [])
    buffer.extend(e.taunt for e in obs.taunts if e.sender != obs.agent_id)

    actions: list[Action] = []
    messages, rest = split_frames(buffer)
    memory["buffer"] = rest
    for message in messages:
        if message.kind == KIND_PROPOSE_SPLIT and "role" not in memory:
            mine = message.args[1]  # second slot of the split is ours
            memory["role"] = _ID_RESOURCE[mine]
            actions.extend(TauntAction(t) for t in encode_message(Message(KIND_ACK, (mine,))))

    role: Resource | None = memory.get("role")
    if role is not None and obs.stockpile.get(role, 0) >= 100:
        actions.append(MarketAction("sell", role))
    return actions


def print_epoch(bridge: DeSubstrate, epoch: int) -> None:
    obs0, obs1 = bridge.observe(0), bridge.observe(1)
    gold = {0: obs0.stockpile.get(GOLD, 0), 1: obs1.stockpile.get(GOLD, 0)}
    print(f"epoch {epoch:3d} | prices {obs0.prices} | gold {gold}")
    for event in obs0.taunts:
        print(f"    taunt: agent {event.sender} shouts {event.taunt}")


def make_scripted_agent(agent_id: int) -> Agent:
    policy = leader_policy if agent_id == 0 else follower_policy
    return FakeLlm(agent_id, policy)


def make_llm_agent(agent_id: int, model: str, base_url: str) -> Agent:
    stock = "400 wood" if agent_id == 0 else "400 stone"
    role = _LLM_ROLE.format(goal=GOAL_GOLD, stock=stock)
    return ChannelFilter(LlmAgent(agent_id, role, OllamaClient(model, base_url=base_url)))


def run(bridge: DeSubstrate, max_epochs: int, make_agent: Any) -> bool:
    supervisor = Supervisor(
        bridge,
        [
            AgentSpec(0, lambda: make_agent(0)),
            AgentSpec(1, lambda: make_agent(1)),
        ],
    )
    for _ in range(max_epochs):
        epoch = supervisor.run_tick()
        print_epoch(bridge, epoch)
        total = sum(bridge.observe(a).stockpile.get(GOLD, 0) for a in (0, 1))
        if total >= GOAL_GOLD:
            print(f"GOAL REACHED: team gold {total} >= {GOAL_GOLD} after {epoch} epochs")
            return True
    print(f"goal not reached in {max_epochs} epochs")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="wololo two-agent demo over the DE bridge")
    parser.add_argument("--dir", type=Path, default=None, help="folder holding the .xsdat files")
    parser.add_argument("--offline", action="store_true", help="use FakeDeGame instead of DE")
    parser.add_argument("--epochs", type=int, default=20, help="max epochs to run (default 20)")
    parser.add_argument("--timeout", type=float, default=60.0, help="per-epoch ack timeout (s)")
    parser.add_argument("--llm", action="store_true", help="drive agents with Ollama models")
    parser.add_argument("--model", default="qwen3.6:35b", help="Ollama model tag for --llm")
    parser.add_argument(
        "--ollama-url",
        default=DEFAULT_OLLAMA_URL,
        help="Ollama server base URL for --llm (default: $OLLAMA_HOST or localhost)",
    )
    args = parser.parse_args(argv)

    if args.llm:
        print(f"LLM agents: {args.model} via {args.ollama_url}")

        def make_agent(agent_id: int) -> Agent:
            return make_llm_agent(agent_id, args.model, args.ollama_url)
    else:
        make_agent = make_scripted_agent

    if args.offline:
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="wololo_de_"))
        cmd, state = tmp / "wololo_cmd.xsdat", tmp / "wololo_state.xsdat"
        game = FakeDeGame(cmd, state, {0: {"wood": 400}, 1: {"stone": 400}})
        bridge = DeSubstrate(
            FileMailbox(send_path=cmd, recv_path=state),
            agent_ids=[0, 1],
            timeout=5.0,
            sleep=lambda _s: game.step(),  # each poll advances the fake game
        )
        game.start()
        print(f"offline rehearsal against FakeDeGame in {tmp}")
    else:
        state = find_state_file(args.dir, timeout=30.0)
        cmd = command_path_for(state)
        if clear_command_file(state):
            print(f"cleared stale command file: {cmd}")
        print(f"state file: {state}")
        print(f"command file: {cmd}")
        bridge = DeSubstrate(
            FileMailbox(send_path=cmd, recv_path=state),
            agent_ids=[0, 1],
            timeout=args.timeout,
        )

    print("connecting (waiting for the game's state frame)...")
    bridge.connect()
    print("connected. running two agents; watch the in-game chat for taunts.")
    return 0 if run(bridge, args.epochs, make_agent) else 1


if __name__ == "__main__":
    sys.exit(main())
