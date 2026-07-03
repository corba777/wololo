// wololo.xs — the in-game half of the DE bridge (Milestone 3, step 2).
//
// Port of FakeDeGame.step() (src/wololo/substrate/de/fakegame.py), which is
// the executable specification for this file.  Every rule tick (2 s):
//
//   1. poll wololo_cmd.xsdat for a new command frame (seq != last applied),
//   2. apply taunt/market records; echo taunts into the game chat,
//   3. rewrite the state file (named after the scenario: use "wololo_state";
//      the editor's *test* mode writes default0.xsdat instead — the Python
//      runner accepts both) with prices, stockpiles, taunt echoes and
//      ack = seq of the last command frame applied.
//
// Frame layout (all int32, see substrate/de/protocol.py):
//     MAGIC VERSION seq ack n_records [type n_fields field*]* CHECKSUM
// Checksum = plain sum of everything before it.  All demo values are tiny,
// so the sum stays far below 2^31 and matches Python's sum % 2^31.
//
// Restart tolerance (superset of FakeDeGame): a frame is applied whenever
// seq != last applied, so a restarted orchestrator (seq reset) is picked up.

// -- protocol ----------------------------------------------------------------
const int W_MAGIC = 41186;      // 0xA0E2
const int W_VERSION = 1;
const int W_CMD_TAUNT = 1;      // [agent, taunt]
const int W_CMD_MARKET = 2;     // [agent, op(0=buy,1=sell), resource]
const int W_ST_PRICE = 1;       // [resource, price]
const int W_ST_STOCK = 2;       // [agent, resource, amount]
const int W_ST_TAUNT = 3;       // [sender, taunt]
const int W_OP_BUY = 0;
const int W_MAX_RECORDS = 256;  // sanity cap on inbound frames; must match
                                // protocol.MAX_RECORDS (sized for text-over-
                                // taunts: a ~100-char claim is ~210 taunts)

// -- market rules (mirror of substrate/sim/market.py defaults) ---------------
const int W_LOT_SIZE = 100;
const int W_PRICE_STEP = 5;
const int W_MIN_PRICE = 20;
const int W_MAX_PRICE = 9999;

// -- world state (2 agents, resources: 0=food 1=wood 2=stone 3=gold) ---------
int gInited = 0;
int gPrices = -1;     // int array[3], start price 100
int gStock = -1;      // int array[8], index = agent * 4 + resource
int gRecType = -1;    // parsed inbound records (validated before applying)
int gRecF0 = -1;
int gRecF1 = -1;
int gRecF2 = -1;
int gTauntBuf = -1;   // (sender, taunt) pairs heard this epoch, echoed once
int gTauntCount = 0;
int gEpoch = 0;       // seq of our state frames
int gAcked = 0;       // seq of the last command frame applied
int gSum = 0;         // running checksum while writing

void initWololo() {
    if (gInited == 1) {
        return;
    }
    gInited = 1;
    gPrices = xsArrayCreateInt(3, 100, "wololoPrices");
    gStock = xsArrayCreateInt(8, 0, "wololoStock");
    gRecType = xsArrayCreateInt(W_MAX_RECORDS, 0, "wololoRecType");
    gRecF0 = xsArrayCreateInt(W_MAX_RECORDS, 0, "wololoRecF0");
    gRecF1 = xsArrayCreateInt(W_MAX_RECORDS, 0, "wololoRecF1");
    gRecF2 = xsArrayCreateInt(W_MAX_RECORDS, 0, "wololoRecF2");
    gTauntBuf = xsArrayCreateInt(W_MAX_RECORDS * 2, 0, "wololoTaunts");
    // Demo roster (matches the coop_gather scenario and scripts/de_demo.py):
    // agent 0 starts with 400 wood, agent 1 with 400 stone.
    xsArraySetInt(gStock, 0 * 4 + 1, 400);
    xsArraySetInt(gStock, 1 * 4 + 2, 400);
    xsChatData("[wololo] bridge script initialised, epoch %d", gEpoch);
}

void bufferTaunt(int sender = 0, int taunt = 0) {
    if (gTauntCount >= W_MAX_RECORDS) {
        return;
    }
    xsArraySetInt(gTauntBuf, gTauntCount * 2, sender);
    xsArraySetInt(gTauntBuf, gTauntCount * 2 + 1, taunt);
    gTauntCount = gTauntCount + 1;
    if (sender == 0) {
        xsChatData("[wololo] agent 0 shouts taunt %d", taunt);
    } else if (taunt == 101) {
        xsChatData("[wololo] agent 1 heard the dispatch");
    } else if (taunt == 102) {
        xsChatData("[wololo] agent 1 decoded the claim");
    } else if (taunt == 103) {
        xsChatData("[wololo] agent 1 publishing verified story");
    } else if (taunt == 104) {
        xsChatData("[wololo] agent 1 flagging fake news");
    } else {
        xsChatData("[wololo] agent 1 shouts taunt %d", taunt);
    }
}

void applyMarket(int agent = 0, int op = 0, int resource = 0) {
    if ((agent < 0) || (agent > 1) || (resource < 0) || (resource > 2)) {
        return;
    }
    int price = xsArrayGetInt(gPrices, resource);
    int resIdx = agent * 4 + resource;
    int goldIdx = agent * 4 + 3;
    int held = xsArrayGetInt(gStock, resIdx);
    int gold = xsArrayGetInt(gStock, goldIdx);
    int drifted = 0;
    if (op == W_OP_BUY) {
        if (gold < price) {
            return;                    // can't afford: silently dropped
        }
        xsArraySetInt(gStock, goldIdx, gold - price);
        xsArraySetInt(gStock, resIdx, held + W_LOT_SIZE);
        drifted = price + W_PRICE_STEP;
        if (drifted > W_MAX_PRICE) {
            drifted = W_MAX_PRICE;
        }
        xsArraySetInt(gPrices, resource, drifted);
        xsChatData("[wololo] buy: agent %d took a lot", agent);
    } else {
        if (held < W_LOT_SIZE) {
            return;                    // nothing to sell: silently dropped
        }
        xsArraySetInt(gStock, resIdx, held - W_LOT_SIZE);
        xsArraySetInt(gStock, goldIdx, gold + price);
        drifted = price - W_PRICE_STEP;
        if (drifted < W_MIN_PRICE) {
            drifted = W_MIN_PRICE;
        }
        xsArraySetInt(gPrices, resource, drifted);
        xsChatData("[wololo] sell: agent %d sold a lot", agent);
    }
}

// Poll the command file; on a new, checksum-valid frame apply its records.
bool readCommands() {
    gTauntCount = 0;
    if (xsOpenFile("wololo_cmd") == false) return (false);
    int sum = 0;
    int magic = xsReadInt();
    int version = xsReadInt();
    int seq = xsReadInt();
    int ack = xsReadInt();
    int nrec = xsReadInt();
    sum = magic + version + seq + ack + nrec;
    if ((magic != W_MAGIC) || (version != W_VERSION) || (nrec < 0) || (nrec > W_MAX_RECORDS)) {
        xsCloseFile();
        return (false);
    }
    int i = 0;
    int j = 0;
    int rtype = 0;
    int nf = 0;
    int field = 0;
    int bad = 0;
    while ((i < nrec) && (bad == 0)) {
        rtype = xsReadInt();
        nf = xsReadInt();
        sum = sum + rtype + nf;
        if ((nf < 0) || (nf > 8)) {
            bad = 1;
        } else {
            xsArraySetInt(gRecType, i, rtype);
            xsArraySetInt(gRecF0, i, 0);
            xsArraySetInt(gRecF1, i, 0);
            xsArraySetInt(gRecF2, i, 0);
            j = 0;
            while (j < nf) {
                field = xsReadInt();
                sum = sum + field;
                if (j == 0) {
                    xsArraySetInt(gRecF0, i, field);
                }
                if (j == 1) {
                    xsArraySetInt(gRecF1, i, field);
                }
                if (j == 2) {
                    xsArraySetInt(gRecF2, i, field);
                }
                j = j + 1;
            }
        }
        i = i + 1;
    }
    int checksum = xsReadInt();
    xsCloseFile();
    if (bad == 1) {
        return (false);
    }
    if (checksum != sum) {
        return (false);                    // torn or foreign file
    }
    if (seq == gAcked) {
        return (false);                    // already applied this frame
    }
    // Fresh scenario (epoch 0): swallow leftover cmd frames from a prior
    // Python session so stale taunts do not flood chat on Test Scenario.
    if ((gEpoch == 0) && (gAcked == 0) && (seq > 0)) {
        gAcked = seq;
        xsChatData("[wololo] discarded stale command frame %d", seq);
        return (false);
    }
    i = 0;
    while (i < nrec) {
        rtype = xsArrayGetInt(gRecType, i);
        if (rtype == W_CMD_TAUNT) {
            bufferTaunt(xsArrayGetInt(gRecF0, i), xsArrayGetInt(gRecF1, i));
        }
        if (rtype == W_CMD_MARKET) {
            applyMarket(xsArrayGetInt(gRecF0, i), xsArrayGetInt(gRecF1, i), xsArrayGetInt(gRecF2, i));
        }
        i = i + 1;
    }
    gAcked = seq;
    return (true);
}

// Mirror the bridge's virtual stockpiles onto the real players' resource
// counters, so the in-game HUD shows the agents' economy live.  Agent 0 is
// player 1, agent 1 is player 2.  Attributes: 0=food 1=wood 2=stone 3=gold.
void mirrorHud() {
    int agent = 0;
    int resource = 0;
    while (agent < 2) {
        resource = 0;
        while (resource < 4) {
            xsSetPlayerAttribute(agent + 1, resource, 0.0 + xsArrayGetInt(gStock, agent * 4 + resource));
            resource = resource + 1;
        }
        agent = agent + 1;
    }
}

void putInt(int v = 0) {
    xsWriteInt(v);
    gSum = gSum + v;
}

// Rewrite the state file: prices, stockpiles, this epoch's taunt echoes.
void writeState() {
    gEpoch = gEpoch + 1;
    gSum = 0;
    xsCreateFile(false);
    putInt(W_MAGIC);
    putInt(W_VERSION);
    putInt(gEpoch);
    putInt(gAcked);
    putInt(3 + 8 + gTauntCount);   // n_records: 3 prices + 2x4 stocks + taunts
    int resource = 0;
    while (resource < 3) {
        putInt(W_ST_PRICE);
        putInt(2);
        putInt(resource);
        putInt(xsArrayGetInt(gPrices, resource));
        resource = resource + 1;
    }
    int agent = 0;
    while (agent < 2) {
        resource = 0;
        while (resource < 4) {
            putInt(W_ST_STOCK);
            putInt(3);
            putInt(agent);
            putInt(resource);
            putInt(xsArrayGetInt(gStock, agent * 4 + resource));
            resource = resource + 1;
        }
        agent = agent + 1;
    }
    int t = 0;
    while (t < gTauntCount) {
        putInt(W_ST_TAUNT);
        putInt(2);
        putInt(xsArrayGetInt(gTauntBuf, t * 2));
        putInt(xsArrayGetInt(gTauntBuf, t * 2 + 1));
        t = t + 1;
    }
    xsWriteInt(gSum);
    xsCloseFile();
}

rule wololoBridge
    active
    minInterval 2
    maxInterval 2
{
    initWololo();
    readCommands();
    mirrorHud();
    writeState();
}
