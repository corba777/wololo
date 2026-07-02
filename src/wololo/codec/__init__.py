"""Codec layer — structured messages ⇄ taunt sequences."""

from __future__ import annotations

from wololo.codec.tauntcodec import (
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

__all__ = [
    "END_TAUNT",
    "TAUNT_MAX",
    "TAUNT_MIN",
    "Message",
    "TauntDecodeError",
    "decode_message",
    "decode_stream",
    "encode_message",
    "split_frames",
]
