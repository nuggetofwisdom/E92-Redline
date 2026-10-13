"""
K+DCAN cable transport (Linux, macOS and Windows).

A K+DCAN cable (Bimmergeeks, generic FTDI FT232RL "INPA" cables, ...) shows up
as an ordinary USB serial port:

* Linux    ``/dev/ttyUSB0`` (kernel ``ftdi_sio`` driver, no install needed)
* macOS    ``/dev/cu.usbserial-XXXXXXXX`` (Apple's built-in FTDI driver)
* Windows  ``COM3`` etc. (FTDI VCP driver, installed automatically by Windows Update)

so pyserial is the only dependency and the same code runs everywhere.

In D-CAN mode (every E9x from 2007 on, including the E92 M3) the cable speaks
BMW-FAST telegrams at 115200 8N1 and bridges them to the car's diagnostic CAN
bus. See ``bmw_fast.py`` for the framing. Behaviour mirrored from EdiabasLib's
D-CAN concept (0x0110):

* DTR asserted, RTS released while the port is open.
* The cable echoes every byte written; the echo is read back and discarded.
  Cables that don't echo are detected on the first request and handled too.
* ``7F <sid> 78`` (response pending) replies extend the wait instead of failing.
* DSR reflects ignition (terminal 15) on FTDI-based cables.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from e92_redline.core.app_logging import get_logger
from e92_redline.transport.base import BaseTransport, TransportError
from e92_redline.transport.bmw_fast import (
    DME_ADDRESS,
    TESTER_ADDRESS,
    Frame,
    FrameParser,
    build_frame,
)

logger = get_logger(__name__)

KDCAN_BAUDRATE = 115200
ECHO_TIMEOUT = 0.1          # s to wait for the cable's echo of our own telegram
DEFAULT_TIMEOUT = 1.0       # s to wait for an ECU reply (KWP2000 P2 is far shorter)
PENDING_TIMEOUT = 5.0       # s to keep waiting after a 7F xx 78 (P2* extended)
READ_SLICE = 0.02           # s per serial read; keeps latency low without busy-waiting
STASH_LIMIT = 32            # unclaimed replies kept for a later receive()
STASH_MAX_AGE = 2.0         # s before an unclaimed reply is considered stale

SerialFactory = Callable[[str], Any]


def _default_serial_factory(port: str) -> Any:
    """Open ``port`` with pyserial. Accepts device paths, COM names and pyserial URLs."""
    from e92_redline.sim.kdcan_cable import SIM_PORT, FakeSerial

    if port == SIM_PORT:
        return FakeSerial()
    try:
        import serial
    except ImportError as e:  # pragma: no cover - pyserial is a hard dependency
        raise TransportError(
            message="pyserial is not installed (pip install pyserial)",
            code="MISSING_DEPENDENCY",
            recoverable=False,
        ) from e

    kwargs: dict[str, Any] = {
        "baudrate": KDCAN_BAUDRATE,
        "bytesize": serial.EIGHTBITS,
        "parity": serial.PARITY_NONE,
        "stopbits": serial.STOPBITS_ONE,
        "timeout": READ_SLICE,
        "write_timeout": 1.0,
        "xonxoff": False,
        "rtscts": False,
        "dsrdtr": False,
    }
    if sys.platform != "win32" and "://" not in port:
        kwargs["exclusive"] = True  # Windows ports are exclusive already
    try:
        return serial.serial_for_url(port, **kwargs)
    except serial.SerialException as e:
        raise TransportError(
            message=f"Could not open {port}: {e}",
            code="SERIAL_OPEN_FAILED",
            recoverable=True,
        ) from e


def _set_low_latency(ser: Any, port: str) -> str:
    """
    Cut the FTDI receive latency from its 16 ms default to 1 ms where we can.

    Every request/reply round trip pays this latency, so it roughly decides how
    many live values per second the dashboard gets. Returns a short status string.
    """
    if sys.platform.startswith("linux"):
        try:
            ser.set_low_latency_mode(True)  # pyserial: ASYNC_LOW_LATENCY -> 1 ms on ftdi_sio
            return "low-latency (1 ms)"
        except Exception:  # ptys and some drivers refuse; that is fine
            pass
        sysfs = Path("/sys/bus/usb-serial/devices") / Path(port).resolve().name / "latency_timer"
        try:
            value = sysfs.read_text().strip()
            return f"latency_timer={value} ms"
        except OSError:
            return "latency unknown"
    if sys.platform == "win32":
        return "set Latency Timer to 1 ms in Device Manager for best speed"
    return "default latency"


class KdcanTransport(BaseTransport):
    """BMW-FAST over a K+DCAN USB-serial cable."""

    def __init__(
        self,
        serial_factory: SerialFactory | None = None,
        dtr: bool = True,
        rts: bool = False,
    ) -> None:
        self._serial_factory = serial_factory or _default_serial_factory
        self._dtr = dtr
        self._rts = rts
        self._ser: Any = None
        self._port: str | None = None
        self._parser = FrameParser()
        self._stash: deque[tuple[float, Frame]] = deque(maxlen=STASH_LIMIT)
        self._last_sid: int | None = None
        self._lock = threading.RLock()
        self._target_address = DME_ADDRESS
        self._echo: bool | None = None  # None until the first request tells us
        self._pending: deque[Frame] = deque()  # decoded but not yet examined
        self._latency = "unknown"
        self.last_probe: dict[str, Any] = {}

    # ------------------------------------------------------------------ open/close

    def open(self, interface: str, bitrate: int = 500000) -> bool:
        """Open the cable on ``interface`` (a serial port). ``bitrate`` is ignored:
        the cable runs the CAN side at 500 kbit/s itself."""
        with self._lock:
            if self.is_open():
                self.close()
            ser = self._serial_factory(interface)
            for line, state in (("dtr", self._dtr), ("rts", self._rts)):
                try:
                    setattr(ser, line, state)
                except Exception as e:  # ptys and simulators have no modem lines
                    logger.debug(f"Could not set {line.upper()} on {interface}: {e}")
            self._latency = _set_low_latency(ser, interface)
            time.sleep(0.05)  # let the cable's MCU settle after DTR changes
            with contextlib.suppress(Exception):
                ser.reset_input_buffer()
                ser.reset_output_buffer()
            self._ser = ser
            self._port = interface
            self._parser.clear()
            self._pending.clear()
            self._stash.clear()
            self._echo = None
            logger.info(f"K+DCAN cable opened on {interface} ({self._latency})")
            return True

    def close(self) -> None:
        with self._lock:
            if self._ser is None:
                return
            try:
                with contextlib.suppress(Exception):
                    self._ser.dtr = False
                self._ser.close()
                logger.info(f"K+DCAN cable closed: {self._port}")
            except Exception as e:
                logger.warning(f"Error closing {self._port}: {e}")
            finally:
                self._ser = None
                self._port = None

    def is_open(self) -> bool:
        return self._ser is not None and bool(getattr(self._ser, "is_open", True))

    @property
    def port(self) -> str | None:
        return self._port

    def ignition_on(self) -> bool | None:
        """Ignition (terminal 15) as reported on DSR, or None if the port can't tell."""
        if not self.is_open():
            return None
        try:
            return bool(self._ser.dsr)
        except Exception:
            return None

    def set_target_address(self, address: int) -> None:
        self._target_address = address & 0xFF

    # ------------------------------------------------------------------ low level

    def _require_open(self) -> None:
        if not self.is_open():
            raise TransportError(message="K+DCAN cable not open", code="NOT_OPEN")

    def _read_some(self, timeout: float) -> bytes:
        """Read whatever arrives within ``timeout`` (at least one byte if any arrives)."""
        slice_ = READ_SLICE if timeout >= READ_SLICE else max(0.001, round(timeout, 3))
        if self._ser.timeout != slice_:
            # Only when it changes: on Windows every assignment reconfigures the port.
            self._ser.timeout = slice_
        try:
            waiting = int(self._ser.in_waiting)
        except Exception:
            waiting = 0
        return bytes(self._ser.read(max(1, waiting)))

    def _write_telegram(self, telegram: bytes) -> None:
        """Write one telegram and consume the cable's echo of it."""
        try:
            self._ser.write(telegram)
            self._ser.flush()
        except Exception as e:
            raise TransportError(message=f"Write failed: {e}", code="WRITE_FAILED") from e
        logger.debug(f"TX {telegram.hex(' ')}")

        if self._echo is False:
            return

        got = bytearray()
        deadline = time.monotonic() + ECHO_TIMEOUT
        while len(got) < len(telegram) and (left := deadline - time.monotonic()) > 0:
            got.extend(self._read_some(left))

        if bytes(got[: len(telegram)]) == telegram:
            if self._echo is None:
                logger.info("K+DCAN cable echoes transmitted data (normal)")
            self._echo = True
            extra = got[len(telegram) :]
        else:
            if self._echo is None:
                logger.info("K+DCAN cable does not echo; continuing without echo removal")
            elif got:
                logger.warning(f"Echo mismatch: sent {telegram.hex()}, got {bytes(got).hex()}")
            self._echo = False
            extra = got
        if extra:
            self._pending.extend(self._parser.feed(extra))

    def _stash_frame(self, f: Frame) -> None:
        self._stash.append((time.monotonic(), f))

    def _next_frame(self, deadline: float) -> Frame | None:
        """Next decoded telegram from the stream, or None at ``deadline``."""
        while not self._pending:
            left = deadline - time.monotonic()
            if left <= 0:
                if self._parser.pending:
                    # A partial telegram that never completed is noise; drop it so
                    # it can't swallow the start of the next reply.
                    logger.debug(f"Dropping {self._parser.pending} unparsed byte(s)")
                    self._parser.clear()
                return None
            chunk = self._read_some(left)
            if chunk:
                logger.debug(f"RX {chunk.hex(' ')}")
                self._pending.extend(self._parser.feed(chunk))
        return self._pending.popleft()

    def _wait_for(
        self, accept: Callable[[Frame], bool], timeout: float
    ) -> Frame | None:
        """
        Return the first telegram ``accept`` likes, honouring response-pending.

        Telegrams that don't match are stashed for a later caller (e.g. the UDS
        client's receive() while the live-data poller is mid-request).
        """
        deadline = time.monotonic() + timeout

        # Something we already buffered may be the answer; stale entries are dropped.
        now = time.monotonic()
        for item in list(self._stash):
            stamp, f = item
            if now - stamp > STASH_MAX_AGE:
                self._stash.remove(item)
            elif accept(f):
                self._stash.remove(item)
                if not f.is_response_pending():
                    return f
                deadline = max(deadline, now + PENDING_TIMEOUT)

        while True:
            f = self._next_frame(deadline)
            if f is None:
                return None
            if accept(f):
                if f.is_response_pending():
                    logger.debug("ECU says response pending (7F xx 78); extending wait")
                    deadline = max(deadline, time.monotonic() + PENDING_TIMEOUT)
                    continue
                return f
            self._stash_frame(f)

    # ------------------------------------------------------------------ high level

    def request(
        self,
        payload: bytes,
        target: int | None = None,
        functional: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
        accept: Callable[[Frame], bool] | None = None,
    ) -> Frame | None:
        """
        Send ``payload`` and wait for the matching reply, atomically.

        By default a reply matches when it is addressed to the tester, comes from
        ``target`` (any source for functional requests) and is either the positive
        response (SID + 0x40) or a negative response for this SID.
        """
        self._require_open()
        tgt = self._target_address if target is None else target & 0xFF
        sid = payload[0]

        def default_accept(f: Frame) -> bool:
            if f.target != TESTER_ADDRESS or not f.payload:
                return False
            if not functional and f.source != tgt:
                return False
            if f.payload[0] == (sid + 0x40) & 0xFF:
                return True
            return len(f.payload) >= 2 and f.payload[0] == 0x7F and f.payload[1] == sid

        with self._lock:
            self._write_telegram(build_frame(tgt, payload, TESTER_ADDRESS, functional))
            return self._wait_for(accept or default_accept, timeout)

    # BaseTransport API used by UDSClient: payload in, payload out.

    def send(self, data: bytes) -> bool:
        self._require_open()
        if not data:
            return False
        with self._lock:
            self._write_telegram(build_frame(self._target_address, data))
            self._last_sid = data[0]
        return True

    def receive(self, timeout: float = DEFAULT_TIMEOUT) -> bytes | None:
        """Reply to the last send(): positive or negative response for its SID."""
        self._require_open()
        tgt = self._target_address
        sid = self._last_sid

        def accept(f: Frame) -> bool:
            if f.target != TESTER_ADDRESS or f.source != tgt or not f.payload:
                return False
            if sid is None:
                return True
            if f.payload[0] == (sid + 0x40) & 0xFF:
                return True
            return len(f.payload) >= 2 and f.payload[0] == 0x7F and f.payload[1] == sid

        with self._lock:
            f = self._wait_for(accept, timeout)
        return f.payload if f else None

    def flush(self) -> None:
        with self._lock:
            self._stash.clear()
            self._pending.clear()
            self._parser.clear()
            if self.is_open():
                with contextlib.suppress(Exception):
                    self._ser.reset_input_buffer()

    def validate(self) -> bool:
        """
        The port is usable if it opened. Also pings the DME (TesterPresent) and
        records what happened in ``last_probe`` so the UI can say whether the car
        actually answered; a silent car (ignition off) still allows connecting.
        """
        if not self.is_open():
            return False
        probe: dict[str, Any] = {"ignition": self.ignition_on(), "dme": False}
        try:
            reply = self.request(bytes([0x3E, 0x01]), target=DME_ADDRESS, timeout=0.5)
            probe["dme"] = reply is not None
            if reply is not None:
                probe["dme_reply"] = reply.payload.hex(" ")
        except TransportError as e:
            probe["error"] = e.message
        probe["echo"] = self._echo
        self.last_probe = probe
        logger.info(f"K+DCAN probe: {probe}")
        return True

    def get_info(self) -> dict[str, Any]:
        return {
            "type": "kdcan",
            "port": self._port,
            "is_open": self.is_open(),
            "target": f"0x{self._target_address:02X}",
            "echo": self._echo,
            "latency": self._latency,
            "bad_frames": self._parser.bad_frames,
            "ignition": self.ignition_on(),
        }
