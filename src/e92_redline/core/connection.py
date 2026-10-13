"""
Connection Manager

Manages the lifecycle of diagnostic connections including:
- Connection establishment and validation
- SocketCAN interfaces (Linux) and K+DCAN USB cables (Linux, macOS, Windows)
- State management and event notifications
"""

import sys
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Protocol, Any

from e92_redline.core.app_logging import get_logger, log_audit_event
from e92_redline.core.config import AppConfig

logger = get_logger(__name__)


class ConnectionState(Enum):
    """Connection state machine states."""

    DISCONNECTED = auto()
    CONNECTING = auto()
    VALIDATING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()
    ERROR = auto()


class TransportProtocol(Protocol):
    """Protocol for transport implementations."""

    def open(self, interface: str, bitrate: int = 500000) -> bool:
        """Open the transport connection."""
        ...

    def close(self) -> None:
        """Close the transport connection."""
        ...

    def is_open(self) -> bool:
        """Check if transport is open."""
        ...

    def send(self, data: bytes) -> bool:
        """Send data through the transport."""
        ...

    def receive(self, timeout: float) -> bytes | None:
        """Receive data from the transport."""
        ...

    def validate(self) -> bool:
        """Validate the connection is alive."""
        ...

    def set_target_address(self, address: int) -> None:
        """Set target ECU address."""
        ...


@dataclass
class InterfaceInfo:
    """Information about a CAN interface."""

    name: str  # e.g. "can0", "vcan0", "/dev/ttyUSB0", "COM3", "sim://kdcan"
    interface_type: str  # "socketcan", "virtual", "kdcan"
    description: str
    is_available: bool
    bitrate: int | None = None


@dataclass
class ConnectionError:
    """Details about a connection error."""

    message: str
    code: str
    recoverable: bool
    suggestion: str


StateChangeCallback = Callable[[ConnectionState, ConnectionState], None]


@dataclass
class ConnectionManager:
    """
    Manages diagnostic connection lifecycle.

    Supports:
    - SocketCAN interfaces (can0, can1, etc.) on Linux
    - Virtual CAN for testing (vcan0)
    - K+DCAN USB cables on any OS (/dev/ttyUSB0, /dev/cu.usbserial-*, COM3)
    """

    config: AppConfig
    _state: ConnectionState = ConnectionState.DISCONNECTED
    _transport: Any = None  # TransportProtocol
    _current_interface: InterfaceInfo | None = None
    _state_callbacks: list[StateChangeCallback] = field(default_factory=list)
    _last_error: ConnectionError | None = None
    _transport_kind: str | None = None

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def current_interface(self) -> InterfaceInfo | None:
        """Currently connected interface."""
        return self._current_interface

    @property
    def last_error(self) -> ConnectionError | None:
        """Last connection error."""
        return self._last_error

    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._state == ConnectionState.CONNECTED

    def add_state_callback(self, callback: StateChangeCallback) -> None:
        """Add a callback for state changes."""
        self._state_callbacks.append(callback)

    def remove_state_callback(self, callback: StateChangeCallback) -> None:
        """Remove a state change callback."""
        if callback in self._state_callbacks:
            self._state_callbacks.remove(callback)

    def discover_interfaces(self) -> list[InterfaceInfo]:
        """
        Discover adapters: K+DCAN cables first, then SocketCAN interfaces.

        Returns:
            List of usable interfaces, best guess first
        """
        interfaces: list[InterfaceInfo] = []
        interfaces.extend(self._discover_kdcan())

        if sys.platform.startswith("linux"):
            try:
                from e92_redline.transport.can_transport import list_can_interfaces

                for iface in list_can_interfaces():
                    is_virtual = iface.startswith("vcan")
                    interfaces.append(
                        InterfaceInfo(
                            name=iface,
                            interface_type="virtual" if is_virtual else "socketcan",
                            description=f"{'Virtual ' if is_virtual else ''}CAN Interface",
                            is_available=True,
                            bitrate=500000,
                        )
                    )
            except Exception as e:
                logger.warning(f"Error discovering CAN interfaces: {e}")

        return interfaces

    def _discover_kdcan(self) -> list[InterfaceInfo]:
        """USB serial ports that could be a K+DCAN cable, plus manual/simulated ports."""
        found: list[InterfaceInfo] = []
        try:
            from e92_redline.core.discovery import PortDiscovery

            for port in PortDiscovery(self.config.last_known_interface).discover_ports():
                found.append(
                    InterfaceInfo(
                        name=port.device,
                        interface_type="kdcan",
                        description=f"K+DCAN cable ({port.name})",
                        is_available=True,
                    )
                )
        except Exception as e:
            logger.warning(f"Error discovering serial ports: {e}")

        known = {i.name for i in found}
        for extra in self.config.connection.extra_ports:
            if extra not in known:
                found.append(InterfaceInfo(extra, "kdcan", "K+DCAN cable (manual port)", True))
        if self.config.connection.simulate:
            from e92_redline.sim.kdcan_cable import SIM_PORT

            found.append(InterfaceInfo(SIM_PORT, "kdcan", "K+DCAN simulator (no car)", True))
        return found

    @staticmethod
    def interface_from_name(name: str) -> InterfaceInfo:
        """Guess the interface type from a bare name."""
        if name.startswith("vcan"):
            return InterfaceInfo(name, "virtual", f"Virtual CAN {name}", True, 500000)
        if name.startswith("can") and "/" not in name:
            return InterfaceInfo(name, "socketcan", f"CAN Interface {name}", True, 500000)
        return InterfaceInfo(name, "kdcan", f"K+DCAN cable {name}", True)

    def connect(self, interface: str | InterfaceInfo) -> bool:
        """
        Establish a diagnostic connection.

        Args:
            interface: Interface to connect to (name or InterfaceInfo)

        Returns:
            True if connection successful
        """
        if self._state == ConnectionState.CONNECTED:
            logger.warning("Already connected, disconnecting first")
            self.disconnect()

        self._set_state(ConnectionState.CONNECTING)

        # Resolve interface
        if isinstance(interface, InterfaceInfo):
            interface_info = interface
        else:
            interface_info = self.interface_from_name(interface)

        is_kdcan = interface_info.interface_type == "kdcan"
        logger.info(f"Connecting to {interface_info.name}...")

        # A K+DCAN cable and a CAN adapter need different transports.
        if self._transport is None or self._transport_kind not in (
            interface_info.interface_type,
            "injected",
        ):
            self._transport = self._create_transport(interface_info.interface_type)
            self._transport_kind = interface_info.interface_type

        try:
            # Attempt to open transport
            bitrate = interface_info.bitrate or 500000
            if not self._transport.open(interface_info.name, bitrate):
                self._set_error(
                    f"Failed to open {interface_info.name}",
                    "OPEN_FAILED",
                    recoverable=True,
                    suggestion=self._open_suggestion(is_kdcan),
                )
                return False

            self._current_interface = interface_info
            self._set_state(ConnectionState.VALIDATING)

            # Validate connection
            if not self._transport.validate():
                self._transport.close()
                self._set_error(
                    "Adapter validation failed",
                    "VALIDATION_FAILED",
                    recoverable=True,
                    suggestion="Check the adapter and vehicle connection",
                )
                return False

            self._set_state(ConnectionState.CONNECTED)

            details: dict[str, Any] = {
                "interface": interface_info.name,
                "type": interface_info.interface_type,
            }
            if is_kdcan:
                details["probe"] = getattr(self._transport, "last_probe", {})
                self.config.last_known_interface = interface_info.name
            log_audit_event(
                "connection_established",
                f"Connected to {interface_info.name}",
                details,
            )

            logger.info(f"Connected successfully to {interface_info.name}")
            return True

        except Exception as e:
            logger.error(f"Connection error: {e}")
            self._set_error(
                str(e),
                "CONNECTION_EXCEPTION",
                recoverable=True,
                suggestion=self._open_suggestion(is_kdcan),
            )
            return False

    def disconnect(self) -> None:
        """Disconnect from the diagnostic interface."""
        if self._transport:
            try:
                if hasattr(self._transport, 'is_open') and self._transport.is_open():
                    self._transport.close()
            except Exception as e:
                logger.warning(f"Error closing transport: {e}")

        interface_name = self._current_interface.name if self._current_interface else "unknown"
        self._current_interface = None
        self._set_state(ConnectionState.DISCONNECTED)

        log_audit_event(
            "connection_closed",
            f"Disconnected from {interface_name}",
        )

        logger.info("Disconnected")

    def set_transport(self, transport: Any) -> None:
        """Set the transport implementation (for testing)."""
        self._transport = transport
        self._transport_kind = "injected"

    def get_transport(self) -> Any:
        """Get the current transport."""
        return self._transport

    def _create_transport(self, kind: str = "socketcan") -> Any:
        """Create the transport for an interface type."""
        if kind == "kdcan":
            from e92_redline.transport.kdcan_transport import KdcanTransport

            logger.info("Using K+DCAN transport (BMW-FAST over USB serial)")
            return KdcanTransport()

        from e92_redline.transport.can_transport import CANTransport

        logger.info("Using CAN transport (SocketCAN)")
        return CANTransport()

    @staticmethod
    def _open_suggestion(is_kdcan: bool) -> str:
        if not is_kdcan:
            return "Check if interface is up: sudo ip link set can0 up type can bitrate 500000"
        if sys.platform.startswith("linux"):
            return (
                "Check the cable is plugged in and that your user can open serial ports: "
                "sudo usermod -aG dialout $USER (then log out and back in). "
                "Close INPA/other tools using the cable."
            )
        if sys.platform == "darwin":
            return "Check the cable is plugged in and no other app (e.g. a terminal) has the port open."
        return (
            "Check the cable shows up under Ports (COM & LPT) in Device Manager and "
            "that no other program (INPA, ISTA, BimmerGeeks app) is using it."
        )

    def obd_source_factory(self) -> tuple[Callable[[], Any], str] | None:
        """How the Live Data page should read OBD-II on the current connection."""
        iface = self._current_interface
        if iface is None or not self.is_connected:
            return None
        if iface.interface_type == "kdcan":
            from e92_redline.live_data.obd_reader import KdcanObdReader

            transport = self._transport
            return (lambda: KdcanObdReader(transport)), iface.description
        from e92_redline.live_data.obd_reader import SocketCanObdReader

        name = iface.name
        return (lambda: SocketCanObdReader(name)), name

    def _set_state(self, new_state: ConnectionState) -> None:
        """Update state and notify callbacks."""
        old_state = self._state
        self._state = new_state

        if old_state != new_state:
            logger.debug(f"Connection state: {old_state.name} -> {new_state.name}")
            for callback in self._state_callbacks:
                try:
                    callback(old_state, new_state)
                except Exception as e:
                    logger.error(f"State callback error: {e}")

    def _set_error(
        self,
        message: str,
        code: str,
        recoverable: bool,
        suggestion: str,
    ) -> None:
        """Set error state with details."""
        self._last_error = ConnectionError(
            message=message,
            code=code,
            recoverable=recoverable,
            suggestion=suggestion,
        )
        self._set_state(ConnectionState.ERROR)
        logger.error(f"Connection error [{code}]: {message}")
