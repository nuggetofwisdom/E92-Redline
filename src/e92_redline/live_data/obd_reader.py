"""
OBD-II Mode 01 readers.

The Live Data poller only needs ``read_pid``; where the bytes come from depends
on the hardware:

* ``SocketCanObdReader`` - a SocketCAN adapter (Linux only), raw 0x7DF/0x7E8 frames.
* ``KdcanObdReader``     - a K+DCAN cable (Linux, macOS, Windows), BMW-FAST telegrams
  through the already-open connection's ``KdcanTransport``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from e92_redline.transport.bmw_fast import Frame
    from e92_redline.transport.kdcan_transport import KdcanTransport

OBD_REQUEST_ID = 0x7DF      # broadcast to all OBD-II ECUs
ENGINE_RESPONSE_ID = 0x7E8  # engine computer's reply ID


class ObdUnavailable(RuntimeError):
    """The car didn't answer OBD-II requests at all through this hardware."""


class ObdSource(Protocol):
    description: str

    def read_pid(self, pid: int, timeout: float = 0.1) -> bytes | None: ...

    def close(self) -> None: ...


def query_supported_pids(source: ObdSource, timeout: float = 0.5) -> set[int] | None:
    """
    Ask which Mode 01 PIDs the engine computer supports (PIDs 0x00, 0x20, 0x40...).

    Returns None if the car won't say, in which case callers should simply try
    every PID.
    """
    supported: set[int] = set()
    base = 0x00
    while base <= 0xE0:
        data = source.read_pid(base, timeout)
        if data is None or len(data) < 4:
            return supported or None
        bits = int.from_bytes(data[:4], "big")
        for i in range(1, 0x21):
            if bits & (1 << (32 - i)):
                supported.add(base + i)
        if base + 0x20 not in supported:
            break
        base += 0x20
    return supported


class SocketCanObdReader:
    """Mode 01 over a SocketCAN interface (can0, vcan0)."""

    def __init__(self, channel: str = "can0") -> None:
        import can  # imported here so non-Linux platforms never touch socketcan

        self._can = can
        self.description = channel
        self._bus = can.Bus(
            interface="socketcan",
            channel=channel,
            can_filters=[{"can_id": ENGINE_RESPONSE_ID, "can_mask": 0x7FF}],
        )

    def read_pid(self, pid: int, timeout: float = 0.1) -> bytes | None:
        self._bus.send(self._can.Message(
            arbitration_id=OBD_REQUEST_ID,
            data=[0x02, 0x01, pid, 0, 0, 0, 0, 0],  # length, mode 01, PID, padding
            is_extended_id=False,
        ))
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            msg = self._bus.recv(remaining)
            if msg is None:
                break
            d = msg.data
            if len(d) >= 3 and d[1] == 0x41 and d[2] == pid:
                return bytes(d[3 : 1 + d[0]])  # d[0] counts mode + PID + data bytes
        return None

    def close(self) -> None:
        self._bus.shutdown()

    def __enter__(self) -> SocketCanObdReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# Kept for existing imports.
ObdReader = SocketCanObdReader


# How a Mode 01 request can reach the engine computer through a K+DCAN cable,
# tried in order until one answers: (label, target address, functional?).
KDCAN_OBD_ROUTES: tuple[tuple[str, int, bool], ...] = (
    ("DME physical 0x12", 0x12, False),
    ("OBD functional 0x33", 0x33, True),
    ("BMW functional 0xDF", 0xDF, True),
)


class KdcanObdReader:
    """
    Mode 01 through a K+DCAN cable.

    The cable only forwards BMW-FAST telegrams, so the request is wrapped as
    ``[fmt] [target] F1 01 <pid> [cs]``. Which target the MSS60 answers on is
    found once by trying ``KDCAN_OBD_ROUTES``; the winner is reused.
    The transport belongs to the connection, so ``close`` leaves it open.
    """

    def __init__(self, transport: KdcanTransport, probe_timeout: float = 0.5) -> None:
        self._transport = transport
        self._probe_timeout = probe_timeout
        self._route: tuple[str, int, bool] | None = None
        self.description = f"K+DCAN {transport.port or ''}".strip()

    @property
    def route(self) -> str | None:
        return self._route[0] if self._route else None

    def _ask(self, pid: int, target: int, functional: bool, timeout: float) -> Frame | None:
        def accept(f: Frame) -> bool:
            p = f.payload
            if f.target != 0xF1 or len(p) < 2:
                return False
            if p[0] == 0x41 and p[1] == pid:
                return True
            return p[0] == 0x7F and p[1] == 0x01  # negative reply to Mode 01

        return self._transport.request(
            bytes([0x01, pid]), target=target, functional=functional,
            timeout=timeout, accept=accept,
        )

    def resolve_route(self) -> str:
        """Find how the engine computer answers OBD-II; raises ObdUnavailable."""
        if self._route:
            return self._route[0]
        for probe_pid in (0x00, 0x0C):
            for route in KDCAN_OBD_ROUTES:
                reply = self._ask(probe_pid, route[1], route[2], self._probe_timeout)
                if reply is not None and reply.payload[0] == 0x41:
                    self._route = route
                    self.description = f"{self.description} via {route[0]}"
                    return route[0]
        ignition = self._transport.ignition_on()
        hint = "Is the ignition on?" if ignition is not True else (
            "The cable is talking but no module answered OBD-II requests."
        )
        raise ObdUnavailable(f"No OBD-II answer through the K+DCAN cable. {hint}")

    def read_pid(self, pid: int, timeout: float = 0.15) -> bytes | None:
        if self._route is None:
            self.resolve_route()
        assert self._route is not None
        reply = self._ask(pid, self._route[1], self._route[2], timeout)
        if reply is None or reply.payload[0] != 0x41:
            return None
        return reply.payload[2:]

    def close(self) -> None:
        pass
