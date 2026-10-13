"""
E92Redline Application Bootstrap

Main entry point for the E92Redline diagnostic tool.
Initializes logging, loads configuration, and starts the PyQt6 GUI.
"""

import sys
import argparse
from pathlib import Path
from typing import NoReturn

from e92_redline.core.app_logging import setup_logging, get_logger
from e92_redline.core.config import load_config


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="e92-redline",
        description="E92Redline - BMW E92 M3 Diagnostic Tool",
        epilog="For more information, see the documentation.",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Custom log directory (default: ./logs)",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to configuration file",
    )
    parser.add_argument(
        "--port",
        "-p",
        action="append",
        default=[],
        metavar="PORT",
        help="Extra K+DCAN serial port to offer, e.g. COM5 or a pty from tools/fake_kdcan.py",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Offer a simulated K+DCAN cable so Live Data works without the car",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version="%(prog)s 0.1.0",
    )
    return parser.parse_args()


def main() -> NoReturn:
    """Main application entry point."""
    args = parse_arguments()

    # Setup logging
    log_dir = args.log_dir or Path("./logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(log_dir=log_dir, debug=args.debug)
    logger = get_logger(__name__)

    logger.info("E92Redline starting...")
    logger.info(f"Debug mode: {args.debug}")

    # Load configuration
    config = load_config(args.config)
    config.connection.extra_ports = list(args.port)
    config.connection.simulate = args.simulate

    # Import PyQt6 after argument parsing to avoid slow startup for --help
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError as e:
        logger.error(f"Failed to import PyQt6: {e}")
        print("Error: PyQt6 is required. Install with: pip install PyQt6")
        sys.exit(1)

    # Create Qt application
    app = QApplication(sys.argv)
    app.setApplicationName("E92Redline")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("E92Redline")

    # Set application style
    app.setStyle("Fusion")

    # Import and create main window
    from e92_redline.gui.main_window import MainWindow

    window = MainWindow(config=config)
    window.show()

    logger.info("Application window displayed")

    # Run event loop
    exit_code = app.exec()

    logger.info(f"Application exiting with code: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
