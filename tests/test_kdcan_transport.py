"""K+DCAN transport and OBD reader against the simulated cable."""

import pytest

from e92_redline.live_data.obd_reader import (
    KdcanObdReader,
    ObdUnavailable,
    query_supported_pids,
)
from e92_redline.sim.engine import SimulatedEngine
from e92_redline.sim.kdcan_cable import FakeSerial, SimulatedKdcanCable
from e92_redline.transport.base import TransportError
from e92_redline.transport.bmw_fast import build_frame
from e92_redline.transport.kdcan_transport import KdcanTransport


def make(cable: SimulatedKdcanCable | None = None) -> tuple[KdcanTransport, FakeSerial]:
    ser = FakeSerial(cable or SimulatedKdcanCable(), latency=0)
    t = KdcanTransport(serial_factory=lambda port: ser)
    t.open("sim")
    return t, ser


def test_open_sets_ediabas_line_states():
    t, ser = make()
    assert ser.dtr is True and ser.rts is False
    t.close()
    assert ser.dtr is False and not t.is_open()


@pytest.mark.parametrize("echo", [True, False])
def test_request_with_and_without_echo(echo):
    t, _ = make(SimulatedKdcanCable(echo=echo))
    reply = t.request(bytes([0x3E, 0x01]), target=0x12)
    assert reply is not None and reply.payload == bytes([0x7E, 0x01])
    assert t.get_info()["echo"] is echo


def test_response_pending_is_waited_through():
    t, _ = make(SimulatedKdcanCable(pending_first=True))
    reply = t.request(bytes([0x01, 0x0C]), target=0x12)
    assert reply is not None and reply.payload[:2] == bytes([0x41, 0x0C])


def test_send_receive_api_used_by_uds_client():
    t, _ = make()
    t.set_target_address(0x12)
    assert t.send(bytes([0x3E, 0x01]))
    assert t.receive(0.5) == bytes([0x7E, 0x01])


def test_negative_response_returned_to_uds_client():
    t, _ = make()
    t.set_target_address(0x12)
    t.send(bytes([0x22, 0xF1, 0x90]))
    assert t.receive(0.5) == bytes([0x7F, 0x22, 0x11])


def test_unclaimed_reply_is_kept_for_receive():
    """A UDS send, then a live-data request in between, then the UDS receive."""
    t, _ = make()
    t.set_target_address(0x12)
    t.send(bytes([0x3E, 0x01]))
    reader = KdcanObdReader(t)
    assert reader.read_pid(0x05) == bytes([130])  # poller grabs the line meanwhile
    assert t.receive(0.5) == bytes([0x7E, 0x01])   # UDS reply was stashed, not lost


def test_ignition_from_dsr():
    cable = SimulatedKdcanCable()
    t, _ = make(cable)
    assert t.ignition_on() is True
    cable.ignition = False
    assert t.ignition_on() is False


def test_timeout_returns_none_when_car_is_silent():
    cable = SimulatedKdcanCable()
    cable.ignition = False
    t, _ = make(cable)
    assert t.request(bytes([0x3E, 0x01]), target=0x12, timeout=0.05) is None


def test_validate_records_probe_even_if_car_is_off():
    cable = SimulatedKdcanCable()
    cable.ignition = False
    t, _ = make(cable)
    assert t.validate() is True
    assert t.last_probe["dme"] is False and t.last_probe["ignition"] is False


def test_not_open_raises():
    with pytest.raises(TransportError):
        KdcanTransport().send(b"\x3e")


def test_request_frames_on_the_wire():
    cable = SimulatedKdcanCable()
    t, _ = make(cable)
    t.request(bytes([0x01, 0x0C]), target=0x12)
    assert cable.requests[-1] == bytes([0x01, 0x0C])
    assert build_frame(0x12, bytes([0x01, 0x0C])) == bytes.fromhex("82 12 f1 01 0c 92")


# ------------------------------------------------------------------ OBD over the cable

def test_obd_reader_uses_physical_dme_when_it_answers():
    t, _ = make()
    reader = KdcanObdReader(t)
    data = reader.read_pid(0x0C)
    assert reader.route == "DME physical 0x12"
    assert data is not None and len(data) == 2


def test_obd_reader_falls_back_to_functional_address():
    t, _ = make(SimulatedKdcanCable(physical_obd=False))
    reader = KdcanObdReader(t, probe_timeout=0.05)
    assert reader.read_pid(0x05) == bytes([130])
    assert reader.route == "OBD functional 0x33"


def test_obd_reader_reports_when_nothing_answers():
    t, _ = make(SimulatedKdcanCable(physical_obd=False, functional_obd=False))
    reader = KdcanObdReader(t, probe_timeout=0.02)
    with pytest.raises(ObdUnavailable):
        reader.read_pid(0x0C)


def test_unsupported_pid_returns_none_quickly():
    t, _ = make(SimulatedKdcanCable(engine=SimulatedEngine(unsupported={0x46})))
    assert KdcanObdReader(t).read_pid(0x46) is None


def test_supported_pid_query():
    t, _ = make(SimulatedKdcanCable(engine=SimulatedEngine(unsupported={0x46})))
    supported = query_supported_pids(KdcanObdReader(t))
    assert supported is not None
    assert {0x0C, 0x05, 0x42} <= supported
    assert 0x46 not in supported
