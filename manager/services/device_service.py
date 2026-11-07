"""Device Discovery, Selection, and Host Integration Service for MVentX Manager.

Manages physical MVentX USB drive detection as well as virtual test disk staging mounts.
Integrates HostDeviceService for classification, strong identity tracking, state machines,
and safe ejection. Provides reactive Qt signals for UI components.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from core.host.device_classification import MVentXClassification, MVentXStatus
from core.host.device_identity import DeviceIdentity
from core.host.device_state import DeviceLifecycleState, DeviceStateMachine
from core.host.host_device_service import HostDeviceService, HostDeviceTarget
from core.host.safe_eject import EjectResult
from core.storage.device_detection import DeviceDetector, DiscoveredPartition, MVentXVolumePair
from core.storage.device_info import DeviceInfo
from core.storage.storage_manager import StorageManager


@dataclass
class ActiveTarget:
    """Represents the currently targeted MVentX device or virtual filesystem."""
    data_mount: Path
    boot_mount: Optional[Path]
    is_virtual: bool
    display_name: str
    volume_pair: Optional[MVentXVolumePair] = None
    device_info: Optional[DeviceInfo] = None
    identity: Optional[DeviceIdentity] = None
    classification: Optional[MVentXClassification] = None

    @property
    def has_boot_mount(self) -> bool:
        return self.boot_mount is not None and self.boot_mount.is_dir()


class DeviceService(QObject):
    """Manages active MVentX device selection, host classification, and safe ejection."""

    target_changed = Signal(object)           # Emits ActiveTarget or None
    devices_refreshed = Signal(list)          # Emits List[MVentXVolumePair]
    classifications_updated = Signal(dict)    # Emits Dict[str, MVentXClassification]
    eject_completed = Signal(bool, str)       # Emits (success, message)
    device_disconnected = Signal(str)         # Emits device_id
    device_connected = Signal(str)            # Emits device_name or device_id

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.host_service = HostDeviceService(allow_physical_formatting=False)
        self._active_target: Optional[ActiveTarget] = None
        self._detected_pairs: List[MVentXVolumePair] = []
        self._classifications: Dict[str, MVentXClassification] = {}

        # Wire monitor callbacks to host service
        self.host_service.monitor.on_device_connected = self._on_device_connected
        self.host_service.monitor.on_device_disconnected = self._on_device_disconnected
        self.host_service.monitor.on_device_changed = self._on_device_changed

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
        """Scan system for connected physical MVentX USB drives and classify them."""
        # Enumerate via HostDeviceService
        devs = self.host_service.list_devices()

        self._classifications.clear()
        pairs: List[MVentXVolumePair] = []

        for d in devs:
            # Classify device
            cls_report = self.host_service.classify_device(d)
            self._classifications[d.device_id] = cls_report

            # If it has data partition mounted, expose as volume pair for UI selector
            if cls_report.data_mount and cls_report.data_partition:
                data_dp = DiscoveredPartition(
                    mount_point=cls_report.data_mount,
                    label=cls_report.data_partition.label or "MVX_DATA",
                    filesystem=cls_report.data_partition.filesystem or "EXFAT",
                    total_bytes=cls_report.data_partition.size_bytes,
                    free_bytes=0,
                    is_removable=d.is_removable,
                    is_mventx_data=True,
                    is_mventx_boot=False,
                )
                boot_dp: Optional[DiscoveredPartition] = None
                if cls_report.boot_mount and cls_report.boot_partition:
                    boot_dp = DiscoveredPartition(
                        mount_point=cls_report.boot_mount,
                        label=cls_report.boot_partition.label or "MVX_BOOT",
                        filesystem=cls_report.boot_partition.filesystem or "FAT32",
                        total_bytes=cls_report.boot_partition.size_bytes,
                        free_bytes=0,
                        is_removable=d.is_removable,
                        is_mventx_data=False,
                        is_mventx_boot=True,
                    )
                pairs.append(MVentXVolumePair(data_partition=data_dp, boot_partition=boot_dp))

        # Fallback to DeviceDetector if PowerShell returned no mounted pairs
        if not pairs:
            try:
                pairs = DeviceDetector.find_mventx_drives()
            except Exception:
                pairs = []

        self._detected_pairs = pairs
        self.devices_refreshed.emit(self._detected_pairs)
        self.classifications_updated.emit(self._classifications)

        # Check if active target is still present
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

        # Build virtual device info and classify
        v_dev = self.host_service.device_manager.inspect_virtual_disk_or_dir(data_p) if hasattr(self.host_service.device_manager, "inspect_virtual_disk_or_dir") else None

        target = ActiveTarget(
            data_mount=data_p,
            boot_mount=boot_p,
            is_virtual=True,
            display_name=display_name,
            volume_pair=None,
            device_info=v_dev,
        )
        self.set_active_target(target)

    def set_active_target(self, target: Optional[ActiveTarget]) -> None:
        """Update active target and notify subscribers."""
        self._active_target = target
        if target:
            # Set target in host_service as well
            if target.device_info:
                try:
                    self.host_service.select_mventx_device(
                        device=target.device_info,
                        data_mount_override=target.data_mount,
                        boot_mount_override=target.boot_mount,
                    )
                except Exception:
                    pass
        else:
            self.host_service.set_active_target(None)

        self.target_changed.emit(target)

    def eject_active_device(self) -> EjectResult:
        """Safely eject the active MVentX device."""
        if not self._active_target:
            res = EjectResult(success=False, device_id="", message="No active device selected to eject.")
            self.eject_completed.emit(False, res.message)
            return res

        target = self._active_target
        dev_id = target.device_info.device_id if target.device_info else str(target.data_mount)

        # Virtual targets don't have physical storage bus ejection; flush and detach
        if target.is_virtual:
            self.set_active_target(None)
            res = EjectResult(
                success=True,
                device_id=dev_id,
                message=f"Virtual disk '{target.display_name}' safely unmounted.",
            )
            self.eject_completed.emit(True, res.message)
            return res

        res = self.host_service.safe_ejector.eject_device(
            device_id=dev_id,
            identity=target.identity,
            state_machine=None,
            data_mount=target.data_mount,
            boot_mount=target.boot_mount,
        )

        if res.success:
            self.set_active_target(None)
            self.refresh_devices()

        self.eject_completed.emit(res.success, res.message)
        return res

    def _on_device_connected(self, dev: DeviceInfo) -> None:
        """Signal handler when a storage device is plugged in."""
        name = dev.name or dev.device_id
        self.device_connected.emit(name)
        self.refresh_devices()

    def _on_device_changed(self, dev: DeviceInfo) -> None:
        """Signal handler when storage volume mounts or status changes."""
        self.refresh_devices()

    def _on_device_disconnected(self, device_id: str) -> None:
        """Signal handler when an active device is unplugged."""
        self.device_disconnected.emit(device_id)
        if self._active_target:
            active_id = self._active_target.device_info.device_id if self._active_target.device_info else ""
            if active_id == device_id or str(self._active_target.data_mount) == device_id:
                self.set_active_target(None)
                self.refresh_devices()
