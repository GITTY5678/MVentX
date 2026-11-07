"""Partition Management Layer for MVentX.

Implements partition creation and formatting for virtual disk images and
physical storage devices with strict safety boundaries.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Dict, Optional, Tuple

from core.storage.device_info import DeviceInfo
from installer.partition_planner import PartitionPlan, PartitionPlanner
from scripts.windows.make_dual_partition_disk import build_dual_partition_gpt_disk


class PartitionManagerError(Exception):
    """Raised when partitioning or formatting fails."""


class PartitionManager:
    """Manages disk partitioning and filesystem formatting."""

    def __init__(self, allow_physical_formatting: bool = False) -> None:
        self.allow_physical_formatting = allow_physical_formatting

    def create_and_format_virtual_disk(
        self,
        output_image_path: Path | str,
        esp_staging_dir: Path | str,
        data_staging_dir: Path | str,
        disk_size_mb: int = 256,
        esp_size_mb: int = 64,
    ) -> Path:
        """Create a complete dual-partition GPT virtual disk image from staged directories.

        Parameters:
            output_image_path: Destination path for the raw disk image (.img).
            esp_staging_dir: Directory containing staged EFI bootloader files.
            data_staging_dir: Directory containing staged MVX_DATA files and configuration.
            disk_size_mb: Total raw disk size in Megabytes.
            esp_size_mb: Size of the MVX_BOOT FAT32 EFI partition in Megabytes.
        """
        out_p = Path(output_image_path).resolve()
        esp_p = Path(esp_staging_dir).resolve()
        data_p = Path(data_staging_dir).resolve()

        # Validate partition plan before writing
        plan = PartitionPlanner.calculate_plan(
            disk_size_bytes=disk_size_mb * 1024 * 1024,
            esp_size_mb=esp_size_mb,
        )

        try:
            build_dual_partition_gpt_disk(
                esp_source_dir=esp_p,
                output_path=out_p,
                disk_size_mb=disk_size_mb,
                esp_size_mb=esp_size_mb,
                data_source_dir=data_p,
            )
        except Exception as exc:
            raise PartitionManagerError(f"Failed constructing virtual disk image: {exc}") from exc

        return out_p

    def format_physical_device(
        self,
        device: DeviceInfo,
        esp_size_mb: int = 64,
        confirmed: bool = False,
    ) -> Tuple[str, str]:
        """Format a physical storage device.

        CRITICAL SAFETY CONTROL:
        Physical formatting is strictly blocked unless allow_physical_formatting is True
        AND confirmed is True.
        """
        if not self.allow_physical_formatting or not confirmed:
            raise PermissionError(
                "CRITICAL SAFETY SHIELD: Physical storage device formatting is disabled by policy. "
                "Virtual disk target mode must be used for testing and validation."
            )

        from core.host.host_platform import HostPlatform
        if HostPlatform.is_windows():
            from installer.windows_physical_installer import WindowsPhysicalInstaller
            win_installer = WindowsPhysicalInstaller(allow_physical_formatting=self.allow_physical_formatting)
            disk_num = win_installer.extract_disk_number(device.device_id)
            return win_installer.prepare_physical_partitions(disk_number=disk_num, esp_size_mb=esp_size_mb)

        raise NotImplementedError("Physical device formatting will be enabled in future releases after validation.")
