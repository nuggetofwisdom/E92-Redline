import math, time
import can

bus = can.Bus(interface="socketcan", channel="vcan0")
t0 = time.monotonic()

def payload(pid: int) -> bytes | None:
    t = time.monotonic() - t0
    rpm = 3000 + 2500 * math.sin(t / 2)          # sweeps roughly 500-5500 rpm
    return {
        0x0C: int(rpm * 4).to_bytes(2, "big"),
        0x0E: bytes([int((20 + 64) * 2)]),       # 20° advance
        0x04: bytes([int(rpm / 8400 * 255)]),
        0x11: bytes([int(rpm / 8400 * 255)]),
        0x10: int(rpm / 50 * 100).to_bytes(2, "big"),
        0x0D: bytes([int(rpm / 60)]),
        0x06: bytes([128 + int(5 * math.sin(t))]),
        0x08: bytes([128 - int(5 * math.sin(t))]),
        0x07: bytes([130]), 0x09: bytes([126]),
        0x05: bytes([90 + 40]), 0x0F: bytes([25 + 40]),
        0x46: bytes([18 + 40]),                  # 18 °C ambient
        0x42: (13800).to_bytes(2, "big"),
    }.get(pid)

print("Fake ECU listening on vcan0")
for msg in bus:
    if msg.arbitration_id == 0x7DF and len(msg.data) >= 3 and msg.data[1] == 0x01:
        data = payload(msg.data[2])
        if data is None:
            continue
        frame = [2 + len(data), 0x41, msg.data[2], *data]
        bus.send(can.Message(arbitration_id=0x7E8, data=frame + [0] * (8 - len(frame)),
                             is_extended_id=False))
