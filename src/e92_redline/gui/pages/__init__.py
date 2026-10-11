"""
E92Redline GUI Pages

Contains the main content pages for the application.
"""

from e92_redline.gui.pages.connect_page import ConnectPage
from e92_redline.gui.pages.quick_test_page import QuickTestPage
from e92_redline.gui.pages.fault_memory_page import FaultMemoryPage
from e92_redline.gui.pages.services_page import ServicesPage
from e92_redline.gui.pages.export_page import ExportPage

__all__ = [
    "ConnectPage",
    "QuickTestPage",
    "FaultMemoryPage",
    "ServicesPage",
    "ExportPage",
]
