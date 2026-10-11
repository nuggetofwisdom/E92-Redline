"""Metric -> imperial display conversions. Raw data and CSV logs stay metric."""

from collections.abc import Callable

_IMPERIAL: dict[str, tuple[str, Callable[[float], float]]] = {
    "°C": ("°F", lambda c: c * 9 / 5 + 32),
    "km/h": ("mph", lambda kmh: kmh * 0.621371),
    "g/s": ("lb/min", lambda gs: gs * 0.132277),
}


def convert(value: float, unit: str, imperial: bool) -> tuple[float, str]:
    """Return (display value, display unit) for the chosen unit system."""
    if imperial and unit in _IMPERIAL:
        new_unit, fn = _IMPERIAL[unit]
        return fn(value), new_unit
    return value, unit


def display_unit(unit: str, imperial: bool) -> str:
    return convert(0.0, unit, imperial)[1]

