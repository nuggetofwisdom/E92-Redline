import time
import can

OBD_REQUEST_ID = 0x7DF      # broadcast to all OBD-II ECUs
ENGINE_RESPONSE_ID = 0x7E8  # engine computer's reply ID

class ObdReader:
    def __init__(self, channel: str = "can0") -> None:
        self._bus = can.Bus(
            interface="socketcan",
            channel=channel,
            can_filters=[{"can_id": ENGINE_RESPONSE_ID, "can_mask": 0x7FF}],
        )

    def read_pid(self, pid: int, timeout: float = 0.1) -> bytes | None:
        self._bus.send(can.Message(
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

    def __enter__(self) -> "ObdReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
