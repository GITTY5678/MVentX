"""Cross-Platform Mount Management Abstraction for MVentX.

Coordinates safe discovery, mounting, ownership tracking, permission checking,
and unmounting of MVentX boot and data partitions across operating systems.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.storage.device_info import PartitionInfo


class MountError(Exception):
    """Base error for mount management operations."""


class MountPermissionError(MountError):
    """Raised when host denies mount/unmount permissions."""


class PartitionBusyError(MountError):
    """Raised when partition is held open by another process."""


@dataclass
class MountRecord:
    """Tracks a partition's mount point and whether MVentX owns the mount lifecycle."""
    partition_number: int
    device_path: str
    mount_point: Path
    filesystem: str
    is_mventx_owned: bool = False       # True if mounted by MVentX; False if mounted by OS/user
    is_read_only: bool = False
    label: str = ""


class BaseMountManager(abc.ABC):
    """Abstract interface for host filesystem mount managers."""

    def __init__(self) -> None:
        self._tracked_mounts: Dict[str, MountRecord] = {}

    @abc.abstractmethod
    def locate_mount(self, partition: PartitionInfo, device_path: str) -> Optional[Path]:
        """Check if partition is currently mounted on the host system."""

    @abc.abstractmethod
    def mount_partition(
        self,
        partition: PartitionInfo,
        device_path: str,
        desired_mount: Optional[Path | str] = None,
        read_only: bool = False,
    ) -> MountRecord:
        """Mount an unmounted partition. Must never overwrite existing mount points."""

    @abc.abstractmethod
    def unmount_partition(self, mount_record: MountRecord, force: bool = False) -> bool:
        """Safely unmount a partition. Never unmount user-mounted partitions unprompted."""

    def register_existing_mount(
        self,
        partition_number: int,
        device_path: str,
        mount_point: Path | str,
        filesystem: str,
        label: str = "",
        read_only: bool = False,
    ) -> MountRecord:
        """Record an already existing OS/user mount point (not owned by MVentX)."""
        p = Path(mount_point).resolve()
        key = f"{device_path}:{partition_number}"
        rec = MountRecord(
            partition_number=partition_number,
            device_path=device_path,
            mount_point=p,
            filesystem=filesystem,
            is_mventx_owned=False,
            is_read_only=read_only,
            label=label,
        )
        self._tracked_mounts[key] = rec
        return rec

    def get_tracked_mount(self, device_path: str, partition_number: int) -> Optional[MountRecord]:
        key = f"{device_path}:{partition_number}"
        return self._tracked_mounts.get(key)

    def list_mventx_owned_mounts(self) -> List[MountRecord]:
        """Return all mounts actively created and owned by this MVentX process."""
        return [m for m in self._tracked_mounts.values() if m.is_mventx_owned]

