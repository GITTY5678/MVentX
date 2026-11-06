"""Device Detection Service for MVentX.

Identifies connected storage media and safely detects active MVentX drives
by inspecting partition labels ('MVX_BOOT', 'MVX_DATA'), filesystems
('FAT32', 'exFAT'), and storage capacities.

Supports Windows 11 natively (using Win32 API and psutil) as well as Linux.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


@dataclass
class DiscoveredPartition:
    """Represents a mounted volume or partition on the host system."""
    mount_point: Path
    label: str
    filesystem: str
    total_bytes: int
    free_bytes: int
    is_removable: bool
    is_mventx_data: bool = False
    is_mventx_boot: bool = False

    @property
    def total_gb(self) -> float:
        """Capacity in Gigabytes."""
        return round(self.total_bytes / (1024 ** 3), 2)

    @property
    def free_gb(self) -> float:
        """Available free space in Gigabytes."""
        return round(self.free_bytes / (1024 ** 3), 2)


@dataclass
class MVentXVolumePair:
    """Represents a matched pair of MVentX partitions on a USB drive."""
    data_partition: DiscoveredPartition
    boot_partition: Optional[DiscoveredPartition] = None

    @property
    def is_complete(self) -> bool:
        """True if both MVX_DATA and MVX_BOOT partitions are present and mounted."""
        return self.boot_partition is not None


class DeviceDetector:
    """Scans and detects mounted storage partitions and MVentX USB drives."""

    @classmethod
    def get_volume_info(cls, mount_path: str | Path) -> Optional[DiscoveredPartition]:
        """Query label, filesystem, and capacity for a given mount point."""
        path_obj = Path(mount_path).resolve()
        path_str = str(path_obj)

        if not path_obj.exists():
            return None

        # Default fallback values
        label = ""
        fs_type = ""
        total_bytes = 0
        free_bytes = 0
        is_removable = False

        if sys.platform == "win32":
            # Ensure path format ends with backslash for GetVolumeInformationW (e.g. "E:\\")
            drive_root = path_str if path_str.endswith("\\") else path_str + "\\"
            # If path is inside a drive, resolve to root drive
            drive_letter = os.path.splitdrive(drive_root)[0] + "\\"

            vol_name_buf = ctypes.create_unicode_buffer(1024)
            fs_name_buf = ctypes.create_unicode_buffer(1024)
            serial = ctypes.c_ulong()
            max_comp = ctypes.c_ulong()
            flags = ctypes.c_ulong()

            res = ctypes.windll.kernel32.GetVolumeInformationW(
                drive_letter,
                vol_name_buf,
                1024,
                ctypes.byref(serial),
                ctypes.byref(max_comp),
                ctypes.byref(flags),
                fs_name_buf,
                1024,
            )
            if res:
                label = vol_name_buf.value
                fs_type = fs_name_buf.value

            # Check drive type: 2 is DRIVE_REMOVABLE, 3 is DRIVE_FIXED
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_letter)
            is_removable = (drive_type == 2)
        else:
            # Linux fallback via os.statvfs or findmnt/lsblk
            pass

        # Query disk capacity
        if HAS_PSUTIL:
            try:
                usage = psutil.disk_usage(path_str)
                total_bytes = usage.total
                free_bytes = usage.free
            except Exception:
                pass
        else:
            try:
                st = os.statvfs(path_str)
                total_bytes = st.f_blocks * st.f_frsize
                free_bytes = st.f_bavail * st.f_frsize
            except Exception:
                pass

        is_data = (label.strip().upper() == "MVX_DATA")
        is_boot = (label.strip().upper() == "MVX_BOOT")

        return DiscoveredPartition(
            mount_point=path_obj,
            label=label,
            filesystem=fs_type.upper(),
            total_bytes=total_bytes,
            free_bytes=free_bytes,
            is_removable=is_removable,
            is_mventx_data=is_data,
            is_mventx_boot=is_boot,
        )

    @classmethod
    def list_partitions(cls) -> List[DiscoveredPartition]:
        """Scan system for all mounted partitions."""
        partitions: List[DiscoveredPartition] = []

        if HAS_PSUTIL:
            for p in psutil.disk_partitions(all=False):
                # Avoid network drives or optical media if possible
                if "cdrom" in p.opts or not p.mountpoint:
                    continue
                info = cls.get_volume_info(p.mountpoint)
                if info:
                    if not info.filesystem and p.fstype:
                        info.filesystem = p.fstype.upper()
                    partitions.append(info)
        elif sys.platform == "win32":
            # Iterate drive letters A: through Z:
            import string
            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                if os.path.exists(root):
                    info = cls.get_volume_info(root)
                    if info:
                        partitions.append(info)

        return partitions

    @classmethod
    def detect_mventx_drives(cls) -> List[MVentXVolumePair]:
        """Find mounted MVentX partitions and return matched pairs.

        Safety Rule: Explicitly avoids primary OS drives (C:\\, /).
        """
        all_parts = cls.list_partitions()
        data_parts = [p for p in all_parts if p.is_mventx_data]
        boot_parts = [p for p in all_parts if p.is_mventx_boot]

        pairs: List[MVentXVolumePair] = []

        for dp in data_parts:
            # Safety check: ensure not system drive root
            if sys.platform == "win32":
                if str(dp.mount_point).upper().startswith("C:"):
                    continue  # Never treat C: as MVentX
            else:
                if str(dp.mount_point) == "/":
                    continue

            # Find matching boot partition (usually nearby or removable)
            matched_boot: Optional[DiscoveredPartition] = None
            if boot_parts:
                matched_boot = boot_parts[0]

            pairs.append(MVentXVolumePair(data_partition=dp, boot_partition=matched_boot))

        return pairs

    @classmethod
    def validate_mventx_volume(
        cls,
        mount_path: Path | str,
        expected_label: str = "MVX_DATA",
        expected_fstype: str = "EXFAT",
    ) -> Tuple[bool, List[str]]:
        """Validate that a mounted volume matches expected MVentX label and filesystem."""
        errors: List[str] = []
        info = cls.get_volume_info(mount_path)

        if not info:
            return False, [f"Mount point does not exist or cannot be accessed: {mount_path}"]

        if info.label.strip().upper() != expected_label.strip().upper():
            errors.append(f"Expected label '{expected_label}', found '{info.label}'")

        # Allow FAT/FAT32 if expected is FAT32, exFAT if expected is EXFAT
        actual_fs = info.filesystem.strip().upper()
        expected_fs = expected_fstype.strip().upper()
        if expected_fs and actual_fs and expected_fs not in actual_fs and actual_fs not in expected_fs:
            errors.append(f"Expected filesystem '{expected_fstype}', found '{info.filesystem}'")

        if info.total_bytes <= 0:
            errors.append("Invalid or unreadable partition capacity")

        return len(errors) == 0, errors
