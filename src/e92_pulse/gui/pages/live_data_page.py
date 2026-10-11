"""
Live Data page: real-time engine values with an RPM bar, value tiles,
a scrolling history plot, and optional CSV logging.
"""

import csv
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from e92_pulse.core.app_logging import get_logger
from e92_pulse.live_data.definitions import PidDef, load_pids
from e92_pulse.live_data.poller import LivePoller

logger = get_logger(__name__)

REDLINE_RPM = 8400       # S65 redline
SHIFT_WARN_RPM = 7500    # bar turns amber above this
HISTORY_SECONDS = 30
PLOTTED = ("rpm", "throttle")  # values drawn on the history plot


class ValueTile(QFrame):
    """One live value: name, big number, unit."""

    def __init__(self, pid: PidDef, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "QFrame { background: #252526; border: 1px solid #3c3c3c; border-radius: 6px; }"
            "QLabel { border: none; }"
        )
        layout = QVBoxLayout(self)
        name = QLabel(pid.name)
        name.setStyleSheet("color: #888888;")
        self._value = QLabel("--")
        self._value.setFont(QFont("Sans", 22, QFont.Weight.Bold))
        self._value.setStyleSheet("color: #ffffff;")
        unit = QLabel(pid.unit)
        unit.setStyleSheet("color: #0099ff;")
        layout.addWidget(name)
        layout.addWidget(self._value)
        layout.addWidget(unit)

    def set_value(self, value: float) -> None:
        self._value.setText(f"{value:.0f}" if abs(value) >= 100 else f"{value:.1f}")


class LiveDataPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pids = load_pids()
        self._channel: str | None = None
        self._poller: LivePoller | None = None
        self._t0 = time.monotonic()
        self._history = {
            pid_id: (deque(maxlen=HISTORY_SECONDS * 20), deque(maxlen=HISTORY_SECONDS * 20))
            for pid_id in PLOTTED
        }
        self._log_file = None
        self._log_writer = None
        self._build_ui()

        # Redraw the plot at a steady 10 fps rather than on every sample.
        self._plot_timer = QTimer(self)
        self._plot_timer.timeout.connect(self._redraw_plot)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)

        header = QHBoxLayout()
        title = QLabel("Live Data")
        title.setFont(QFont("Sans", 18, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch()
        self._log_box = QCheckBox("Log to CSV")
        header.addWidget(self._log_box)
        self._start_btn = QPushButton("Start")
        self._start_btn.setMinimumWidth(100)
        self._start_btn.clicked.connect(self._toggle)
        header.addWidget(self._start_btn)
        root.addLayout(header)

        self._status = QLabel("Connect to the car, then press Start.")
        self._status.setStyleSheet("color: #888888;")
        root.addWidget(self._status)

        # Big RPM readout + bar
        self._rpm_label = QLabel("---- rpm")
        self._rpm_label.setFont(QFont("Sans", 36, QFont.Weight.Bold))
        root.addWidget(self._rpm_label)
        self._rpm_bar = QProgressBar()
        self._rpm_bar.setRange(0, REDLINE_RPM)
        self._rpm_bar.setTextVisible(False)
        self._rpm_bar.setFixedHeight(18)
        self._set_bar_color("#0099ff")
        root.addWidget(self._rpm_bar)

        # Tiles for everything except RPM
        grid = QGridLayout()
        self._tiles: dict[str, ValueTile] = {}
        tile_pids = [p for p in self._pids if p.id != "rpm"]
        for i, pid in enumerate(tile_pids):
            tile = ValueTile(pid)
            self._tiles[pid.id] = tile
            grid.addWidget(tile, i // 4, i % 4)
        root.addLayout(grid)

        # History plot
        pg.setConfigOptions(antialias=True)
        self._plot = pg.PlotWidget(background="#1e1e1e")
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._plot.setLabel("bottom", "seconds")
        self._plot.setLabel("left", "RPM")
        self._plot.setYRange(0, REDLINE_RPM)
        self._rpm_curve = self._plot.plot(pen=pg.mkPen("#0099ff", width=2))
        # Throttle (0-100 %) on a second axis on the right
        self._throttle_view = pg.ViewBox()
        self._plot.showAxis("right")
        self._plot.scene().addItem(self._throttle_view)
        self._plot.getAxis("right").linkToView(self._throttle_view)
        self._plot.getAxis("right").setLabel("Throttle %")
        self._throttle_view.setXLink(self._plot)
        self._throttle_view.setYRange(0, 100)
        self._throttle_curve = pg.PlotCurveItem(pen=pg.mkPen("#e5a00d", width=1.5))
        self._throttle_view.addItem(self._throttle_curve)
        self._plot.getViewBox().sigResized.connect(
            lambda: self._throttle_view.setGeometry(self._plot.getViewBox().sceneBoundingRect())
        )
        root.addWidget(self._plot, 1)

    def _set_bar_color(self, color: str) -> None:
        self._rpm_bar.setStyleSheet(
            "QProgressBar { background: #252526; border: 1px solid #3c3c3c; border-radius: 4px; }"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 4px; }}"
        )

    # ---------- wiring from MainWindow ----------

    def set_channel(self, channel: str | None) -> None:
        """Called on connect (with 'can0'/'vcan0') and on disconnect (None)."""
        if channel is None:
            self.stop()
        self._channel = channel

    # ---------- start / stop ----------

    def _toggle(self) -> None:
        if self._poller is not None:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if not self._channel:
            self._status.setText("Not connected.")
            return
        for xs, ys in self._history.values():
            xs.clear()
            ys.clear()
        self._t0 = time.monotonic()
        if self._log_box.isChecked():
            self._open_log()
        self._poller = LivePoller(self._channel, self._pids, self)
        self._poller.value_ready.connect(self._on_value)
        self._poller.error.connect(self._on_error)
        self._poller.start()
        self._plot_timer.start(100)
        self._start_btn.setText("Stop")
        self._log_box.setEnabled(False)
        self._status.setText(f"Polling {len(self._pids)} values on {self._channel}")

    def stop(self) -> None:
        if self._poller is not None:
            self._poller.stop()
            self._poller = None
        self._plot_timer.stop()
        self._close_log()
        self._start_btn.setText("Start")
        self._log_box.setEnabled(True)
        if self._channel:
            self._status.setText("Stopped.")

    # ---------- data ----------

    def _on_value(self, pid_id: str, value: float) -> None:
        t = time.monotonic() - self._t0
        if pid_id == "rpm":
            self._rpm_label.setText(f"{value:,.0f} rpm")
            self._rpm_bar.setValue(int(min(value, REDLINE_RPM)))
            self._set_bar_color("#e5a00d" if value >= SHIFT_WARN_RPM else "#0099ff")
        elif pid_id in self._tiles:
            self._tiles[pid_id].set_value(value)
        if pid_id in self._history:
            xs, ys = self._history[pid_id]
            xs.append(t)
            ys.append(value)
        if self._log_writer:
            self._log_writer.writerow([f"{t:.3f}", pid_id, f"{value:.3f}"])

    def _on_error(self, message: str) -> None:
        logger.warning(message)
        self._status.setText(message)
        self.stop()

    def _redraw_plot(self) -> None:
        rpm_x, rpm_y = self._history["rpm"]
        thr_x, thr_y = self._history["throttle"]
        self._rpm_curve.setData(list(rpm_x), list(rpm_y))
        self._throttle_curve.setData(list(thr_x), list(thr_y))
        now = time.monotonic() - self._t0
        self._plot.setXRange(max(0, now - HISTORY_SECONDS), max(HISTORY_SECONDS, now), padding=0)

    # ---------- CSV logging ----------

    def _open_log(self) -> None:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        path = log_dir / f"live_{datetime.now():%Y%m%d_%H%M%S}.csv"
        self._log_file = open(path, "w", newline="")
        self._log_writer = csv.writer(self._log_file)
        self._log_writer.writerow(["seconds", "value_id", "value"])
        logger.info(f"Logging live data to {path}")

    def _close_log(self) -> None:
        if self._log_file:
            self._log_file.close()
        self._log_file = None
        self._log_writer = None

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.stop()
        super().closeEvent(event)
