"""File mailbox — the transport under the DE bridge.

Game analogy: two dead-drop satchels in the player's profile folder: the
orchestrator leaves orders in one, the game leaves reports in the other;
each side polls for a newer seal number.  CS meaning: half-duplex message
exchange over two single-writer files.  Our side writes atomically
(temp + rename); the XS side rewrites its file each rule tick, so readers
must tolerate torn content — the frame checksum rejects it and we simply
poll again.
"""

from __future__ import annotations

from pathlib import Path

from wololo.substrate.de.protocol import Frame, ProtocolError, decode_frame, encode_frame
from wololo.substrate.de.xsdat import XsdatError, read_ints, write_ints


class FileMailbox:
    """One endpoint: sends frames to ``send_path``, polls ``recv_path``."""

    def __init__(self, send_path: Path, recv_path: Path) -> None:
        self.send_path = send_path
        self.recv_path = recv_path
        self._last_seen_seq: int | None = None

    def send(self, frame: Frame) -> None:
        self.send_path.parent.mkdir(parents=True, exist_ok=True)
        write_ints(self.send_path, encode_frame(frame))

    def try_receive(self) -> Frame | None:
        """Return the incoming frame if it is new and intact, else None.

        Torn or half-written files are treated as "nothing yet": the writer
        will finish (or rewrite) and a later poll will pick it up.
        """
        if not self.recv_path.exists():
            return None
        try:
            frame = decode_frame(read_ints(self.recv_path))
        except (XsdatError, ProtocolError, OSError):
            return None
        if self._last_seen_seq is not None and frame.seq <= self._last_seen_seq:
            return None
        self._last_seen_seq = frame.seq
        return frame
