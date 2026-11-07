"""Windows Mount Manager for MVentX.

Locates mounted drive letters, handles assigning drive letters to unmounted partitions,
and provides safe unmounting on Windows 10/11 without modifying partition data.
"""

from __future__ import annotations

import os
import string
import subprocess
from pathlib import Path
from typing import Optional

from core.host.mount_manager import BaseMountManager, MountError, MountRecord
from core.storage.device_info import PartitionInfo


class WindowsMountManager(BaseMountManager):
    """Mount manager implementation for Windows."""

    def locate_mount(self, partition: PartitionInfo, device_path: str) -> Optional[Path]:
        """Return the partition's active mount point (e.g. 'E:\\') if available."""
        if partition.mount_point:
            p = Path(partition.mount_point)
            if p.exists():
                return p
        return None

    def mount_partition(
        self,
        partition: PartitionInfo,
        device_path: str,
        desired_mount: Optional[Path | str] = None,
        read_only: bool = False,
    ) -> MountRecord:
        """Assign an available drive letter to an unmounted partition on Windows."""
        # If already mounted, register and return
        existing = self.locate_mount(partition, device_path)
        if existing:
            return self.register_existing_mount(
                partition_number=partition.partition_number,
                device_path=device_path,
                mount_point=existing,
                filesystem=partition.filesystem,
                label=partition.label,
                read_only=read_only,
            )

        # Extract disk number from device_path (e.g. "\\.\PHYSICALDRIVE2" -> 2)
        disk_num = None
        if "PHYSICALDRIVE" in device_path.upper():
            try:
                disk_num = int(device_path.upper().split("PHYSICALDRIVE")[-1])
            except Exception:
                disk_num = None

        if disk_num is None:
            raise MountError(f"Cannot mount partition on non-physical device path: {device_path}")

        # Choose an unused drive letter (from Z down to D)
        target_letter = self._find_available_drive_letter()
        if not target_letter:
            raise MountError("No available drive letters on host system.")

        # Assign drive letter via PowerShell
        ps_cmd = (
            f"Get-Partition -DiskNumber {disk_num} -PartitionNumber {partition.partition_number} | "
            f"Set-Partition -NewDriveLetter {target_letter}"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res.returncode != 0:
                raise MountError(f"PowerShell Set-Partition failed: {res.stderr.strip() or res.stdout.strip()}")
        except Exception as exc:
            raise MountError(f"Failed to assign drive letter {target_letter}: {exc}") from exc

        mount_p = Path(f"{target_letter}:\\")
        key = f"{device_path}:{partition.partition_number}"
        rec = MountRecord(
            partition_number=partition.partition_number,
            device_path=device_path,
            mount_point=mount_p,
            filesystem=partition.filesystem,
            is_mventx_owned=True,
            is_read_only=read_only,
            label=partition.label,
        )
        self._tracked_mounts[key] = rec
        return rec

    def unmount_partition(self, mount_record: MountRecord, force: bool = False) -> bool:
        """Remove assigned drive letter from a partition on Windows."""
        # Extract letter from "E:\" -> "E"
        letter = str(mount_record.mount_point).rstrip(":\\").upper()
        if not letter or len(letter) != 1 or letter not in string.ascii_uppercase:
            return False

        # If not owned by MVentX and force is False, do not unmount
        if not mount_record.is_mventx_owned and not force:
            return True

        ps_cmd = (
            f"Get-Partition -DriveLetter {letter} | "
            f"Remove-PartitionAccessPath -Accesspath '{letter}:\\'"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=15,
            )
            ok = (res.returncode == 0)
            if ok:
                key = f"{mount_record.device_path}:{mount_record.partition_number}"
                self._tracked_mounts.pop(key, None)
            return ok
        except Exception:
            return False

    @staticmethod
    def _find_available_drive_letter() -> Optional[str]:
        """Find an unused uppercase drive letter."""
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for idx in range(25, 3, -1):  # Z down to E
            if not (bitmask & (1 << idx)):
                return chr(ord('A') + idx)
        return None

