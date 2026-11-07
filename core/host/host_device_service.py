"""Host Device Service for MVentX.

Coordinates cross-platform storage enumeration, multi-attribute classification,
state machine transitions, strong identity tracking, safe mount management, and ejection.
Integrates seamlessly with StorageManager, ISORegistrationManager, and Desktop UI.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from core.host.device_classification import DeviceClassifier, MVentXClassification, MVentXStatus
from core.host.device_identity import DeviceIdentity, revalidate_device_identity
from core.host.device_monitor import DeviceChangeEvent, DeviceMonitor
from core.host.device_state import DeviceLifecycleState, DeviceStateMachine
from core.host.filesystem_access import FilesystemAccessor
from core.host.host_platform import HostPlatform, PlatformType
from core.host.mount_manager import BaseMountManager, MountRecord
from core.host.safe_eject import BaseSafeEjector, EjectResult
from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo
from core.storage.device_manager import DeviceManager, DeviceSafetyError


@dataclass
class HostDeviceTarget:
    """Represents a validated, operational MVentX device ready for management."""
    device_info: DeviceInfo
    identity: DeviceIdentity
    classification: MVentXClassification
    state_machine: DeviceStateMachine
    data_mount: Path
    boot_mount: Optional[Path] = None
    is_virtual: bool = False

    @property
    def display_name(self) -> str:
        if self.is_virtual:
            return f"Virtual Disk ({self.data_mount.name})"
        return f"{self.device_info.name} ({self.data_mount})"


class HostDeviceService:
    """Master cross-platform host device service."""

    def __init__(self, allow_physical_formatting: bool = False) -> None:
        self.platform_type = HostPlatform.get_platform_type()
        self.device_manager = DeviceManager(allow_physical_formatting=allow_physical_formatting)

        # Instantiate platform-specific subsystems
        self.mount_manager: BaseMountManager
        self.safe_ejector: BaseSafeEjector
        self._init_platform_providers()

        # State registries
        self._state_machines: Dict[str, DeviceStateMachine] = {}
        self._identities: Dict[str, DeviceIdentity] = {}
        self._classifications: Dict[str, MVentXClassification] = {}
        self._active_target: Optional[HostDeviceTarget] = None

        # Device hotplug monitor
        self.monitor = DeviceMonitor(
            device_enumerator=self.list_devices,
            poll_interval_seconds=2.5,
        )
        self.monitor.on_device_disconnected = self._handle_device_disconnected

    def _init_platform_providers(self) -> None:
        if self.platform_type == PlatformType.WINDOWS:
            from core.host.windows import WindowsMountManager, WindowsSafeEjector
            self.mount_manager = WindowsMountManager()
            self.safe_ejector = WindowsSafeEjector(self.mount_manager)
        elif self.platform_type == PlatformType.LINUX:
            from core.host.linux import LinuxMountManager, LinuxSafeEjector
            self.mount_manager = LinuxMountManager()
            self.safe_ejector = LinuxSafeEjector(self.mount_manager)
        else:
            # Fallback stub providers
            from core.host.windows import WindowsMountManager, WindowsSafeEjector
            self.mount_manager = WindowsMountManager()
            self.safe_ejector = WindowsSafeEjector(self.mount_manager)

    # --------------------------------------------------------------------------
    # Device Discovery & Classification
    # --------------------------------------------------------------------------

    def list_devices(self) -> List[DeviceInfo]:
        """Enumerate connected storage devices and update state machines."""
        devices = self.device_manager.list_devices()
        for d in devices:
            sm = self.get_state_machine(d.device_id)
            if sm.current_state == DeviceLifecycleState.DETECTED:
                try:
                    sm.transition_to(DeviceLifecycleState.IDENTIFYING, "Extracting hardware and partition identifiers")
                except Exception:
                    pass
            # Build and cache identity
            identity = self.build_identity(d)
            self._identities[d.device_id] = identity
            if sm.can_transition_to(DeviceLifecycleState.IDENTIFIED):
                sm.transition_to(DeviceLifecycleState.IDENTIFIED, "Identity established")
        return devices

    def classify_device(
        self,
        device: DeviceInfo,
        data_mount_override: Optional[Path | str] = None,
        boot_mount_override: Optional[Path | str] = None,
    ) -> MVentXClassification:
        """Classify device without altering files or mounting unprompted."""
        sm = self.get_state_machine(device.device_id)
        if sm.current_state == DeviceLifecycleState.DISCONNECTED:
            sm.transition_to(DeviceLifecycleState.DETECTED, "Device re-detected on bus")
        if sm.current_state == DeviceLifecycleState.DETECTED:
            sm.transition_to(DeviceLifecycleState.IDENTIFYING, "Classifying device")
            sm.transition_to(DeviceLifecycleState.IDENTIFIED, "Device identified")
        if sm.can_transition_to(DeviceLifecycleState.VALIDATING):
            sm.transition_to(DeviceLifecycleState.VALIDATING, "Inspecting partitions and configuration")

        classification = DeviceClassifier.classify_device(
            device=device,
            data_mount_override=data_mount_override,
            boot_mount_override=boot_mount_override,
        )
        self._classifications[device.device_id] = classification

        # Transition state machine based on classification result
        if classification.status == MVentXStatus.VALID_MVENTX:
            if sm.can_transition_to(DeviceLifecycleState.READY):
                sm.transition_to(DeviceLifecycleState.READY, "Valid MVentX device ready for operations")
        elif classification.status == MVentXStatus.INCOMPLETE_INSTALLATION:
            if sm.can_transition_to(DeviceLifecycleState.INCOMPLETE):
                sm.transition_to(DeviceLifecycleState.INCOMPLETE, "Incomplete MVentX installation")
        elif classification.status == MVentXStatus.CORRUPTED_INSTALLATION:
            if sm.can_transition_to(DeviceLifecycleState.CORRUPTED):
                sm.transition_to(DeviceLifecycleState.CORRUPTED, "Corrupted MVentX installation")
        elif classification.status == MVentXStatus.ORDINARY_STORAGE:
            if sm.can_transition_to(DeviceLifecycleState.ORDINARY):
                sm.transition_to(DeviceLifecycleState.ORDINARY, "Standard non-MVentX drive")
        elif classification.status == MVentXStatus.INACCESSIBLE:
            if sm.can_transition_to(DeviceLifecycleState.INACCESSIBLE):
                sm.transition_to(DeviceLifecycleState.INACCESSIBLE, "Device unmounted or locked")

        return classification

    # --------------------------------------------------------------------------
    # State & Identity Tracking
    # --------------------------------------------------------------------------

    def get_state_machine(self, device_id: str) -> DeviceStateMachine:
        """Return or create state machine for device_id."""
        if device_id not in self._state_machines:
            self._state_machines[device_id] = DeviceStateMachine(DeviceLifecycleState.DETECTED)
        return self._state_machines[device_id]

    def build_identity(self, device: DeviceInfo) -> DeviceIdentity:
        """Extract identity attributes from device info."""
        if self.platform_type == PlatformType.WINDOWS:
            from core.host.windows import WindowsDeviceProvider
            provider = WindowsDeviceProvider()
            return provider.build_device_identity(device)
        elif self.platform_type == PlatformType.LINUX:
            from core.host.linux import LinuxDeviceProvider
            provider = LinuxDeviceProvider()
            return provider.build_device_identity(device)
        else:
            from core.host.device_identity import PartitionIdentity
            part_ids = tuple(
                PartitionIdentity(
                    partition_number=p.partition_number,
                    label=p.label,
                    filesystem=p.filesystem,
                    size_bytes=p.size_bytes,
                ) for p in device.partitions
            )
            return DeviceIdentity(
                disk_guid=device.device_id,
                hardware_serial=device.serial_number,
                model=device.model,
                vendor=device.vendor,
                capacity_bytes=device.size_bytes,
                device_path=device.device_id,
                is_virtual=device.is_virtual,
                partitions=part_ids,
            )

    # --------------------------------------------------------------------------
    # Active Target Selection & Management
    # --------------------------------------------------------------------------

    @property
    def active_target(self) -> Optional[HostDeviceTarget]:
        return self._active_target

    def set_active_target(self, target: Optional[HostDeviceTarget]) -> None:
        self._active_target = target

    def select_mventx_device(
        self,
        device: DeviceInfo,
        data_mount_override: Optional[Path | str] = None,
        boot_mount_override: Optional[Path | str] = None,
    ) -> HostDeviceTarget:
        """Verify device safety, classify, and activate as target."""
        # Enforce safety guards: must not be host system disk (non-destructive management)
        self.device_manager.assert_safe_target(device, for_installation=False)

        classification = self.classify_device(
            device=device,
            data_mount_override=data_mount_override,
            boot_mount_override=boot_mount_override,
        )

        if not classification.is_valid:
            reasons = "; ".join(classification.diagnostics)
            raise DeviceSafetyError(f"Cannot activate invalid MVentX device ({classification.status.value}): {reasons}")

        if not classification.data_mount:
            raise DeviceSafetyError("Cannot activate device: data partition is not mounted.")

        identity = self._identities.get(device.device_id) or self.build_identity(device)
        sm = self.get_state_machine(device.device_id)

        target = HostDeviceTarget(
            device_info=device,
            identity=identity,
            classification=classification,
            state_machine=sm,
            data_mount=classification.data_mount,
            boot_mount=classification.boot_mount,
            is_virtual=device.is_virtual,
        )
        self._active_target = target
        return target

    def get_filesystem_accessor(self) -> FilesystemAccessor:
        """Create a verified FilesystemAccessor for the active target with pre-write revalidation."""
        if not self._active_target:
            raise DeviceSafetyError("No active MVentX target selected.")

        target = self._active_target

        def fetcher() -> Optional[DeviceIdentity]:
            # Re-probe device on bus to confirm presence
            devs = self.list_devices()
            for d in devs:
                if d.device_id == target.device_info.device_id:
                    return self.build_identity(d)
            if target.is_virtual and target.device_info.file_path and target.device_info.file_path.exists():
                return target.identity
            return None

        return FilesystemAccessor(
            data_mount=target.data_mount,
            boot_mount=target.boot_mount,
            expected_identity=target.identity,
            identity_fetcher=fetcher,
        )

    # --------------------------------------------------------------------------
    # Safe Ejection
    # --------------------------------------------------------------------------

    def eject_active_device(self) -> EjectResult:
        """Safely eject the active device."""
        if not self._active_target:
            raise DeviceSafetyError("No active target to eject.")

        target = self._active_target
        res = self.safe_ejector.eject_device(
            device_id=target.device_info.device_id,
            identity=target.identity,
            state_machine=target.state_machine,
            data_mount=target.data_mount,
            boot_mount=target.boot_mount,
        )
        if res.success:
            self._active_target = None
        return res

    # --------------------------------------------------------------------------
    # Disconnection Safety Guard
    # --------------------------------------------------------------------------

    def _handle_device_disconnected(self, device_id: str) -> None:
        """Handle disconnection event from monitor."""
        sm = self.get_state_machine(device_id)
        if sm.can_transition_to(DeviceLifecycleState.DISCONNECTED):
            sm.transition_to(DeviceLifecycleState.DISCONNECTED, "Device was unplugged from host bus")

        if self._active_target and self._active_target.device_info.device_id == device_id:
            # Active target disconnected! Clear target immediately to prevent writes
            self._active_target = None
