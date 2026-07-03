# CLAUDE.md — wololo

Agent orchestration framework whose coordination substrate is Age of Empires II
mechanics. Inspired by de Wynter, *"If LLMs Have Human-Like Attributes, Then So
Does Age of Empires II"* (arXiv:2605.31514), which proves AoE II is
functionally- and Turing-complete. We take the joke seriously: instead of
encoding computation *inside* the game, we use AoE II mechanics as the **only
coordination layer** between real LLM agents.

## Core design constraint (do not violate)

Agents may communicate **exclusively** through in-game channels:

| Channel | Game mechanic | Orchestration primitive |
|---|---|---|
| `taunt`  | 105 numbered taunts (1–105) | message bus; discrete 105-symbol alphabet |
| `market` | global buy/sell prices, drift per transaction | shared scalar state / slow consensus |
| `relic`  | one relic ⇒ one monastery | distributed mutex / leader election |
| `wololo` | monk conversion of a unit | ownership transfer / work stealing *(planned — not in the substrate yet)* |
| `fog`    | fog of war | partial observability (each agent sees only its explored map; expanded via `move_op`) |

No side-channel JSON between agents. The orchestrator may talk to each agent
privately (task assignment, supervision), but **agent↔agent traffic goes
through the substrate only**. This constraint is the research content:
protocols over a narrow, lossy, public channel.

Triggers (condition → effect, loopable) are the substrate's internal event
bus / rule engine — used by the kernel, not by agents directly.

## External tools (MCP)

Agents may additionally hold **private tools** connecting them to the outside
world (email, spreadsheets, ...), supplied per-agent as `ToolProvider`s —
typically `McpToolProvider` wrapping an MCP server session (`agents/mcp.py`,
duck-typed, no `mcp` SDK dependency, sync only). Rules:

- Tools are **agent↔world**, never agent↔agent. A tool must not be a covert
  channel between agents (no shared mutable stores readable by two agents).
- Anything one agent learned via a tool reaches another agent only through
  the substrate: control *and* data plane is the taunt codec.
- Each agent gets its own session ⇒ its own credential scope (e.g. the mail
  watcher can read email but not write sheets, and vice versa).
- Codec helper tools (`encode_message` / `decode_taunts`) are local, free,
  and invisible to other agents.

Reference demo: `orchestrator/shipping.py` — a mail-watcher agent parses an
Amazon shipping email (fake inbox session), shouts the facts as a codec
message; a sheet-scribe agent decodes and appends the row (fake sheet
session). Offline and deterministic; real MCP sessions and
`client_factory=AnthropicClient` slot into the same seams.

## Architecture

```
src/wololo/
  substrate/
    interface.py     # Substrate ABC: tick(), taunt(), market_op(), relic_op(), move_op(), observe()
    de/              # Milestone 3: bridge to a real AoE II DE match
      xsdat.py       # int32 .xsdat file codec (XS file I/O physical layer)
      protocol.py    # command/state frames: MAGIC VERSION seq ack records CHECKSUM
      mailbox.py     # two-file dead-drop exchange; torn reads rejected by checksum
      bridge.py      # DeSubstrate: Substrate over the mailbox (taunt+market only)
      fakegame.py    # FakeDeGame: offline stand-in = executable spec for wololo.xs
    sim/             # Milestone 1: deterministic simulated kernel
      kernel.py      # tick-based world state
      taunts.py      # taunt bus (broadcast, ordered per tick)
      market.py      # price dynamics: price drifts with each buy/sell
      relics.py      # relic ownership = locks
      triggers.py    # condition→effect rules, evaluated each tick
      fog.py         # per-agent visibility masks
  codec/
    tauntcodec.py    # messages ⇄ taunt sequences (base-52 varints over 104 symbols; 105 = end)
  agents/
    base.py          # Agent ABC: observe → think → act; typed actions
    llm.py           # LLM-backed agent, JSON mode (Anthropic API; lazy import)
    tools.py         # tool-use harness: action tools + codec helpers + ToolProvider
    mcp.py           # MCP bridge: expose a server session as a ToolProvider
    fake.py          # FakeLlm deterministic agent for tests (Nexus pattern)
  orchestrator/
    supervisor.py    # spawn/monitor/respawn agents; let-it-crash
    scenarios.py     # scenario = map + goals + agent roster; registry
    harness.py       # batch runs, JSONL records, cross-run n-gram stats
    shipping.py      # email→taunts→spreadsheet pipeline demo (MCP-style tools)
    cli.py           # run a scenario from the command line (--stats/--runs/--record)
  analysis.py        # taunt n-gram statistics (protocol emergence)
tests/
```

## Simulation rules

- **Tick-based and fully deterministic** given a seed. Same seed + same agent
  decisions ⇒ identical run. All randomness through one seeded `random.Random`
  owned by the kernel.
- Taunts sent in tick *t* are visible to everyone in tick *t+1*, in stable
  order (sender id, then sequence).
- Market: single global price per resource; each buy raises, each sell lowers
  the price by a fixed step (AoE II-style). Prices are the only globally
  visible mutable scalars.
- Relic ops are atomic within a tick; contested grabs resolve by deterministic
  priority (lowest agent id wins), losers get a rejection observation.
- LLM calls happen **between** ticks; the kernel never awaits a model.

## Tech & commands

- Python 3.12+, `src/` layout, `pyproject.toml`.
- Runtime deps: stdlib only for the kernel. `anthropic` (or Vertex client)
  only inside `agents/llm.py`. Do not add dependencies without asking.
- Setup: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
- Test: `pytest -q` (must pass before any commit)
- Lint/format: `ruff check . && ruff format .`

## Code style

- Full type hints; `from __future__ import annotations`.
- Dataclasses for state, plain functions where a class isn't earning its keep.
- Modules ≤ ~300 lines; split when bigger.
- No async in Milestone 1 — the tick loop is synchronous by design.
- Docstrings state game-mechanic analogy first, then the CS meaning
  (e.g. "Relic garrison — acquire distributed lock").

## Testing rules

- Kernel and codec: pure pytest, no network, no LLM.
- `tauntcodec`: property-style round-trip tests (message → taunts → message).
- Agents in tests use `fake.py` with scripted decisions.
- Every substrate mechanic gets at least one adversarial test
  (relic contention, market race, taunt flood, fog boundary).

## Milestones

1. **DONE** — Simulated kernel + taunt codec + relic locks + market + trigger
   engine, FakeLlm agents, full test suite. Definition of done: a scripted
   2-agent scenario (`coop_gather`) where agents coordinate a resource goal
   *via taunts only*, green in CI.
2. **DONE** — LLM-backed agents (`llm_gather`, JSON mode; `llm_gather_tools`,
   tool-use mode), taunt n-gram stats for protocol emergence, batch
   experiment harness. Plus the MCP tool-provider layer and the
   `shipping_pipeline` real-world demo (see "External tools").
3. **DONE** — Bridge to actual AoE II DE. Offline half:
   `substrate/de/` file-mailbox protocol, `DeSubstrate` (taunt + market
   channels), `FakeDeGame` as the executable spec for the in-game XS
   script, contract tests green with no game installed. In-game half
   (`xs/wololo.xs`) validated on the Feral macOS port (May 2026 native
   build): live match driven by `scripts/de_demo.py` and the Streamlit
   newsroom over `--de`. Runbook: `docs/de_bridge.md`. The DE bridge is
   wall-clock bound by nature; the "no wall-clock" rule applies to the sim
   kernel, not here. Sim stays the reference implementation and the CI
   substrate. Relic, fog, and wololo conversion remain planned / sim-only.

## What NOT to do

- Don't let agents exchange raw text outside the taunt codec.
- Don't let external tools become an agent↔agent side channel (no shared
  mutable stores between agents' tool sessions).
- Don't make the kernel async, threaded, or wall-clock dependent.
- Don't add an LLM call inside kernel code paths.
- Don't build UI. `cli.py` printing tick logs is enough for now.
