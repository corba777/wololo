"""DE bridge wire-level tests — .xsdat codec and frame protocol."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from wololo.substrate.de.protocol import (
    Frame,
    ProtocolError,
    Record,
    decode_frame,
    encode_frame,
)
from wololo.substrate.de.xsdat import XsdatError, read_ints, write_ints


def test_xsdat_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "probe.xsdat"
    values = [0, 1, -1, 2**31 - 1, -(2**31), 41186]
    write_ints(path, values)
    assert read_ints(path) == values


def test_xsdat_rejects_oversized_values(tmp_path: Path) -> None:
    with pytest.raises(XsdatError, match="int32"):
        write_ints(tmp_path / "x.xsdat", [2**31])


def test_xsdat_rejects_torn_file(tmp_path: Path) -> None:
    path = tmp_path / "torn.xsdat"
    write_ints(path, [1, 2, 3])
    path.write_bytes(path.read_bytes()[:-2])  # tear off half an int
    with pytest.raises(XsdatError, match="whole number"):
        read_ints(path)


def test_frame_round_trip() -> None:
    frame = Frame(
        seq=7,
        ack=6,
        records=(Record(1, (0, 31)), Record(2, (1, 1, 2)), Record(3, ())),
    )
    assert decode_frame(encode_frame(frame)) == frame


def test_frame_round_trip_property() -> None:
    rng = random.Random(0xDE)
    for _ in range(300):
        records = tuple(
            Record(
                rng.randrange(1, 5),
                tuple(rng.randrange(0, 1000) for _ in range(rng.randrange(0, 5))),
            )
            for _ in range(rng.randrange(0, 6))
        )
        frame = Frame(seq=rng.randrange(1, 10**6), ack=rng.randrange(0, 10**6), records=records)
        assert decode_frame(encode_frame(frame)) == frame


def test_frame_rejects_corruption() -> None:
    wire = encode_frame(Frame(seq=1, ack=0, records=(Record(1, (0, 31)),)))
    for i in range(len(wire)):
        corrupted = list(wire)
        corrupted[i] += 1
        with pytest.raises(ProtocolError):
            decode_frame(corrupted)


def test_frame_rejects_truncation() -> None:
    wire = encode_frame(Frame(seq=1, ack=0, records=(Record(1, (0, 31)), Record(2, (1, 0, 1)))))
    for cut in range(len(wire)):
        with pytest.raises(ProtocolError):
            decode_frame(wire[:cut])


def test_frame_rejects_wrong_magic_and_version() -> None:
    wire = encode_frame(Frame(seq=1, ack=0, records=()))
    bad_magic = [99, *wire[1:]]
    with pytest.raises(ProtocolError, match="magic"):
        decode_frame(bad_magic)
