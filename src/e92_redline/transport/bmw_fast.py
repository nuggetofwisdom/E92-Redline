"""
BMW-FAST telegram framing (KWP2000-style), as spoken by K+DCAN cables.

In D-CAN mode a K+DCAN cable is a USB-serial port (115200 8N1). Its
microcontroller turns each BMW-FAST telegram written to the port into
ISO-TP frames on the car's diagnostic CAN bus (tester 0x6F1, ECU 0x600 + addr),
and turns the ECU's reply back into a telegram on the serial side.

Telegram layout::

    [fmt] [target] [source] ([len] | [0x00 lenH lenL]) [payload ...] [checksum]

* ``fmt`` bits 7..6 are the addressing mode: ``10`` physical, ``11`` functional.
  Bits 5..0 hold the payload length when it is 1..63, otherwise 0 and the length
  follows the source byte (one byte up to 255, ``00 hi lo`` beyond that).
* ``checksum`` is the 8-bit *sum* of every preceding byte (not XOR).

Reference: EdiabasLib ``TelLengthBmwFast`` / ``CalcChecksumBmwFast``.
"""

from __future__ import annotations

from dataclasses import dataclass

TESTER_ADDRESS = 0xF1
DME_ADDRESS = 0x12  # MSS60 engine computer on the S65

FMT_PHYSICAL = 0x80
FMT_FUNCTIONAL = 0xC0
_FMT_MODE_MASK = 0xC0


class FrameError(ValueError):
    """A telegram could not be built or parsed."""


def checksum(data: bytes | bytearray) -> int:
    """8-bit additive checksum used by BMW-FAST."""
    return sum(data) & 0xFF


@dataclass(frozen=True)
class Frame:
    """One decoded BMW-FAST telegram."""

    target: int
    source: int
    payload: bytes
    functional: bool = False

    @property
    def sid(self) -> int | None:
        """First payload byte (service ID / response ID), if any."""
        return self.payload[0] if self.payload else None

    def is_response_pending(self) -> bool:
        """True for a 0x7F <sid> 0x78 'request received, response pending' reply."""
        return len(self.payload) == 3 and self.payload[0] == 0x7F and self.payload[2] == 0x78

    def encode(self) -> bytes:
        return build_frame(self.target, self.payload, self.source, self.functional)


def build_frame(
    target: int,
    payload: bytes,
    source: int = TESTER_ADDRESS,
    functional: bool = False,
) -> bytes:
    """Encode ``payload`` as a complete BMW-FAST telegram including checksum."""
    n = len(payload)
    if n == 0:
        raise FrameError("payload must not be empty")
    if n > 0xFFFF:
        raise FrameError(f"payload too long ({n} bytes)")
    mode = FMT_FUNCTIONAL if functional else FMT_PHYSICAL
    if n <= 0x3F:
        head = bytes([mode | n, target & 0xFF, source & 0xFF])
    elif n <= 0xFF:
        head = bytes([mode, target & 0xFF, source & 0xFF, n])
    else:
        head = bytes([mode, target & 0xFF, source & 0xFF, 0x00, n >> 8, n & 0xFF])
    body = head + bytes(payload)
    return body + bytes([checksum(body)])


def _header_info(buf: bytes | bytearray) -> tuple[int, int] | None:
    """Return (header length, payload length) once enough bytes are buffered."""
    if len(buf) < 3:
        return None
    n = buf[0] & 0x3F
    if n:
        return 3, n
    if len(buf) < 4:
        return None
    if buf[3]:
        return 4, buf[3]
    if len(buf) < 6:
        return None
    return 6, (buf[4] << 8) | buf[5]


def parse_frame(data: bytes) -> Frame:
    """Decode exactly one complete telegram (checksum verified)."""
    parser = FrameParser()
    frames = parser.feed(data)
    if len(frames) != 1 or parser.pending:
        raise FrameError(f"not exactly one telegram: {data.hex()}")
    return frames[0]


class FrameParser:
    """
    Incremental decoder for a byte stream of BMW-FAST telegrams.

    Serial reads arrive in arbitrary chunks; ``feed`` returns every telegram
    completed so far and keeps the remainder. Bytes that cannot start a
    telegram (format byte without bit 7 set) are skipped, and a telegram with a
    bad checksum is dropped and counted in ``bad_frames`` so a single glitch
    does not wedge the stream.

    After such a glitch the parser is out of step: a stray byte can look like a
    header announcing a long telegram that never comes. While resynchronising it
    therefore jumps ahead to the next complete telegram with a valid checksum
    instead of waiting. (It doesn't do that in normal operation, where a long
    reply arriving in chunks must not be mistaken for a short one.)
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._resync = False
        self.bad_frames = 0
        self.skipped_bytes = 0

    @property
    def pending(self) -> int:
        return len(self._buf)

    def clear(self) -> None:
        self._buf.clear()
        self._resync = False

    @staticmethod
    def _complete_at(buf: bytearray, start: int) -> int | None:
        """Length of a complete, checksum-valid telegram at ``start``, else None."""
        view = buf[start:]
        if not view or not view[0] & 0x80:
            return None
        info = _header_info(view)
        if info is None or info[1] == 0:
            return None
        total = info[0] + info[1] + 1
        if len(view) < total or checksum(view[: total - 1]) != view[total - 1]:
            return None
        return total

    def feed(self, data: bytes | bytearray) -> list[Frame]:
        self._buf.extend(data)
        frames: list[Frame] = []
        while self._buf:
            if not self._buf[0] & 0x80:
                del self._buf[0]
                self.skipped_bytes += 1
                self._resync = True
                continue
            info = _header_info(self._buf)
            if info is None:
                break
            head_len, n = info
            if n == 0:  # a zero-length telegram is never valid
                del self._buf[0]
                self.skipped_bytes += 1
                self._resync = True
                continue
            total = head_len + n + 1
            if len(self._buf) < total:
                if self._resync and self._skip_to_next_complete():
                    continue
                break
            raw = bytes(self._buf[:total])
            if checksum(raw[:-1]) != raw[-1]:
                # Resynchronise one byte later rather than discarding the lot.
                del self._buf[0]
                self.bad_frames += 1
                self._resync = True
                continue
            del self._buf[:total]
            self._resync = False
            frames.append(
                Frame(
                    target=raw[1],
                    source=raw[2],
                    payload=raw[head_len:-1],
                    functional=(raw[0] & _FMT_MODE_MASK) == FMT_FUNCTIONAL,
                )
            )
        return frames

    def _skip_to_next_complete(self) -> bool:
        for start in range(1, len(self._buf)):
            if self._complete_at(self._buf, start):
                del self._buf[:start]
                self.skipped_bytes += start
                return True
        return False
