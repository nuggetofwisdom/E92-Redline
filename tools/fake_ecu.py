"""Simulated engine computer on a virtual CAN bus (Linux, vcan0)."""

import can

from e92_redline.sim.engine import SimulatedEngine

bus = can.Bus(interface="socketcan", channel="vcan0")
engine = SimulatedEngine()

print("Fake ECU listening on vcan0")
for msg in bus:
    if msg.arbitration_id == 0x7DF and len(msg.data) >= 3 and msg.data[1] == 0x01:
        data = engine.pid(msg.data[2])
        if data is None:
            continue
        frame = [2 + len(data), 0x41, msg.data[2], *data]
        bus.send(can.Message(arbitration_id=0x7E8, data=frame + [0] * (8 - len(frame)),
                             is_extended_id=False))
