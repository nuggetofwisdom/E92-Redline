"""
Pytest configuration and fixtures for E92Redline tests.
"""

import pytest
from pathlib import Path

from e92_redline.core.config import AppConfig
from e92_redline.core.safety import SafetyManager
from e92_redline.core.vehicle import VehicleProfile
from e92_redline.bmw.module_registry import ModuleRegistry


@pytest.fixture
def app_config() -> AppConfig:
    """Create test application configuration."""
    return AppConfig()


@pytest.fixture
def safety_manager() -> SafetyManager:
    """Create test safety manager."""
    return SafetyManager()


@pytest.fixture
def vehicle_profile() -> VehicleProfile:
    """Create test vehicle profile."""
    return VehicleProfile()


@pytest.fixture
def module_registry() -> ModuleRegistry:
    """Create module registry."""
    return ModuleRegistry()


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Create temporary directory for test files."""
    return tmp_path
