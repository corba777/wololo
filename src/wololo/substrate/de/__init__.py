"""AoE II DE bridge — Milestone 3: the substrate becomes the real game.

Offline-complete skeleton: the file-mailbox protocol, the ``DeSubstrate``
implementation, and a fake game process standing in for the XS side.  The
in-game half (scenario + XS script) is documented in ``docs/de_bridge.md``
and pends a smoke test on a machine with the game installed.
"""

from __future__ import annotations

from wololo.substrate.de.bridge import DeBridgeError, DeSubstrate
from wololo.substrate.de.fakegame import FakeDeGame
from wololo.substrate.de.mailbox import FileMailbox
from wololo.substrate.de.protocol import Frame, ProtocolError, Record

__all__ = [
    "DeBridgeError",
    "DeSubstrate",
    "FakeDeGame",
    "FileMailbox",
    "Frame",
    "ProtocolError",
    "Record",
]
