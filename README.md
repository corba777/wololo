# wololo

Agent orchestration framework whose coordination substrate is Age of Empires II
mechanics. Agents communicate **exclusively** through in-game channels: taunts,
the market, relics, monk conversions, and fog of war. See `CLAUDE.md` for the
full design document.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Development

```bash
pytest -q                      # test suite (must be green before commit)
ruff check . && ruff format .  # lint & format
```

## Running scenarios

```bash
wololo coop_gather            # scripted FakeLlm agents, deterministic
wololo coop_gather --stats    # + taunt n-gram stats (protocol emergence)

pip install -e ".[dev,llm]"   # LLM scenarios need the anthropic extra
ANTHROPIC_API_KEY=... wololo llm_gather --stats
```

## Status

- Milestone 1 done: deterministic sim kernel (taunt bus, market, relic
  locks, triggers, fog), base-105 taunt codec with varint framing, FakeLlm
  agents, let-it-crash supervisor, `coop_gather` scenario green in tests.
- Milestone 2 done: `LlmAgent` backed by the Anthropic API (dependency
  confined to `agents/llm.py`, injectable stub clients in tests),
  `llm_gather` cooperative negotiation scenario, taunt n-gram statistics
  for measuring protocol emergence.
- Milestone 3 (AoE II DE bridge) intentionally not started.
