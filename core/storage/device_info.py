"""Device Information Models for MVentX Storage Architecture.

Defines structured representations of physical and virtual storage devices,
partition tables, mount points, and security attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class DeviceType(str, Enum):
    """Storage device physical or virtual classification."""
    REMOVABLE_USB = "removable_usb"
    FIXED_DISK = "fixed_disk"
    VIRTUAL_DISK = "virtual_disk"
    OPTICAL_DISC = "optical_disc"
    UNKNOWN = "unknown"


@dataclass
class PartitionInfo:
    """Represents a single partition on a storage device."""
    partition_number: int
    name: str = ""
    label: str = ""
    filesystem: str = ""
    size_bytes: int = 0
    start_lba: int = 0
    end_lba: int = 0
    type_guid: str = ""
    mount_point: Optional[str] = None
    is_bootable: bool = False
    is_active: bool = False
    is_system: bool = False
    is_mventx_boot: bool = False
    is_mventx_data: bool = False

    @property
    def size_mb(self) -> float:
        """Capacity in Megabytes."""
        return round(self.size_bytes / (1024 ** 2), 2)

    @property
    def size_gb(self) -> float:
        """Capacity in Gigabytes."""
        return round(self.size_bytes / (1024 ** 3), 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "partition_number": self.partition_number,
            "name": self.name,
            "label": self.label,
            "filesystem": self.filesystem,
            "size_bytes": self.size_bytes,
            "size_mb": self.size_mb,
            "start_lba": self.start_lba,
            "end_lba": self.end_lba,
            "type_guid": self.type_guid,
            "mount_point": self.mount_point,
            "is_bootable": self.is_bootable,
            "is_active": self.is_active,
            "is_system": self.is_system,
            "is_mventx_boot": self.is_mventx_boot,
            "is_mventx_data": self.is_mventx_data,
        }


@dataclass
class DeviceInfo:
    """Represents a physical or virtual storage device."""
    device_id: str                      # Stable identifier (e.g., '\\\\.\\PHYSICALDRIVE2', '/dev/sdb', 'C:/path/disk.img')
    name: str                           # Display friendly name
    vendor: str = ""                    # Hardware vendor
    model: str = ""                     # Device model
    serial_number: str = ""             # Serial number when available
    size_bytes: int = 0                 # Total raw capacity in bytes
    sector_size: int = 512              # Logical sector size
    device_type: DeviceType = DeviceType.UNKNOWN
    is_removable: bool = False
    is_read_only: bool = False
    is_system_disk: bool = False        # True if holds operating system boot/system partition
    partition_table_type: str = "none"  # 'gpt', 'mbr', or 'none'
    partitions: List[PartitionInfo] = field(default_factory=list)
    file_path: Optional[Path] = None    # Set for virtual disk images

    @property
    def size_mb(self) -> float:
        """Capacity in Megabytes."""
        return round(self.size_bytes / (1024 ** 2), 2)

    @property
    def size_gb(self) -> float:
        """Capacity in Gigabytes."""
        return round(self.size_bytes / (1024 ** 3), 2)

    @property
    def is_virtual(self) -> bool:
        """True if backed by an image file (.img, .vhd, .vhdx)."""
        return self.device_type == DeviceType.VIRTUAL_DISK or self.file_path is not None

    @property
    def has_mventx(self) -> bool:
        """True if the device has both MVX_BOOT and MVX_DATA partitions."""
        has_boot = any(p.is_mventx_boot or p.label.upper() == "MVX_BOOT" for p in self.partitions)
        has_data = any(p.is_mventx_data or p.label.upper() == "MVX_DATA" for p in self.partitions)
        return has_boot and has_data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "vendor": self.vendor,
            "model": self.model,
            "serial_number": self.serial_number,
            "size_bytes": self.size_bytes,
            "size_gb": self.size_gb,
            "sector_size": self.sector_size,
            "device_type": self.device_type.value,
            "is_removable": self.is_removable,
            "is_read_only": self.is_read_only,
            "is_system_disk": self.is_system_disk,
            "is_virtual": self.is_virtual,
            "has_mventx": self.has_mventx,
            "partition_table_type": self.partition_table_type,
            "partitions": [p.to_dict() for p in self.partitions],
            "file_path": str(self.file_path) if self.file_path else None,
        }

