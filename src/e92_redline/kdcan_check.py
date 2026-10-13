"""
K+DCAN cable check: does the cable open, does the car answer, and how fast.

    e92-redline-cablecheck              # pick the most likely cable automatically
    e92-redline-cablecheck COM3         # or name the port
    e92-redline-cablecheck /dev/ttyUSB0 --try-lines --debug

Run it with the cable in the OBD port and the ignition on. Paste the output
into an issue if something doesn't work; it has everything needed to debug.
"""

from __future__ import annotations

import argparse
import logging
import platform
import sys
import time
from dataclasses import dataclass, field

from e92_redline.live_data.obd_reader import KdcanObdReader, ObdUnavailable
from e92_redline.transport.base import TransportError
from e92_redline.transport.bmw_fast import DME_ADDRESS
from e92_redline.transport.kdcan_transport import KdcanTransport

# DTR/RTS combinations to try with --try-lines; the first is EdiabasLib's D-CAN setting.
LINE_STATES = ((True, False), (False, False), (True, True), (False, True))


@dataclass
class CableReport:
    port: str
    opened: bool = False
    dme_answered: bool = False
    obd_route: str | None = None
    rpm: float | None = None
    coolant: float | None = None
    requests_per_second: float | None = None
    lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.obd_route is not None

    def say(self, text: str) -> None:
        self.lines.append(text)


def probe_cable(port: str, dtr: bool = True, rts: bool = False) -> CableReport:
    """Open ``port`` as a K+DCAN cable and see what the car says. Never raises."""
    report = CableReport(port)
    transport = KdcanTransport(dtr=dtr, rts=rts)
    try:
        transport.open(port)
    except TransportError as e:
        report.say(f"Open {port}: FAILED - {e.message}")
        return report
    report.opened = True
    try:
        info = transport.get_info()
        report.say(f"Open {port}: OK (DTR={int(dtr)} RTS={int(rts)}, {info['latency']})")
        ign = transport.ignition_on()
        report.say(
            "Ignition (DSR): "
            + {True: "ON", False: "OFF - turn the key to position 2", None: "unknown"}[ign]
        )

        reply = transport.request(bytes([0x3E, 0x01]), target=DME_ADDRESS, timeout=0.5)
        report.dme_answered = reply is not None
        echo = transport.get_info()["echo"]
        report.say(f"Cable echo: {'yes' if echo else 'no' if echo is False else 'unknown'}")
        report.say(
            f"DME (0x12) TesterPresent: {reply.payload.hex(' ') if reply else 'no answer'}"
        )

        reader = KdcanObdReader(transport)
        try:
            report.obd_route = reader.resolve_route()
            report.say(f"OBD-II Mode 01: answered via {report.obd_route}")
        except ObdUnavailable as e:
            report.say(f"OBD-II Mode 01: {e}")
            return report

        data = reader.read_pid(0x0C, timeout=0.5)
        if data and len(data) >= 2:
            report.rpm = (data[0] * 256 + data[1]) / 4
        data = reader.read_pid(0x05, timeout=0.5)
        if data:
            report.coolant = data[0] - 40.0
        report.say(f"Engine RPM: {report.rpm if report.rpm is not None else 'no answer'}")
        report.say(
            f"Coolant: {f'{report.coolant:.0f} °C' if report.coolant is not None else 'no answer'}"
        )

        n, t0 = 20, time.perf_counter()
        answered = sum(reader.read_pid(0x0C, timeout=0.3) is not None for _ in range(n))
        elapsed = time.perf_counter() - t0
        report.requests_per_second = answered / elapsed if elapsed else None
        report.say(
            f"Speed: {answered}/{n} answered, {report.requests_per_second:.1f} requests/s"
            if report.requests_per_second
            else f"Speed: {answered}/{n} answered"
        )
        bad = transport.get_info()["bad_frames"]
        if bad:
            report.say(f"Warning: {bad} telegram(s) with bad checksums")
    except TransportError as e:
        report.say(f"Error: {e.message}")
    finally:
        transport.close()
    return report


def _pick_port() -> str | None:
    from e92_redline.core.discovery import PortDiscovery

    ports = PortDiscovery().discover_ports()
    if not ports:
        return None
    print("Serial ports found:")
    for p in ports:
        print(f"  {p.device:<28} {p.name:<20} {p.description}")
    return ports[0].device


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="e92-redline-cablecheck", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("port", nargs="?", help="serial port (default: best guess)")
    parser.add_argument("--try-lines", action="store_true",
                        help="if the car doesn't answer, retry with other DTR/RTS settings")
    parser.add_argument("--debug", action="store_true", help="print every byte sent and received")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING,
                        format="%(asctime)s %(name)s %(message)s")
    print(f"E92-Redline cable check on {platform.system()} {platform.release()}, "
          f"Python {platform.python_version()}")

    port = args.port or _pick_port()
    if not port:
        print("No USB serial ports found. Is the cable plugged in? "
              "(Windows: check Device Manager > Ports (COM & LPT).)")
        return 2

    states = LINE_STATES if args.try_lines else LINE_STATES[:1]
    report = None
    for dtr, rts in states:
        print(f"\n--- {port} with DTR={int(dtr)} RTS={int(rts)} ---")
        report = probe_cable(port, dtr, rts)
        for line in report.lines:
            print("  " + line)
        if report.ok or not report.opened:
            break

    print()
    if report and report.ok:
        print("RESULT: cable works - the Live Data page will use it.")
        return 0
    print("RESULT: no OBD-II answer. Check: ignition on, cable fully seated, "
          "cable switch (if it has one) in the D-CAN position, nothing else using the port.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
