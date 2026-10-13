"""Connecting to a K+DCAN cable through the ConnectionManager."""

from e92_redline.core.config import AppConfig
from e92_redline.core.connection import ConnectionManager, ConnectionState
from e92_redline.live_data.obd_reader import KdcanObdReader
from e92_redline.sim.kdcan_cable import SIM_PORT
from e92_redline.transport.kdcan_transport import KdcanTransport


def test_interface_type_guessing():
    assert ConnectionManager.interface_from_name("can0").interface_type == "socketcan"
    assert ConnectionManager.interface_from_name("vcan0").interface_type == "virtual"
    for name in ("/dev/ttyUSB0", "COM3", "/dev/cu.usbserial-A1", SIM_PORT):
        assert ConnectionManager.interface_from_name(name).interface_type == "kdcan"


def test_simulator_is_listed_when_requested():
    config = AppConfig()
    config.connection.simulate = True
    config.connection.extra_ports = ["/dev/pts/9"]
    names = [i.name for i in ConnectionManager(config).discover_interfaces()]
    assert SIM_PORT in names and "/dev/pts/9" in names


def test_connect_to_simulated_cable_and_read_live_data():
    manager = ConnectionManager(AppConfig())
    assert manager.connect(SIM_PORT)
    assert manager.state == ConnectionState.CONNECTED
    assert isinstance(manager.get_transport(), KdcanTransport)
    assert manager.get_transport().last_probe["dme"] is True

    factory, label = manager.obd_source_factory()
    reader = factory()
    assert isinstance(reader, KdcanObdReader)
    assert reader.read_pid(0x05) == bytes([130])
    reader.close()  # must not close the shared cable
    assert manager.get_transport().is_open()

    manager.disconnect()
    assert manager.obd_source_factory() is None


def test_vin_read_falls_back_to_kwp_on_the_cable():
    from e92_redline.protocols.uds_client import UDSClient
    from e92_redline.sim.kdcan_cable import SIM_VIN

    manager = ConnectionManager(AppConfig())
    assert manager.connect(SIM_PORT)
    client = UDSClient(manager.get_transport())
    client.set_target(0x12)
    assert client.read_vin() == SIM_VIN
    manager.disconnect()
