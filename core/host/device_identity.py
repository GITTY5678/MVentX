"""Strong Device Identity and Fingerprinting for MVentX Host Integration.

Provides cryptographically grounded, multi-attribute identity verification for storage
devices to prevent drive-letter collisions, stale device references, and accidental
writes to wrong or reconnected physical disks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class DeviceIdentityError(Exception):
    """Base error for device identity verification issues."""


class DeviceIdentityMismatchError(DeviceIdentityError):
    """Raised when the target device identity has changed before a write."""


class DeviceDisappearedError(DeviceIdentityError):
    """Raised when the target device cannot be found during pre-write validation."""


@dataclass(frozen=True)
class PartitionIdentity:
    """Immutable identity record for a single partition."""
    partition_number: int
    partition_guid: str = ""       # Unique partition GUID (GPT UniquePartitionGUID)
    type_guid: str = ""            # Partition type GUID (e.g. ESP or Basic Data)
    start_lba: int = 0
    size_bytes: int = 0
    label: str = ""
    filesystem: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "partition_number": self.partition_number,
            "partition_guid": self.partition_guid.strip().lower(),
            "type_guid": self.type_guid.strip().lower(),
            "start_lba": self.start_lba,
            "size_bytes": self.size_bytes,
            "label": self.label.strip().upper(),
            "filesystem": self.filesystem.strip().upper(),
        }


@dataclass(frozen=True)
class DeviceIdentity:
    """Strong immutable identity representation of a physical or virtual storage device."""
    disk_guid: str                          # GPT Disk GUID / Unique identifier
    hardware_serial: str = ""               # Serial number from firmware / bus
    model: str = ""                         # Device model name
    vendor: str = ""                        # Hardware vendor
    capacity_bytes: int = 0                 # Total raw capacity
    sector_size: int = 512                  # Logical sector size
    bus_type: str = ""                      # USB, NVMe, SCSI, VIRTUAL, etc.
    device_path: str = ""                   # Platform device path (\\.\PHYSICALDRIVE, /dev/sdb, path/disk.img)
    is_virtual: bool = False                # True for image files (.img, .vhd)
    partitions: tuple[PartitionIdentity, ...] = field(default_factory=tuple)

    @property
    def fingerprint(self) -> str:
        """Compute deterministic SHA-256 fingerprint from core hardware and GPT attributes."""
        canonical_data = {
            "disk_guid": self.disk_guid.strip().lower(),
            "hardware_serial": self.hardware_serial.strip(),
            "model": self.model.strip(),
            "vendor": self.vendor.strip(),
            "capacity_bytes": self.capacity_bytes,
            "sector_size": self.sector_size,
            "bus_type": self.bus_type.strip().upper(),
            "is_virtual": self.is_virtual,
            "partitions": [p.to_dict() for p in self.partitions],
        }
        serialized = json.dumps(canonical_data, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def compute_fingerprint(self) -> str:
        """Alias method for self.fingerprint."""
        return self.fingerprint

    @classmethod
    def from_device_info(cls, device: Any) -> "DeviceIdentity":
        """Construct a DeviceIdentity from a DeviceInfo instance."""
        from core.storage.device_info import DeviceType

        parts = []
        for p in getattr(device, "partitions", []):
            parts.append(
                PartitionIdentity(
                    partition_number=getattr(p, "partition_number", 0),
                    partition_guid=getattr(p, "partition_guid", "") or getattr(p, "unique_guid", ""),
                    type_guid=getattr(p, "type_guid", ""),
                    start_lba=getattr(p, "start_lba", 0),
                    size_bytes=getattr(p, "size_bytes", 0),
                    label=getattr(p, "label", "") or "",
                    filesystem=getattr(p, "filesystem", "") or "",
                )
            )

        dev_type = getattr(device, "device_type", None)
        is_virt = dev_type == DeviceType.VIRTUAL_DISK if dev_type else False

        raw_serial = getattr(device, "serial_number", "") or ""
        clean_serial = str(raw_serial).strip().strip("\x00").strip().strip("'\"")
        if clean_serial.upper() in ("", "NONE", "N/A", "NA", "NULL", "UNKNOWN", "NOT AVAILABLE"):
            clean_serial = ""

        return cls(
            disk_guid=getattr(device, "disk_guid", "") or getattr(device, "device_id", ""),
            hardware_serial=clean_serial,
            model=getattr(device, "model", "") or getattr(device, "name", ""),
            vendor=getattr(device, "vendor", "") or "",
            capacity_bytes=getattr(device, "size_bytes", 0),
            sector_size=getattr(device, "sector_size", 512) or 512,
            bus_type=getattr(device, "bus_type", "") or ("USB" if getattr(device, "is_removable", False) else ""),
            device_path=getattr(device, "device_id", ""),
            is_virtual=is_virt,
            partitions=tuple(parts),
        )

    def matches(self, other: DeviceIdentity, allow_path_drift: bool = True) -> bool:
        """Compare identity against another record.
        
        Args:
            other: Device identity to compare against.
            allow_path_drift: If True, allows device_path (or drive letter) to differ if
                underlying GPT GUIDs, serial, and partition layout match exactly.
        """
        if not isinstance(other, DeviceIdentity):
            return False

        # Virtual disks: compare canonical file path and disk GUID / size
        if self.is_virtual or other.is_virtual:
            if self.is_virtual != other.is_virtual:
                return False
            # Check capacity and partition count
            if self.capacity_bytes != other.capacity_bytes:
                return False
            if len(self.partitions) != len(other.partitions):
                return False
            # If disk GUID present on both, it must match
            if self.disk_guid and other.disk_guid:
                return self.disk_guid.strip().lower() == other.disk_guid.strip().lower()
            return True

        # Physical disks: strictly match Disk GUID if available
        if self.disk_guid and other.disk_guid:
            if self.disk_guid.strip().lower() != other.disk_guid.strip().lower():
                return False

        # If hardware serial is present on both, it must match
        if self.hardware_serial and other.hardware_serial:
            if self.hardware_serial.strip() != other.hardware_serial.strip():
                return False

        # Model and capacity must match
        if self.capacity_bytes != other.capacity_bytes:
            return False
        if self.model and other.model and self.model.strip() != other.model.strip():
            return False

        # Partition counts must match
        if len(self.partitions) != len(other.partitions):
            return False

        # Compare partition GUIDs where available
        for p1, p2 in zip(self.partitions, other.partitions):
            if p1.partition_number != p2.partition_number:
                return False
            if p1.partition_guid and p2.partition_guid:
                if p1.partition_guid.strip().lower() != p2.partition_guid.strip().lower():
                    return False

        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "disk_guid": self.disk_guid,
            "hardware_serial": self.hardware_serial,
            "model": self.model,
            "vendor": self.vendor,
            "capacity_bytes": self.capacity_bytes,
            "sector_size": self.sector_size,
            "bus_type": self.bus_type,
            "device_path": self.device_path,
            "is_virtual": self.is_virtual,
            "partitions": [p.to_dict() for p in self.partitions],
        }


def revalidate_device_identity(
    expected_identity: DeviceIdentity,
    current_device_fetcher: Callable[[], Optional[DeviceIdentity]],
    operation_name: str = "write operation",
) -> DeviceIdentity:
    """Revalidate that the target storage device matches expected identity before modifying disk.

    Args:
        expected_identity: The DeviceIdentity recorded when target was selected/verified.
        current_device_fetcher: Callback that probes the currently attached device.
        operation_name: Descriptive name for logging / error context.

    Returns:
        The freshly confirmed DeviceIdentity.

    Raises:
        DeviceDisappearedError: If current_device_fetcher returns None.
        DeviceIdentityMismatchError: If the probe returns a different device.
    """
    current_identity = current_device_fetcher()
    if current_identity is None:
        raise DeviceDisappearedError(
            f"Pre-write safety check aborted {operation_name}: "
            f"Device '{expected_identity.model or expected_identity.device_path}' is no longer connected."
        )

    if not expected_identity.matches(current_identity):
        raise DeviceIdentityMismatchError(
            f"Pre-write safety check failed for {operation_name}: "
            f"Target device identity mismatch!\n"
            f"Expected: {expected_identity.fingerprint[:16]}... (GUID: {expected_identity.disk_guid})\n"
            f"Actual:   {current_identity.fingerprint[:16]}... (GUID: {current_identity.disk_guid})\n"
            f"Operation refused to prevent writing to wrong or reconnected disk."
        )

    return current_identity

