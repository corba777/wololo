"""Bridge wire protocol — command and state frames as flat int sequences.

Game analogy: the orchestrator and the game exchange sealed dispatches:
one satchel of orders going in, one satchel of reports coming out, each
stamped with a running seal number.  CS meaning: a versioned, self-checking
frame format over int32 sequences (the only thing XS file I/O speaks).

Frame layout (all int32)::

    MAGIC VERSION seq ack n_records  [record]*  CHECKSUM

    record := type n_fields field*

``seq`` is the sender's frame number; ``ack`` is the highest frame number
the sender has processed from the other side.  The checksum is a simple
sum-mod-2^31 of everything before it — cheap enough for XS to compute,
enough to reject torn reads.

Record types (commands, orchestrator → game):
    CMD_TAUNT  = 1: [agent, taunt]
    CMD_MARKET = 2: [agent, op(0=buy, 1=sell), resource]

Record types (state, game → orchestrator):
    ST_PRICE  = 1: [resource, price]
    ST_STOCK  = 2: [agent, resource, amount]
    ST_TAUNT  = 3: [sender, taunt]        # heard during the last epoch

Resource ids: 0=food, 1=wood, 2=stone, 3=gold.
"""

from __future__ import annotations

from dataclasses import dataclass

MAGIC = 0xA0E2  # 41186
VERSION = 1

#: Sanity cap on records per frame; must match W_MAX_RECORDS in wololo.xs.
#: Sized for text-over-taunts (a ~100-char claim is ~210 taunt records).
MAX_RECORDS = 256

CMD_TAUNT = 1
CMD_MARKET = 2

ST_PRICE = 1
ST_STOCK = 2
ST_TAUNT = 3

OP_BUY = 0
OP_SELL = 1

#: Resource ids on the wire (gold is a stock, never a price).
RESOURCE_IDS: dict[str, int] = {"food": 0, "wood": 1, "stone": 2, "gold": 3}
RESOURCE_NAMES: dict[int, str] = {v: k for k, v in RESOURCE_IDS.items()}

_CHECKSUM_MOD = 2**31


class ProtocolError(ValueError):
    """Raised when a frame does not decode (torn write, version skew, noise)."""


@dataclass(frozen=True, slots=True)
class Record:
    type: int
    fields: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Frame:
    seq: int
    ack: int
    records: tuple[Record, ...]


def encode_frame(frame: Frame) -> list[int]:
    out = [MAGIC, VERSION, frame.seq, frame.ack, len(frame.records)]
    for record in frame.records:
        out.extend((record.type, len(record.fields), *record.fields))
    out.append(sum(out) % _CHECKSUM_MOD)
    return out


def decode_frame(values: list[int]) -> Frame:
    if len(values) < 6:
        raise ProtocolError(f"frame too short: {len(values)} ints")
    if values[0] != MAGIC:
        raise ProtocolError(f"bad magic {values[0]:#x}")
    if values[1] != VERSION:
        raise ProtocolError(f"version {values[1]} != {VERSION}")
    if sum(values[:-1]) % _CHECKSUM_MOD != values[-1]:
        raise ProtocolError("checksum mismatch (torn write?)")
    seq, ack, n_records = values[2], values[3], values[4]
    records: list[Record] = []
    pos = 5
    body_end = len(values) - 1
    for _ in range(n_records):
        if pos + 2 > body_end:
            raise ProtocolError("truncated record header")
        rec_type, n_fields = values[pos], values[pos + 1]
        pos += 2
        if n_fields < 0 or pos + n_fields > body_end:
            raise ProtocolError("truncated record fields")
        records.append(Record(rec_type, tuple(values[pos : pos + n_fields])))
        pos += n_fields
    if pos != body_end:
        raise ProtocolError(f"{body_end - pos} trailing ints after records")
    return Frame(seq=seq, ack=ack, records=tuple(records))
