#!/usr/bin/env python3
"""Kept for old instructions; the cable check now lives in the package.

    python diagnose_cable.py [PORT] [--try-lines] [--debug]

is the same as ``e92-redline-cablecheck``.
"""

import sys

from e92_redline.kdcan_check import main

if __name__ == "__main__":
    sys.exit(main())
