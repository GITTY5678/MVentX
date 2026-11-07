"""Windows Storage Device Provider redirect for MVentX.

Exposes WindowsDeviceProvider and normalize_serial_number from the core.host.windows package.
"""

from core.host.windows.windows_device_provider import (
    WindowsDeviceProvider,
    normalize_serial_number,
)

__all__ = ["WindowsDeviceProvider", "normalize_serial_number"]

