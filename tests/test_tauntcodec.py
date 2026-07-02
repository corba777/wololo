"""Round-trip and adversarial tests for the taunt codec."""

from __future__ import annotations

import contextlib
import random

import pytest

from wololo.codec import (
    END_TAUNT,
    TAUNT_MAX,
    TAUNT_MIN,
    Message,
    TauntDecodeError,
    decode_message,
    decode_stream,
    encode_message,
    split_frames,
)

# ---------------------------------------------------------------------------
# Round-trip properties
# ---------------------------------------------------------------------------


def _random_message(rng: random.Random) -> Message:
    kind = rng.choice([0, 1, 2, rng.randrange(10_000), rng.randrange(10**12)])
    argc = rng.randrange(0, 8)
    args = tuple(
        rng.choice(
            [
                0,
                1,
                -1,
                rng.randrange(-100, 100),
                rng.randrange(-(10**9), 10**9),
                rng.randrange(-(10**18), 10**18),
            ]
        )
        for _ in range(argc)
    )
    return Message(kind, args)


def test_roundtrip_property() -> None:
    rng = random.Random(0xA0E2)
    for _ in range(2000):
        msg = _random_message(rng)
        taunts = encode_message(msg)
        assert decode_message(taunts) == msg


def test_roundtrip_simple_cases() -> None:
    for msg in [
        Message(0),
        Message(0, (0,)),
        Message(1, (1, -1)),
        Message(104, (51, 52, -52, 103, 104, 105)),
        Message(10**30, (-(10**30),)),
    ]:
        assert decode_message(encode_message(msg)) == msg


def test_encoded_taunts_are_valid_game_taunts() -> None:
    rng = random.Random(0xBEEF)
    for _ in range(500):
        taunts = encode_message(_random_message(rng))
        assert all(TAUNT_MIN <= t <= TAUNT_MAX for t in taunts)
        assert taunts[-1] == END_TAUNT
        # END is the frame delimiter: it must appear exactly once, at the end.
        assert taunts.count(END_TAUNT) == 1


def test_encoding_is_deterministic_and_unique() -> None:
    """Distinct messages never collide on the wire (injective encoding)."""
    rng = random.Random(0xCAFE)
    seen: dict[tuple[int, ...], Message] = {}
    for _ in range(1000):
        msg = _random_message(rng)
        wire = tuple(encode_message(msg))
        assert wire == tuple(encode_message(msg))
        if wire in seen:
            assert seen[wire] == msg
        seen[wire] = msg


# ---------------------------------------------------------------------------
# Stream framing
# ---------------------------------------------------------------------------


def test_stream_roundtrip_property() -> None:
    rng = random.Random(0xD00D)
    for _ in range(200):
        msgs = [_random_message(rng) for _ in range(rng.randrange(0, 6))]
        stream: list[int] = []
        for msg in msgs:
            stream.extend(encode_message(msg))
        assert decode_stream(stream) == msgs


def test_stream_empty() -> None:
    assert decode_stream([]) == []


def test_decode_message_rejects_concatenated_messages() -> None:
    two = encode_message(Message(1)) + encode_message(Message(2))
    with pytest.raises(TauntDecodeError, match="trailing"):
        decode_message(two)


def test_split_frames_extracts_complete_and_keeps_partial() -> None:
    rng = random.Random(0xFACE)
    for _ in range(200):
        msgs = [_random_message(rng) for _ in range(rng.randrange(0, 4))]
        stream: list[int] = []
        for msg in msgs:
            stream.extend(encode_message(msg))
        partial = encode_message(_random_message(rng))
        cut = rng.randrange(1, len(partial) + 1)
        tail = partial[: cut - 1]  # strictly incomplete: drop at least END
        decoded, remainder = split_frames(stream + tail)
        assert decoded == msgs
        assert remainder == tail


def test_split_frames_empty_and_exact() -> None:
    assert split_frames([]) == ([], [])
    wire = encode_message(Message(5, (1, 2)))
    assert split_frames(wire) == ([Message(5, (1, 2))], [])


# ---------------------------------------------------------------------------
# Adversarial inputs (taunt flood / garbage on the channel)
# ---------------------------------------------------------------------------


def test_truncated_sequence_rejected() -> None:
    taunts = encode_message(Message(12345, (67, -89)))
    for cut in range(len(taunts)):
        with pytest.raises(TauntDecodeError):
            decode_message(taunts[:cut])


def test_out_of_range_taunts_rejected() -> None:
    for bad in [0, -1, 106, 999]:
        with pytest.raises(TauntDecodeError, match="out of range"):
            decode_message([bad])


def test_non_integer_taunts_rejected() -> None:
    with pytest.raises(TauntDecodeError, match="non-integer"):
        decode_message([1, "wololo", END_TAUNT])  # type: ignore[list-item]
    with pytest.raises(TauntDecodeError, match="non-integer"):
        decode_message([True, END_TAUNT])  # type: ignore[list-item]


def test_end_marker_inside_varint_rejected() -> None:
    # A continuation digit (taunt 53..104) promises another digit; END breaks it.
    with pytest.raises(TauntDecodeError, match="end-of-message"):
        decode_message([53, END_TAUNT])


def test_bare_end_marker_rejected() -> None:
    with pytest.raises(TauntDecodeError):
        decode_message([END_TAUNT])


def test_random_garbage_never_crashes_decoder() -> None:
    """Any taunt sequence either decodes or raises TauntDecodeError — nothing else."""
    rng = random.Random(0xF00D)
    for _ in range(2000):
        garbage = [rng.randrange(TAUNT_MIN, TAUNT_MAX + 1) for _ in range(rng.randrange(0, 30))]
        with contextlib.suppress(TauntDecodeError):
            decode_stream(garbage)


def test_fuzz_mutated_valid_messages() -> None:
    """Bit-flip style mutations of valid wire data must decode or raise cleanly."""
    rng = random.Random(0x5EED)
    for _ in range(500):
        taunts = encode_message(_random_message(rng))
        i = rng.randrange(len(taunts))
        mutated = list(taunts)
        mutated[i] = rng.randrange(TAUNT_MIN, TAUNT_MAX + 1)
        with contextlib.suppress(TauntDecodeError):
            decode_stream(mutated)


# ---------------------------------------------------------------------------
# Message validation
# ---------------------------------------------------------------------------


def test_negative_kind_rejected() -> None:
    with pytest.raises(ValueError, match="kind"):
        Message(-1)


def test_args_normalized_to_tuple() -> None:
    msg = Message(1, [1, 2, 3])  # type: ignore[arg-type]
    assert msg.args == (1, 2, 3)
    assert decode_message(encode_message(msg)) == msg
