"""Plausible S65 engine values for simulators, as raw OBD-II Mode 01 data bytes."""

from __future__ import annotations

import math
import time


class SimulatedEngine:
    """Sweeps RPM between roughly 500 and 5500 and derives the other values from it."""

    def __init__(self, unsupported: set[int] | None = None) -> None:
        self._t0 = time.monotonic()
        self.unsupported = set(unsupported or ())

    def _values(self) -> dict[int, bytes]:
        t = time.monotonic() - self._t0
        rpm = 3000 + 2500 * math.sin(t / 2)
        return {
            0x04: bytes([int(rpm / 8400 * 255)]),
            0x05: bytes([90 + 40]),                    # coolant 90 °C
            0x06: bytes([128 + int(5 * math.sin(t))]),
            0x07: bytes([130]),
            0x08: bytes([128 - int(5 * math.sin(t))]),
            0x09: bytes([126]),
            0x0C: int(rpm * 4).to_bytes(2, "big"),
            0x0D: bytes([int(rpm / 60)]),
            0x0E: bytes([int((20 + 64) * 2)]),         # 20° advance
            0x0F: bytes([25 + 40]),                    # IAT 25 °C
            0x10: int(rpm / 50 * 100).to_bytes(2, "big"),
            0x11: bytes([int(rpm / 8400 * 255)]),
            0x42: (13800).to_bytes(2, "big"),          # 13.8 V
            0x46: bytes([18 + 40]),                    # ambient 18 °C
        }

    @property
    def supported(self) -> set[int]:
        return set(self._values()) - self.unsupported

    def supported_bitmap(self, base: int) -> bytes:
        """Mode 01 PID 0x00/0x20/0x40...: which of base+1..base+0x20 are supported."""
        bits = 0
        supported = self.supported
        for i in range(1, 0x21):
            pid = base + i
            if pid in supported or (i == 0x20 and any(p > base + 0x20 for p in supported)):
                bits |= 1 << (32 - i)
        return bits.to_bytes(4, "big")

    def pid(self, pid: int) -> bytes | None:
        """Data bytes for a Mode 01 request, or None if the PID isn't supported."""
        if pid % 0x20 == 0:
            return self.supported_bitmap(pid)
        if pid in self.unsupported:
            return None
        return self._values().get(pid)
