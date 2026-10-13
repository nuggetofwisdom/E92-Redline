"""
Simulated K+DCAN cable with an engine computer behind it.

``FakeSerial`` looks enough like a ``serial.Serial`` for ``KdcanTransport``, so
the whole cable path (framing, echo, response-pending, OBD addressing) can be
exercised on any OS without hardware. Open the port name ``sim://kdcan`` in the
app (``e92-redline --simulate``) to drive the Live Data page from it.
"""

from __future__ import annotations

import threading
import time

from e92_redline.sim.engine import SimulatedEngine
from e92_redline.transport.bmw_fast import (
    DME_ADDRESS,
    TESTER_ADDRESS,
    FrameParser,
    build_frame,
)

SIM_PORT = "sim://kdcan"
SIM_VIN = "SIMULATEDE92M3000"
OBD_FUNCTIONAL_ADDRESSES = (0x33, 0xDF)


class SimulatedKdcanCable:
    """Turns telegrams written to the 'cable' into the bytes the cable would send back."""

    def __init__(
        self,
        engine: SimulatedEngine | None = None,
        echo: bool = True,
        physical_obd: bool = True,
        functional_obd: bool = True,
        pending_first: bool = False,
        silent_unsupported: bool = False,
    ) -> None:
        self.engine = engine or SimulatedEngine()
        self.echo = echo
        self.physical_obd = physical_obd
        self.functional_obd = functional_obd
        self.pending_first = pending_first  # reply 7F xx 78 before every answer
        self.silent_unsupported = silent_unsupported
        self.ignition = True
        self.requests: list[bytes] = []
        self._parser = FrameParser()

    def process(self, data: bytes) -> bytes:
        out = bytearray()
        if self.echo:
            out += data
        for frame in self._parser.feed(data):
            self.requests.append(frame.payload)
            for reply in self._answer(frame.target, frame.functional, frame.payload):
                out += build_frame(TESTER_ADDRESS, reply, DME_ADDRESS)
        return bytes(out)

    def _answer(self, target: int, functional: bool, payload: bytes) -> list[bytes]:
        if not self.ignition:
            return []
        if functional:
            if target not in OBD_FUNCTIONAL_ADDRESSES:
                return []
        elif target != DME_ADDRESS:
            return []

        sid = payload[0]
        replies: list[bytes] = []
        if self.pending_first:
            replies.append(bytes([0x7F, sid, 0x78]))

        if sid == 0x01 and len(payload) >= 2:
            if (functional and not self.functional_obd) or (not functional and not self.physical_obd):
                return []
            data = self.engine.pid(payload[1])
            if data is None:
                if self.silent_unsupported:
                    return []
                replies.append(bytes([0x7F, 0x01, 0x12]))
            else:
                replies.append(bytes([0x41, payload[1]]) + data)
        elif sid == 0x3E:
            replies.append(bytes([0x7E]) + payload[1:2])
        elif sid == 0x1A and payload[1:2] == b"\x90":  # KWP ReadECUIdentification: VIN
            replies.append(bytes([0x5A, 0x90]) + SIM_VIN.encode())
        else:
            replies.append(bytes([0x7F, sid, 0x11]))  # service not supported
        return replies


class FakeSerial:
    """Minimal pyserial stand-in backed by a SimulatedKdcanCable."""

    def __init__(self, cable: SimulatedKdcanCable | None = None, latency: float = 0.002) -> None:
        self.cable = cable or SimulatedKdcanCable()
        self.latency = latency
        self.timeout: float | None = 0.02
        self.dtr = False
        self.rts = False
        self.is_open = True
        self._rx = bytearray()
        self._cv = threading.Condition()

    @property
    def dsr(self) -> bool:
        return self.cable.ignition

    @property
    def in_waiting(self) -> int:
        with self._cv:
            return len(self._rx)

    def write(self, data: bytes) -> int:
        reply = self.cable.process(bytes(data))
        if self.latency:
            time.sleep(self.latency)
        with self._cv:
            self._rx += reply
            self._cv.notify_all()
        return len(data)

    def read(self, size: int = 1) -> bytes:
        with self._cv:
            if not self._rx and self.timeout:
                self._cv.wait(self.timeout)
            chunk = bytes(self._rx[:size])
            del self._rx[:size]
            return chunk

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        with self._cv:
            self._rx.clear()

    def reset_output_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False
