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

## Status

Milestone 1 in progress. Current component: `tauntcodec` — serialization of
structured messages to/from sequences of the 105 numbered taunts (base-105
codec with varint framing).
