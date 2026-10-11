"""
E92Redline Transport Layer

Provides abstracted transport implementations for diagnostic communication.
Supports SocketCAN for proper CAN bus access on Linux.
"""

from e92_redline.transport.base import BaseTransport, TransportError
from e92_redline.transport.can_transport import CANTransport

__all__ = [
    "BaseTransport",
    "TransportError",
    "CANTransport",
]
