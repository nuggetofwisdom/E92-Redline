import time
from collections.abc import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from .definitions import PidDef
from .obd_reader import ObdSource, query_supported_pids


class LivePoller(QThread):
    value_ready = pyqtSignal(str, float)  # (pid id, decoded value)
    unsupported = pyqtSignal(list)        # pid ids the car says it doesn't report
    status = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        source_factory: Callable[[], ObdSource],
        pids: list[PidDef],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._source_factory = source_factory
        self._pids = pids
        self._running = False

    def run(self) -> None:
        try:
            reader = self._source_factory()
        except Exception as e:
            self.error.emit(f"Could not open the adapter: {e}")
            return
        self._running = True
        try:
            pids = self._filter_supported(reader)
            if pids is None:
                return
            self.status.emit(f"Polling {len(pids)} values on {reader.description}")
            next_due = {p.id: 0.0 for p in pids}
            while self._running:
                now = time.monotonic()
                due = [p for p in pids if now >= next_due[p.id]]
                if not due:
                    self.msleep(5)
                    continue
                for p in due:
                    if not self._running:
                        break
                    next_due[p.id] = now + 1.0 / p.hz
                    data = reader.read_pid(p.pid)
                    if data is not None and len(data) >= p.bytes:
                        self.value_ready.emit(p.id, p.decode(data))
        except Exception as e:
            self.error.emit(str(e))
        finally:
            reader.close()

    def _filter_supported(self, reader: ObdSource) -> list[PidDef] | None:
        """Skip values the car says it doesn't report, so they don't cost a timeout each."""
        try:
            supported = query_supported_pids(reader)
        except Exception as e:
            self.error.emit(str(e))
            return None
        if supported is None:
            return list(self._pids)
        missing = [p.id for p in self._pids if p.pid not in supported]
        if missing:
            self.unsupported.emit(missing)
        return [p for p in self._pids if p.pid in supported]

    def stop(self) -> None:
        self._running = False
        self.wait(2000)
