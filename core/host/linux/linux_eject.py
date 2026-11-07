"""Linux Safe Device Ejector for MVentX.

Executes safe device removal on Linux distributions via udisksctl power-off or eject.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from core.host.device_identity import DeviceIdentity
from core.host.mount_manager import BaseMountManager
from core.host.safe_eject import BaseSafeEjector


class LinuxSafeEjector(BaseSafeEjector):
    """Safe ejection implementation for Linux host platform."""

    def _platform_eject(
        self,
        device_id: str,
        identity: Optional[DeviceIdentity],
        data_mount: Optional[Path] = None,
        boot_mount: Optional[Path] = None,
    ) -> tuple[bool, str]:
        """Eject device via udisksctl power-off or eject."""
        if identity and identity.is_virtual:
            return True, "Virtual disk safely released."

        # Prefer udisksctl power-off
        if shutil.which("udisksctl"):
            cmd = ["udisksctl", "power-off", "-b", device_id]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
                if res.returncode == 0:
                    return True, f"Device {device_id} safely powered off."
            except Exception:
                pass

        # Fallback to eject command
        if shutil.which("eject"):
            try:
                res = subprocess.run(["eject", device_id], capture_output=True, text=True, timeout=12)
                if res.returncode == 0:
                    return True, f"Device {device_id} safely ejected."
            except Exception:
                pass

        return False, f"Could not power off or eject {device_id} (missing udisksctl and eject)."

