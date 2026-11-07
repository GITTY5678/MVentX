"""Cross-Platform Host Integration Layer for MVentX."""

from core.host.device_classification import (
    DeviceClassifier,
    MVentXClassification,
    MVentXStatus,
)
from core.host.device_identity import (
    DeviceDisappearedError,
    DeviceIdentity,
    DeviceIdentityError,
    DeviceIdentityMismatchError,
    PartitionIdentity,
    revalidate_device_identity,
)
from core.host.device_monitor import DeviceChangeEvent, DeviceMonitor
from core.host.device_state import (
    DeviceLifecycleState,
    DeviceStateMachine,
    InvalidStateTransitionError,
)
from core.host.filesystem_access import (
    ConfigurationRollbackError,
    ConfigurationSyncError,
    FilesystemAccessor,
    FilesystemAccessError,
    PermissionDeniedError,
)
from core.host.host_device_service import HostDeviceService, HostDeviceTarget
from core.host.host_platform import HostPlatform, PlatformCapabilities, PlatformType
from core.host.mount_manager import (
    BaseMountManager,
    MountError,
    MountPermissionError,
    MountRecord,
    PartitionBusyError,
)
from core.host.safe_eject import BaseSafeEjector, DeviceBusyError, EjectResult, SafeEjectError

__all__ = [
    # State Machine
    "DeviceLifecycleState",
    "DeviceStateMachine",
    "InvalidStateTransitionError",
    # Device Identity
    "DeviceIdentity",
    "PartitionIdentity",
    "DeviceIdentityError",
    "DeviceIdentityMismatchError",
    "DeviceDisappearedError",
    "revalidate_device_identity",
    # Classification
    "MVentXStatus",
    "MVentXClassification",
    "DeviceClassifier",
    # Platform
    "PlatformType",
    "PlatformCapabilities",
    "HostPlatform",
    # Filesystem & Sync
    "FilesystemAccessor",
    "FilesystemAccessError",
    "PermissionDeniedError",
    "ConfigurationSyncError",
    "ConfigurationRollbackError",
    # Mount & Eject
    "BaseMountManager",
    "MountRecord",
    "MountError",
    "MountPermissionError",
    "PartitionBusyError",
    "BaseSafeEjector",
    "SafeEjectError",
    "DeviceBusyError",
    "EjectResult",
    # Monitoring
    "DeviceMonitor",
    "DeviceChangeEvent",
    # Master Service
    "HostDeviceService",
    "HostDeviceTarget",
]

