"""Linux Mount Manager for MVentX.

Locates mounted partitions via findmnt / procfs, mounts unmounted partitions via udisksctl,
and provides safe unmounting on Linux distributions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from core.host.mount_manager import BaseMountManager, MountError, MountRecord
from core.storage.device_info import PartitionInfo


class LinuxMountManager(BaseMountManager):
    """Mount manager implementation for Linux host platform."""

    def locate_mount(self, partition: PartitionInfo, device_path: str) -> Optional[Path]:
        """Check if partition is mounted by inspecting partition.mount_point or findmnt."""
        if partition.mount_point:
            p = Path(partition.mount_point)
            if p.exists():
                return p

        # Query findmnt if partition name available
        part_dev = f"{device_path}{partition.partition_number}"
        if shutil.which("findmnt"):
            try:
                res = subprocess.run(
                    ["findmnt", "-n", "-o", "TARGET", part_dev],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0 and res.stdout.strip():
                    target = Path(res.stdout.strip())
                    if target.exists():
                        return target
            except Exception:
                pass
        return None

    def mount_partition(
        self,
        partition: PartitionInfo,
        device_path: str,
        desired_mount: Optional[Path | str] = None,
        read_only: bool = False,
    ) -> MountRecord:
        """Mount partition using udisksctl mount or mount command."""
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

        part_dev = f"{device_path}{partition.partition_number}"
        mount_point: Optional[Path] = None

        # Try udisksctl mount
        if shutil.which("udisksctl"):
            cmd = ["udisksctl", "mount", "-b", part_dev]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if res.returncode == 0:
                    # Output: "Mounted /dev/sdb2 at /media/user/MVX_DATA"
                    out = res.stdout.strip()
                    if " at " in out:
                        mount_str = out.split(" at ")[-1].rstrip(".")
                        mount_point = Path(mount_str)
            except Exception:
                pass

        if not mount_point or not mount_point.exists():
            raise MountError(f"Failed to mount partition {part_dev} on Linux.")

        key = f"{device_path}:{partition.partition_number}"
        rec = MountRecord(
            partition_number=partition.partition_number,
            device_path=device_path,
            mount_point=mount_point,
            filesystem=partition.filesystem,
            is_mventx_owned=True,
            is_read_only=read_only,
            label=partition.label,
        )
        self._tracked_mounts[key] = rec
        return rec

    def unmount_partition(self, mount_record: MountRecord, force: bool = False) -> bool:
        """Unmount a partition via udisksctl unmount or umount."""
        if not mount_record.is_mventx_owned and not force:
            return True

        target_str = str(mount_record.mount_point)
        # Try udisksctl unmount
        if shutil.which("udisksctl"):
            cmd = ["udisksctl", "unmount", "-m", target_str]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if res.returncode == 0:
                    key = f"{mount_record.device_path}:{mount_record.partition_number}"
                    self._tracked_mounts.pop(key, None)
                    return True
            except Exception:
                pass

        # Fallback to umount
        if shutil.which("umount"):
            try:
                res = subprocess.run(["umount", target_str], capture_output=True, text=True, timeout=15)
                if res.returncode == 0:
                    key = f"{mount_record.device_path}:{mount_record.partition_number}"
                    self._tracked_mounts.pop(key, None)
                    return True
            except Exception:
                pass

        return False

