"""Taunt codec — the only agent↔agent wire format.

Game analogy: agents can shout only the 105 numbered taunts ("1: Yes",
"2: No", ..., "30: Wololo", ..., "105: You can resign again").  Anything
structured they want to say must be spelled out as a sequence of taunt
numbers.

CS meaning: a self-delimiting serialization of structured messages over a
105-symbol alphabet.  Taunt 105 is reserved as the end-of-message marker,
the remaining 104 symbols carry data as varint chunks:

* taunt ``t`` maps to digit ``d = t - 1`` (0..104);
* digit 104 (taunt 105) terminates a message frame;
* digits 0..51 encode a final varint chunk, digits 52..103 encode a chunk
  with a continuation bit (little-endian base-52);
* signed integers are zigzag-mapped to unsigned before chunking.

A message ``Message(kind, args)`` is framed as::

    varint(kind) varint(len(args)) varint(arg0) ... varint(argN) END

so a flat stream of taunts from many senders can be split back into
messages with no out-of-band length information.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TAUNT_MIN = 1
TAUNT_MAX = 105

#: Taunt reserved as the end-of-message frame marker.
END_TAUNT = 105

_END_DIGIT = END_TAUNT - 1  # 104
_DATA_BASE = 104  # digits 0..103 carry data
_CHUNK = _DATA_BASE // 2  # 52: chunk values per varint digit


class TauntDecodeError(ValueError):
    """Raised when a taunt sequence is not a valid encoding."""


@dataclass(frozen=True, slots=True)
class Message:
    """A structured message: a verb (`kind`) plus integer arguments.

    Game analogy: "kind" is *which* taunt-phrase family you mean ("attack",
    "need resource", ...), args are the specifics (resource id, amount, ...).
    """

    kind: int
    args: tuple[int, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.kind < 0:
            raise ValueError(f"kind must be non-negative, got {self.kind}")
        object.__setattr__(self, "args", tuple(self.args))


def _encode_uint(value: int, out: list[int]) -> None:
    """Append little-endian base-52 varint digits (as taunts) for ``value >= 0``."""
    while True:
        chunk = value % _CHUNK
        value //= _CHUNK
        if value == 0:
            out.append(chunk + 1)  # final chunk: digit 0..51 -> taunt 1..52
            return
        out.append(_CHUNK + chunk + 1)  # continuation: digit 52..103 -> taunt 53..104


def _zigzag(value: int) -> int:
    return value * 2 if value >= 0 else -value * 2 - 1


def _unzigzag(value: int) -> int:
    return value // 2 if value % 2 == 0 else -(value + 1) // 2


def encode_message(message: Message) -> list[int]:
    """Serialize one message to a taunt sequence, END_TAUNT-terminated."""
    out: list[int] = []
    _encode_uint(message.kind, out)
    _encode_uint(len(message.args), out)
    for arg in message.args:
        _encode_uint(_zigzag(arg), out)
    out.append(END_TAUNT)
    return out


class _Reader:
    """Cursor over a taunt sequence with validation."""

    def __init__(self, taunts: list[int] | tuple[int, ...]) -> None:
        self._taunts = taunts
        self._pos = 0

    @property
    def pos(self) -> int:
        return self._pos

    def exhausted(self) -> bool:
        return self._pos >= len(self._taunts)

    def _next_digit(self) -> int:
        if self.exhausted():
            raise TauntDecodeError(f"truncated sequence at position {self._pos}")
        taunt = self._taunts[self._pos]
        if not isinstance(taunt, int) or isinstance(taunt, bool):
            raise TauntDecodeError(f"non-integer taunt at position {self._pos}: {taunt!r}")
        if not TAUNT_MIN <= taunt <= TAUNT_MAX:
            raise TauntDecodeError(f"taunt {taunt} out of range 1..105 at position {self._pos}")
        self._pos += 1
        return taunt - 1

    def read_uint(self) -> int:
        value = 0
        shift = 1
        while True:
            digit = self._next_digit()
            if digit == _END_DIGIT:
                raise TauntDecodeError(
                    f"unexpected end-of-message marker inside varint at position {self._pos - 1}"
                )
            if digit < _CHUNK:
                return value + digit * shift
            value += (digit - _CHUNK) * shift
            shift *= _CHUNK

    def read_end_marker(self) -> None:
        digit = self._next_digit()
        if digit != _END_DIGIT:
            raise TauntDecodeError(
                f"expected end-of-message taunt {END_TAUNT} at position {self._pos - 1},"
                f" got taunt {digit + 1}"
            )

    def read_message(self) -> Message:
        kind = self.read_uint()
        argc = self.read_uint()
        args = tuple(_unzigzag(self.read_uint()) for _ in range(argc))
        self.read_end_marker()
        return Message(kind, args)


def decode_message(taunts: list[int] | tuple[int, ...]) -> Message:
    """Decode exactly one message; trailing taunts are an error."""
    reader = _Reader(taunts)
    message = reader.read_message()
    if not reader.exhausted():
        raise TauntDecodeError(f"trailing taunts after message at position {reader.pos}")
    return message


def decode_stream(taunts: list[int] | tuple[int, ...]) -> list[Message]:
    """Split a flat taunt stream into consecutive messages.

    Game analogy: reading the whole chat log for a tick and recovering every
    structured message that was shouted into it.
    """
    reader = _Reader(taunts)
    messages: list[Message] = []
    while not reader.exhausted():
        messages.append(reader.read_message())
    return messages


def split_frames(taunts: list[int] | tuple[int, ...]) -> tuple[list[Message], list[int]]:
    """Decode every *complete* frame in a buffer; return (messages, remainder).

    Game analogy: a listener keeps a running buffer of what a player has
    shouted so far; whenever an end-of-message taunt lands, one full message
    is recovered and the tail (a message still being shouted) is kept for
    later.  CS meaning: incremental frame extraction for streaming decode —
    partial trailing input is not an error, unlike ``decode_stream``.
    """
    buf = list(taunts)
    messages: list[Message] = []
    start = 0
    for i, taunt in enumerate(buf):
        if taunt == END_TAUNT:
            messages.append(decode_message(buf[start : i + 1]))
            start = i + 1
    return messages, buf[start:]
