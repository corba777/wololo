"""``.xsdat`` codec — the physical layer of the DE bridge.

Game analogy: the courier's saddlebag: the only thing the game can carry in
and out is a small satchel of numbers.  CS meaning: DE's XS runtime does
file I/O via ``xsWriteInt``/``xsReadInt`` on ``.xsdat`` files in the player
profile folder (1 MB max).  We model those files as flat sequences of
little-endian signed 32-bit integers; byte order must be confirmed by the
in-game smoke test (see docs/de_bridge.md) and is isolated here so a flip
is a one-line change.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

#: XS caps .xsdat files at 1 MB.
MAX_BYTES = 1_000_000

_INT = struct.Struct("<i")

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


class XsdatError(ValueError):
    """Raised for malformed .xsdat content or out-of-range values."""


def write_ints(path: Path, values: list[int]) -> None:
    """Atomically write ints as an .xsdat file (temp file + rename)."""
    for value in values:
        if not INT32_MIN <= value <= INT32_MAX:
            raise XsdatError(f"value {value} does not fit in int32")
    data = b"".join(_INT.pack(value) for value in values)
    if len(data) > MAX_BYTES:
        raise XsdatError(f"{len(data)} bytes exceeds the {MAX_BYTES} byte .xsdat cap")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def read_ints(path: Path) -> list[int]:
    """Read an .xsdat file as a list of int32s; raise XsdatError if torn."""
    data = path.read_bytes()
    if len(data) % _INT.size != 0:
        raise XsdatError(f"{path.name}: {len(data)} bytes is not a whole number of int32s")
    return [_INT.unpack_from(data, offset)[0] for offset in range(0, len(data), _INT.size)]
