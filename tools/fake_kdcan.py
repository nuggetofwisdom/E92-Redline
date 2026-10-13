"""
Simulated K+DCAN cable on a pseudo-terminal (Linux and macOS).

Exercises the real serial code path end to end: the app opens the pty exactly
as it would open /dev/ttyUSB0 or /dev/cu.usbserial-*.

    python tools/fake_kdcan.py            # prints e.g. /dev/pts/4
    e92-redline --port /dev/pts/4         # then Connect, Live Data, Start

On Windows (no ptys) use the in-process simulator instead: e92-redline --simulate
"""

import argparse
import os
import sys

from e92_redline.sim.kdcan_cable import SimulatedKdcanCable


def main() -> int:
    if os.name != "posix":
        print("Pseudo-terminals need Linux or macOS. On Windows run: e92-redline --simulate")
        return 1
    import tty

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-echo", action="store_true", help="simulate a cable without echo")
    args = parser.parse_args()

    master, slave = os.openpty()
    tty.setraw(slave)
    cable = SimulatedKdcanCable(echo=not args.no_echo)
    print(f"Fake K+DCAN cable on {os.ttyname(slave)}  (Ctrl+C to stop)", flush=True)
    try:
        while True:
            data = os.read(master, 4096)
            if data:
                reply = cable.process(data)
                if reply:
                    os.write(master, reply)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
