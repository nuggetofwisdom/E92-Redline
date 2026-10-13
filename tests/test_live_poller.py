"""Live Data poller end to end on the simulated K+DCAN cable."""

from e92_redline.live_data.definitions import load_pids
from e92_redline.live_data.obd_reader import KdcanObdReader
from e92_redline.live_data.poller import LivePoller
from e92_redline.sim.engine import SimulatedEngine
from e92_redline.sim.kdcan_cable import FakeSerial, SimulatedKdcanCable
from e92_redline.transport.kdcan_transport import KdcanTransport


def test_poller_reads_values_and_reports_unsupported(qtbot):
    cable = SimulatedKdcanCable(engine=SimulatedEngine(unsupported={0x46}))
    ser = FakeSerial(cable, latency=0)
    transport = KdcanTransport(serial_factory=lambda port: ser)
    transport.open("sim")

    values: dict[str, float] = {}
    unsupported: list[str] = []
    poller = LivePoller(lambda: KdcanObdReader(transport), load_pids())
    poller.value_ready.connect(lambda k, v: values.__setitem__(k, v))
    poller.unsupported.connect(unsupported.extend)
    poller.start()
    qtbot.waitUntil(lambda: {"rpm", "coolant", "voltage"} <= values.keys(), timeout=3000)
    poller.stop()

    assert unsupported == ["ambient"]
    assert values["coolant"] == 90
    assert 13.7 < values["voltage"] < 13.9
    assert 400 < values["rpm"] < 5600


def test_poller_reports_error_when_car_is_silent(qtbot):
    cable = SimulatedKdcanCable()
    cable.ignition = False
    ser = FakeSerial(cable, latency=0)
    transport = KdcanTransport(serial_factory=lambda port: ser)
    transport.open("sim")

    poller = LivePoller(lambda: KdcanObdReader(transport, probe_timeout=0.02), load_pids())
    with qtbot.waitSignal(poller.error, timeout=3000) as blocker:
        poller.start()
    poller.stop()
    assert "ignition" in blocker.args[0].lower()
