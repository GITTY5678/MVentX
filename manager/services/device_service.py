"""Device Discovery and Selection Service for MVentX Manager.

Manages physical MVentX USB drive detection as well as virtual test disk staging mounts.
Provides reactive signals when devices are connected, disconnected, or changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from core.storage.device_detection import DeviceDetector, MVentXVolumePair
from core.storage.storage_manager import StorageManager


@dataclass
class ActiveTarget:
    """Represents the currently targeted MVentX device or virtual filesystem."""
    data_mount: Path
    boot_mount: Optional[Path]
    is_virtual: bool
    display_name: str
    volume_pair: Optional[MVentXVolumePair] = None

    @property
    def has_boot_mount(self) -> bool:
        return self.boot_mount is not None and self.boot_mount.is_dir()


class DeviceService(QObject):
    """Manages active MVentX device selection and discovery."""

    target_changed = Signal(object)      # Emits ActiveTarget or None
    devices_refreshed = Signal(list)     # Emits List[MVentXVolumePair]

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._active_target: Optional[ActiveTarget] = None
        self._detected_pairs: List[MVentXVolumePair] = []

    @property
    def active_target(self) -> Optional[ActiveTarget]:
        return self._active_target

    def has_active_target(self) -> bool:
        return self._active_target is not None

    def get_storage_manager(self) -> Optional[StorageManager]:
        """Return a StorageManager instance configured for the active target."""
        if not self._active_target:
            return None
        return StorageManager(
            data_mount=self._active_target.data_mount,
            boot_mount=self._active_target.boot_mount,
        )

    def refresh_devices(self) -> List[MVentXVolumePair]:
        """Scan system for connected physical MVentX USB drives."""
        try:
            self._detected_pairs = DeviceDetector.find_mventx_drives()
        except Exception:
            self._detected_pairs = []

        self.devices_refreshed.emit(self._detected_pairs)

        # If we had a physical target that is no longer present, clear it
        if self._active_target and not self._active_target.is_virtual:
            matching = [
                p for p in self._detected_pairs
                if Path(p.data_partition.mount_point).resolve() == self._active_target.data_mount.resolve()
            ]
            if not matching:
                self.set_active_target(None)

        return self._detected_pairs

    def set_active_device(self, pair: MVentXVolumePair) -> None:
        """Set a detected physical MVentX volume pair as the active target."""
        data_p = Path(pair.data_partition.mount_point)
        boot_p = Path(pair.boot_partition.mount_point) if pair.boot_partition else None

        name = f"USB Drive ({pair.data_partition.mount_point})"
        target = ActiveTarget(
            data_mount=data_p,
            boot_mount=boot_p,
            is_virtual=False,
            display_name=name,
            volume_pair=pair,
        )
        self.set_active_target(target)

    def set_custom_target(
        self,
        data_mount: Path | str,
        boot_mount: Optional[Path | str] = None,
        display_name: str = "Virtual Disk",
    ) -> None:
        """Set a custom or virtual directory mount as the active target."""
        data_p = Path(data_mount).resolve()
        boot_p = Path(boot_mount).resolve() if boot_mount else None

        target = ActiveTarget(
            data_mount=data_p,
            boot_mount=boot_p,
            is_virtual=True,
            display_name=display_name,
            volume_pair=None,
        )
        self.set_active_target(target)

    def set_active_target(self, target: Optional[ActiveTarget]) -> None:
        """Update active target and emit notification."""
        self._active_target = target
        self.target_changed.emit(target)

