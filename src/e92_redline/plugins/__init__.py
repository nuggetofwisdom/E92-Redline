"""
E92Redline Plugin System

Provides interfaces and loading for optional datapacks and plugins.
"""

from e92_redline.plugins.base import (
    PluginInterface,
    DatapackInterface,
    PluginMetadata,
)
from e92_redline.plugins.loader import PluginLoader

__all__ = [
    "PluginInterface",
    "DatapackInterface",
    "PluginMetadata",
    "PluginLoader",
]
