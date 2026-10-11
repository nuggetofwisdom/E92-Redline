"""
E92Redline BMW-Specific Package

Contains BMW E92 M3 specific module definitions, scanning logic,
and service implementations.
"""

from e92_redline.bmw.module_registry import ModuleRegistry, ModuleDefinition
from e92_redline.bmw.module_scan import ModuleScanner, ScanResult
from e92_redline.bmw.services import ServiceManager, BatteryRegistrationService

__all__ = [
    "ModuleRegistry",
    "ModuleDefinition",
    "ModuleScanner",
    "ScanResult",
    "ServiceManager",
    "BatteryRegistrationService",
]
