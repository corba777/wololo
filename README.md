# wololo

`wololo` is a small experimental playground for constrained agent-to-agent communication.

The joke is simple:

> What if LLM agents had to coordinate through **Age of Empires II** mechanics instead of normal JSON messages, shared memory, or direct chat?

Agents communicate through a deliberately awkward substrate:

- taunts as packets;
- market prices as shared scalar state;
- relics as locks;
- monk conversions as ownership transfer;
- fog of war as partial observability.

This is **not** a production agent framework. It is a toy-but-executable testbed for asking a narrower question:

> Can agents still coordinate when the only allowed communication channel is hostile, low-bandwidth, game-like infrastructure?

`wololo` started as a joke. It remains a joke. But the communication constraint is real.

---

## Why this exists

Most agent systems assume agents can communicate over rich structured channels:

- JSON messages;
- direct conversation;
- shared memory;
- tool calls;
- external coordination services;
- explicit workflow graphs.

`wololo` asks the opposite question:

> What happens if agents are forced to coordinate through a tiny, awkward, indirect substrate?

Age of Empires II is used as the substrate because it is both ridiculous and surprisingly expressive. Taunts can carry packets. Markets can expose shared numeric state. Relics can behave like locks. Fog of war gives partial observability. Monk conversion gives ownership transfer.

The point is not to build useful business software inside AoE II.

The point is to create a funny but concrete environment where communication constraints are explicit, inspectable, and testable.

---

## What this is

- A constrained communication substrate for multi-agent experiments.
- A deliberately absurd transport layer for agent coordination.
- A toy environment for testing protocol emergence, bandwidth limits, and substrate-mediated coordination.
- A runnable joke with enough structure to become an experiment.
- A small playground for LLM agents, scripted agents, local Ollama-backed agents, and Anthropic-backed agents.

## What this is not

- Not a general-purpose agent orchestration framework.
- Not a replacement for LangGraph, AutoGen, CrewAI, MCP, Google ADK, or similar systems.
- Not intended for production workflows.
- Not an attempt to make Age of Empires II a serious enterprise middleware platform.

Probably.

---

## Core idea

| AoE II mechanic | Role in `wololo` |
|---|---|
| Taunts | Low-bandwidth message bus |
| Market | Shared scalar state / coordination signal |
| Relics | Locking / mutual exclusion |
| Monk conversion | Ownership transfer |
| Fog of war | Partial observability |
| Match ticks | Discrete-time execution substrate |

The important rule:

> Agents do not get a hidden side channel.

If one agent needs another agent to know something, that information must cross the substrate.

In the current demos, the most developed channel is the taunt bus. Structured messages are encoded into taunt sequences, sent through the substrate, then decoded by the receiving agent.

---

## Repository layout

```text
.
├── apps/                 # Streamlit UI
├── docs/                 # AoE II DE bridge docs and runbooks
├── images/               # Demo screenshots
├── scripts/              # Demo runners
├── src/wololo/           # Python package
├── tests/                # Test suite
├── xs/                   # AoE II DE XS bridge script
├── CLAUDE.md             # Design / development notes
├── README.md
└── pyproject.toml
```

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

For Streamlit UI demos:

```bash
pip install -e ".[dev,ui]"
```

For Anthropic-backed LLM demos:

```bash
pip install -e ".[dev,llm]"
```

For Streamlit + Anthropic together:

```bash
pip install -e ".[dev,ui,llm]"
```

---

## Development

```bash
pytest -q
ruff check .
ruff format .
```

The deterministic scenarios are intended to stay testable without requiring external model APIs.

---

## Running scenarios

### Deterministic simulation

```bash
wololo coop_gather
```

Scripted `FakeLlm` agents coordinate through the simulated substrate.

With taunt n-gram statistics:

```bash
wololo coop_gather --stats
```

Batch harness with JSONL run records:

```bash
wololo coop_gather --runs 10 --stats --record runs.jsonl
```

### Anthropic-backed LLM scenarios

```bash
export ANTHROPIC_API_KEY=sk-ant-...

wololo llm_gather --stats
wololo llm_gather_tools --stats
```

`llm_gather` uses raw JSON-style replies.

`llm_gather_tools` uses the tool-use harness: action tools queue substrate operations, while codec helper tools encode and decode structured taunt messages locally.

### Offline pipeline demos

```bash
wololo shipping_pipeline
wololo newsroom_pipeline
wololo relic_front_page
```

`shipping_pipeline` is a toy email → taunts → spreadsheet flow.

`newsroom_pipeline` is a toy claims → fact-check → taunts → dashboard flow.

`relic_front_page` is the same newsroom flow, but verified publish requires the
`front_page` relic lock (sim kernel only; not on the DE bridge yet).

---

## Newsroom demo

The newsroom demo is currently the clearest end-to-end example.

Two agents run a toy fact-checking workflow:

1. A reader submits a claim through a Streamlit form.
2. The fact-checker agent sees the claim through its private desk tool.
3. The fact-checker decides whether the claim is true or false.
4. The claim and verdict are encoded into taunts.
5. The journalist agent decodes the taunts.
6. The dashboard either publishes the story or flags it as fake news.

The claim text and verdict cross between agents only through the taunt channel.

### Run the UI

```bash
streamlit run apps/newsroom_app.py
```

### Run the agents with scripted models

```bash
python scripts/newsroom_demo.py
```

### Run the agents with local Ollama models

```bash
python scripts/newsroom_demo.py --llm
```

Default model:

```text
qwen3.6:35b
```

Override it with:

```bash
python scripts/newsroom_demo.py --llm --model <model-name>
```

Set the Ollama endpoint with either:

```bash
export OLLAMA_HOST=my-box:11434
```

or:

```bash
python scripts/newsroom_demo.py --llm --ollama-url http://localhost:11434
```

### Run the agents with Anthropic

```bash
export ANTHROPIC_API_KEY=sk-ant-...

python scripts/newsroom_demo.py --anthropic
```

Default model:

```text
claude-sonnet-4-5
```

Override it with:

```bash
python scripts/newsroom_demo.py --anthropic --model <model-name>
```

Use `--llm` or `--anthropic`, not both.

### Exchange directory

The Streamlit app and the agent runner default to:

```text
~/.wololo/newsroom
```

Override it with `--dir` on both sides.

Example:

```bash
streamlit run apps/newsroom_app.py -- --dir /tmp/wololo-newsroom
python scripts/newsroom_demo.py --dir /tmp/wololo-newsroom
```

---

## Running over a real Age of Empires II DE match

The taunt bus is swappable.

With `--de`, the agents talk over a live Age of Empires II DE match instead of the simulation kernel.

The setup is intentionally strange:

- agent commands are written into `wololo_cmd.xsdat`;
- the in-game XS script reads and applies them;
- taunts are echoed into match chat;
- market state and stockpiles are mirrored through game state;
- the game writes state back to the file mailbox;
- the Python runner continues from that state.

Setup details live in:

```text
docs/de_bridge.md
```

### Newsroom over live AoE II DE

```bash
python scripts/newsroom_demo.py --de
```

With Ollama:

```bash
python scripts/newsroom_demo.py --de --llm
```

With Anthropic:

```bash
python scripts/newsroom_demo.py --de --anthropic
```

With the fake DE bridge path, no live game:

```bash
python scripts/newsroom_demo.py --de-offline
```

### Live game quick start

One-time setup is documented in:

```text
docs/de_bridge.md
```

For each live session:

1. Copy the bridge script:

   ```text
   xs/wololo.xs
   ```

   into the game's `_common/xs/` folder.

   On the Feral macOS port, the path is usually similar to:

   ```text
   ~/Library/Application Support/Feral Interactive/Age Of Empires II/VFS/User/Games/Age of Empires 2 DE/<steam-id>/resources/_common/xs/
   ```

2. Start the scenario in the game.

   In the scenario editor:

   ```text
   Script Filename = wololo
   ```

   Then run:

   ```text
   Test Scenario
   ```

   Wait for this line in match chat:

   ```text
   [wololo] bridge script initialised
   ```

3. Keep the match unpaused.

   Single-player DE pauses when the game window loses focus.

4. Start the Streamlit dashboard:

   ```bash
   streamlit run apps/newsroom_app.py
   ```

5. Start the runner after the game is ticking:

   ```bash
   python scripts/newsroom_demo.py --de --force
   ```

   Or with Ollama:

   ```bash
   python scripts/newsroom_demo.py --de --llm --force
   ```

   Or with Anthropic:

   ```bash
   python scripts/newsroom_demo.py --de --anthropic --force
   ```

6. Submit a claim in the browser.

   Agent 0 shouts the encoded claim bytes into match chat. Agent 1 answers with short status taunts such as:

   ```text
   heard the dispatch
   decoded the claim
   publishing...
   flagging fake news
   ```

   Then the story lands on the dashboard.

7. Stop the runner before restarting the scenario.

   Use the Streamlit stop button or `Ctrl-C` in the runner terminal. This avoids replaying stale `wololo_cmd.xsdat` commands on the next scenario run.

---

## Simpler live negotiation demo

For the smaller two-agent negotiation demo without Streamlit:

```bash
python scripts/de_demo.py --offline
```

Against a live match:

```bash
python scripts/de_demo.py
```

With local Ollama models:

```bash
python scripts/de_demo.py --llm
```

`--llm` needs an Ollama server. Set:

```bash
export OLLAMA_HOST=my-box:11434
```

or pass:

```bash
python scripts/de_demo.py --llm --ollama-url http://localhost:11434
```

Pick a model with:

```bash
python scripts/de_demo.py --llm --model <model-name>
```

The local Ollama negotiation may take minutes per epoch. That is expected: the agents are actually negotiating rather than following a precomputed script.

---

## Screenshots

Live mode with `--de`: Streamlit on the left, AoE II DE on the right. Agent 0 shouts the encoded claim as taunts; agent 1 echoes short status taunts back into the match chat.

### Ollama

`--de --llm`, default model `qwen3.6:35b`.

Claim in, taunts on the wire:

![Honey claim: Streamlit log and agent 0 taunts in match chat](images/ollama/honey-claim-pipeline.png)

Fake-news debunk + in-game acknowledgements:

![Moon-cheese debunk with agent 1 status lines in game chat](images/ollama/de-bridge-chat.png)

### Anthropic

`--de --anthropic`, default model `claude-sonnet-4-5`.

Verified story published:

![Honey verified: dashboard story and agent 1 publishing in game chat](images/anthropic/verified-story.png)

Verified + fake on the dashboard:

![Honey verified and moon-cheese flagged, with in-game fake-news acknowledgement](images/anthropic/verified-and-fake.png)

More captures live under:

```text
images/ollama/
images/anthropic/
```

---

## Current status

### Done

- Deterministic simulation kernel:
  - taunt bus;
  - market channel;
  - relic locks;
  - triggers;
  - fog;
  - scripted fake agents;
  - let-it-crash supervisor.

- Taunt codec:
  - varint framing;
  - base-52 chunks over the data taunts;
  - taunt `105` as end-of-message marker.

- Deterministic `coop_gather` scenario.

- Anthropic-backed `LlmAgent`.

- `llm_gather` cooperative negotiation scenario.

- Taunt n-gram statistics for rough protocol-emergence inspection.

- Tool-use harness:
  - `ToolLlmAgent`;
  - substrate action tools;
  - local codec helper tools;
  - batch experiment harness;
  - JSONL run records;
  - cross-run n-gram aggregation.

- MCP-style tool-provider layer:
  - per-agent tool sessions;
  - private tools;
  - credential scoping;
  - shipping pipeline demo.

- Newsroom pipeline:
  - Streamlit form;
  - fact-checker agent;
  - journalist agent;
  - taunt-encoded claim transfer;
  - dashboard publishing / fake-news flagging.

- AoE II DE bridge:
  - `.xsdat` int32 codec;
  - checksummed frame protocol;
  - file mailbox;
  - `DeSubstrate`;
  - `FakeDeGame` executable spec;
  - `xs/wololo.xs`;
  - live demos through `scripts/de_demo.py` and `scripts/newsroom_demo.py --de`.

### Still simulation-only

These mechanics exist in the simulated substrate but are not fully live through the DE bridge yet:

- relics;
- fog;
- monk conversion.

---

## Possible experiments

These are not all implemented yet, but they are the direction where the toy becomes more useful.

### Direct channel baseline

Run the same scenario with direct JSON communication and compare it to the taunt substrate.

Useful metrics:

| Metric | Meaning |
|---|---|
| success rate | Did the agents complete the task? |
| ticks to completion | How long did coordination take? |
| taunts per task | Communication cost |
| bytes per task | Payload cost |
| retries | Protocol instability |
| failures by type | Decode error, timeout, contradiction, etc. |

### Lossy channel

Add channel noise:

```bash
--drop-rate 0.05
--duplicate-rate 0.02
--shuffle-window 3
--max-taunts-per-tick 20
```

Then compare protocols with and without acknowledgements.

### ACK/NACK protocol

Build a thin session protocol over taunts:

```text
MSG
ACK
NACK
SEQ
CHECKSUM
RETRANSMIT
```

This would turn the project into a small “TCP over AoE II taunts” experiment.

### Protocol emergence

Track repeated n-grams, entropy, compression, and task-specific conventions across multiple runs.

The obvious question:

> Do agents invent stable communication shortcuts when the channel is expensive?

---

## Roadmap

Possible next steps:

- tighten README and docs around the “constrained A2A substrate” framing;
- split the protocol details into `docs/protocol.md`;
- add `docs/experiments.md`;
- add a benchmark command;
- add direct-channel baselines;
- add lossy-channel simulation;
- add ACK/NACK retransmission;
- expose more metrics in the Streamlit dashboard;
- expand the live DE bridge beyond taunts and market state;
- add a short demo video or GIF.

---

## Design notes

See:

```text
CLAUDE.md
```

for the current design/development notes.

A more formal protocol document should probably move to:

```text
docs/protocol.md
```

once the codec and substrate semantics stabilize.

---

## Suggested GitHub metadata

Repository description:

```text
Toy experimental substrate for constrained LLM agent communication through Age of Empires II mechanics.
```

Suggested topics:

```text
llm-agents
multi-agent-systems
agent-communication
protocol-emergence
age-of-empires-ii
ollama
anthropic
streamlit
```

---

## Name

`wololo` is named after the classic Age of Empires priest / monk conversion sound.

It is also a fair description of the project’s research methodology.

```text
wololo
```