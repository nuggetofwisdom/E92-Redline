import time
from PyQt6.QtCore import QThread, pyqtSignal
from .definitions import PidDef
from .obd_reader import ObdReader

class LivePoller(QThread):
    value_ready = pyqtSignal(str, float)  # (pid id, decoded value)
    error = pyqtSignal(str)

    def __init__(self, channel: str, pids: list[PidDef], parent=None) -> None:
        super().__init__(parent)
        self._channel = channel
        self._pids = pids
        self._running = False

    def run(self) -> None:
        try:
            reader = ObdReader(self._channel)
        except Exception as e:
            self.error.emit(f"Could not open {self._channel}: {e}")
            return
        self._running = True
        next_due = {p.id: 0.0 for p in self._pids}
        try:
            while self._running:
                now = time.monotonic()
                due = [p for p in self._pids if now >= next_due[p.id]]
                if not due:
                    self.msleep(5)
                    continue
                for p in due:
                    next_due[p.id] = now + 1.0 / p.hz
                    data = reader.read_pid(p.pid)
                    if data is not None and len(data) >= p.bytes:
                        self.value_ready.emit(p.id, p.decode(data))
        finally:
            reader.close()

    def stop(self) -> None:
        self._running = False
        self.wait(2000)
