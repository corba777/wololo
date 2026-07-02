# DE bridge — in-game side and smoke test

Milestone 3, step 1 (this repo) is done offline: `wololo.substrate.de`
implements the file-mailbox protocol, `DeSubstrate`, and `FakeDeGame` — the
executable specification of what the in-game script must do. Step 2 is
validating the game half on a real installation. This document is the
runbook for that.

## How the bridge works

```
orchestrator (Python)                      Age of Empires II: DE
┌───────────────────┐   wololo_cmd.xsdat   ┌──────────────────────┐
│ DeSubstrate       │ ───────────────────> │ custom scenario      │
│  agents, LLM calls│                      │  + wololo.xs rule    │
│  between epochs   │ <─────────────────── │  (runs every N secs) │
└───────────────────┘  wololo_state.xsdat  └──────────────────────┘
```

- Both files are flat sequences of **int32** (XS `xsWriteInt`/`xsReadInt`),
  framed as `MAGIC VERSION seq ack n_records [record...] CHECKSUM`
  (see `substrate/de/protocol.py` for record types).
- Our side writes `wololo_cmd.xsdat` atomically. The XS rule polls it,
  applies command records whose `seq` is new, and rewrites
  `wololo_state.xsdat` with `ack = <last command seq applied>`.
- `DeSubstrate.tick()` blocks until the state file acks its command frame.
  A torn read fails the checksum and is simply polled again.
- `FakeDeGame.step()` in `substrate/de/fakegame.py` is the reference
  implementation of the XS side, down to market economics.

## Smoke test 0: does XS file I/O work in the Feral macOS port?

This is the go/no-go gate; ten minutes, no wololo code involved.

1. Install the game (Steam → Age of Empires II: Definitive Edition; the
   macOS build is included with the Windows license).
2. Find the XS scripts folder. On Windows it is
   `<Steam>/steamapps/common/AoE2DE/resources/_common/xs/`. On macOS look
   inside the app bundle / Feral support folders:

   ```bash
   find ~/Library/Application\ Support ~/Library/Containers \
        -ipath '*aoe*' -iname 'xs' -type d 2>/dev/null
   find "$HOME/Library/Application Support/Steam/steamapps/common" \
        -iname 'Constants.xs' 2>/dev/null
   ```

3. Drop this file there as `wololo_probe.xs`:

   ```c
   int probeTicks = 0;

   void writeProbe() {
       probeTicks = probeTicks + 1;
       xsCreateFile(false);          // file is named after the scenario
       xsWriteInt(41186);            // MAGIC
       xsWriteInt(1);                // VERSION
       xsWriteInt(probeTicks);       // seq
       xsCloseFile();
   }

   rule wololoProbe
       active
       minInterval 2
       maxInterval 2
   {
       writeProbe();
   }
   ```

4. In the scenario editor: new scenario named `wololo_probe`, Map tab →
   `Script Filename` → `wololo_probe`. Save, then *test* the scenario.
5. After ~10 seconds of game time, find the output:

   ```bash
   find ~/Library "$HOME/Games" -iname 'wololo_probe*.xsdat' 2>/dev/null
   ```

6. Verify from the repo — decode the ints and check the seq grows:

   ```bash
   .venv/bin/python - <<'EOF'
   from pathlib import Path
   from wololo.substrate.de.xsdat import read_ints
   print(read_ints(Path("<path from step 5>")))
   EOF
   ```

   Expected: `[41186, 1, <n>]` with `n` increasing on re-reads while the
   scenario runs.

**Outcomes.** File appears with correct ints → the transport works, go.
File appears but bytes look wrong → byte order differs in the port; flip
the struct format in `substrate/de/xsdat.py` (one line) and retest. No
file at all → Feral port lacks XS file I/O; fall back to the Linux/Proton
VM route, where this exact flow is community-verified.

## Smoke test 1: can XS *read* what we write?

Same setup, but the rule calls `xsOpenFile("wololo_cmd")` / `xsReadInt()`
and echoes the ints back into the state file. Run it, then from the repo:

```bash
.venv/bin/python - <<'EOF'
from pathlib import Path
from wololo.substrate.de.protocol import Frame, Record, encode_frame
from wololo.substrate.de.xsdat import write_ints
write_ints(Path("<xsdat folder>/wololo_cmd.xsdat"),
           encode_frame(Frame(seq=1, ack=0, records=(Record(1, (0, 31)),))))
EOF
```

If the echo comes back, the mailbox round-trips and the remaining work is
porting `FakeDeGame.step()` to XS (`wololo.xs`): taunt records via
`xsTaunt`-equivalent effects and market records via trigger effects or the
AI-script layer — see "Open questions".

## Open questions (to resolve with the game in hand)

1. **Exact .xsdat byte format** of `xsWriteInt` in the Feral port
   (assumed little-endian int32; isolated in `xsdat.py`).
2. **Where .xsdat files land on macOS** (profile folder layout differs
   from Windows; both smoke tests locate it with `find`).
3. **Sending taunts from script.** Scenario triggers can play taunt
   *sounds*/chat, but making player-attributed taunts that AI-script
   `taunt-detected` facts can hear may need the AI layer
   (`xs-script-call` bridges AI `.per` files and XS, per the
   [UGC guide](https://ugc.aoe2.rocks/general/xs/programmer/)).
4. **Market ops from script** — trigger effects vs AI `buy-commodity` /
   `sell-commodity`; the AI route matches real game economics exactly.
5. **Rule interval floor** (minInterval 1s?) sets the minimum epoch length;
   our tick cadence must respect it.

## References

- [Official XS scripting reference (Forgotten Empires)](https://www.forgottenempires.net/age-of-empires-ii-definitive-edition/xs-scripting-in-age-of-empires-ii-definitive-edition)
- [UGC Guide: XS for programmers](https://ugc.aoe2.rocks/general/xs/programmer/)
- [macOS release announcement](https://www.ageofempires.com/news/age-of-empires-ii-definitive-edition-available-now-on-mac/)
