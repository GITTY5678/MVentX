"""Device Preparation Subsystem for MVentX.

Performs pre-installation validation, sanity checks, and staging environment
preparation for virtual disk images and storage targets.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Optional, Tuple

from core.storage.device_info import DeviceInfo, DeviceType
from core.storage.device_manager import DeviceManager, DeviceSafetyError
from installer.partition_planner import MIN_DISK_CAPACITY_MB


class DevicePreparationError(Exception):
    """Raised when device preparation or pre-validation fails."""


class DevicePreparationService:
    """Prepares and validates devices for the installation engine."""

    def __init__(self, device_manager: Optional[DeviceManager] = None) -> None:
        self.device_manager = device_manager or DeviceManager()

    def prepare_virtual_disk_file(self, target_path: Path | str, size_bytes: int) -> DeviceInfo:
        """Create or initialize a raw virtual disk image for installation.

        Raises:
            DevicePreparationError: If path is invalid or host has insufficient storage.
        """
        p = Path(target_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)

        size_mb = size_bytes / (1024 ** 2)
        if size_mb < MIN_DISK_CAPACITY_MB:
            raise DevicePreparationError(
                f"Requested virtual disk size ({size_mb:.1f} MiB) is smaller than the minimum "
                f"required capacity ({MIN_DISK_CAPACITY_MB} MiB)."
            )

        # Check host available storage
        try:
            usage = shutil.disk_usage(p.parent)
            if usage.free < size_bytes + (50 * 1024 * 1024):  # 50MB safety margin
                raise DevicePreparationError(
                    f"Insufficient host disk space to allocate {size_mb:.1f} MiB virtual disk. "
                    f"Free space: {usage.free / (1024**2):.1f} MiB."
                )
        except OSError:
            pass

        # Create sparse or preallocated file
        try:
            with open(p, "wb") as f:
                f.seek(size_bytes - 1)
                f.write(b"\x00")
        except OSError as exc:
            raise DevicePreparationError(f"Failed to create virtual disk file '{p}': {exc}") from exc

        return self.device_manager.inspect_virtual_disk(p)

    def validate_device_for_installation(
        self,
        device: DeviceInfo,
        required_size_bytes: Optional[int] = None,
    ) -> None:
        """Enforce strict pre-installation safety invariants on the target device."""
        # 1. Assert safety rules (non-system, non-read-only, capacity > 0)
        self.device_manager.assert_safe_target(device)

        # 2. Check minimum capacity
        size = required_size_bytes or device.size_bytes
        size_mb = size / (1024 ** 2)
        if size_mb < MIN_DISK_CAPACITY_MB:
            raise DevicePreparationError(
                f"Device '{device.name}' capacity ({size_mb:.1f} MiB) is below minimum "
                f"required capacity ({MIN_DISK_CAPACITY_MB} MiB)."
            )

