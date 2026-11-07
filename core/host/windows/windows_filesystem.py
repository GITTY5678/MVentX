"""Windows Filesystem Utilities for MVentX.

Handles Win32 path normalization, volume locking, and permission testing.
"""

from __future__ import annotations

import ctypes
from pathlib import Path


class WindowsFilesystemUtils:
    """Windows-specific filesystem helpers."""

    @staticmethod
    def get_volume_serial_number(drive_letter: str) -> str:
        """Retrieve 32-bit volume serial number formatted as XXXX-XXXX."""
        root = f"{drive_letter.rstrip(':')}:\\"
        serial = ctypes.c_ulong()
        try:
            ret = ctypes.windll.kernel32.GetVolumeInformationW(
                root, None, 0, ctypes.byref(serial), None, None, None, 0
            )
            if ret:
                val = serial.value
                return f"{val >> 16:04X}-{val & 0xFFFF:04X}"
        except Exception:
            pass
        return ""

    @staticmethod
    def is_drive_removable(drive_letter: str) -> bool:
        """Check if drive is DRIVE_REMOVABLE (type 2)."""
        root = f"{drive_letter.rstrip(':')}:\\"
        try:
            drv_type = ctypes.windll.kernel32.GetDriveTypeW(root)
            return drv_type == 2  # DRIVE_REMOVABLE
        except Exception:
            return False

