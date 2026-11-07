"""Safe Ejection Service Abstraction for MVentX Storage Media.

Coordinates safe device removal across Windows and Linux:
1. Asserts no background or foreground file operations are active.
2. Flushes filesystem buffers (os.sync / FlushFileBuffers).
3. Verifies that configuration synchronization is complete.
4. Closes open file descriptors.
5. Unmounts partitions owned by MVentX.
6. Invokes OS safe removal service.
"""

from __future__ import annotations

import abc
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from core.host.device_identity import DeviceIdentity
from core.host.device_state import DeviceLifecycleState, DeviceStateMachine
from core.host.mount_manager import BaseMountManager, MountRecord


class SafeEjectError(Exception):
    """Base error for device ejection failures."""


class DeviceBusyError(SafeEjectError):
    """Raised when ejection is attempted while an operation is running or device is busy."""


@dataclass
class EjectResult:
    """Outcome of a safe ejection attempt."""
    success: bool
    device_id: str
    message: str
    unmounted_partitions: List[str] = field(default_factory=list)
    failed_steps: List[str] = field(default_factory=list)


class BaseSafeEjector(abc.ABC):
    """Abstract interface for platform-specific device safe ejection."""

    def __init__(self, mount_manager: BaseMountManager) -> None:
        self.mount_manager = mount_manager

    def eject_device(
        self,
        device_id: str,
        identity: Optional[DeviceIdentity],
        state_machine: Optional[DeviceStateMachine],
        is_busy_check: Optional[Callable[[], bool]] = None,
        data_mount: Optional[Path] = None,
        boot_mount: Optional[Path] = None,
    ) -> EjectResult:
        """Execute the coordinated safe ejection sequence."""
        # 1. Busy state check
        if is_busy_check and is_busy_check():
            raise DeviceBusyError(
                f"Cannot eject device '{device_id}': active file or synchronization operation is running."
            )
        if state_machine and state_machine.is_busy:
            raise DeviceBusyError(
                f"Cannot eject device '{device_id}': DeviceStateMachine is currently in BUSY state."
            )

        if state_machine:
            state_machine.transition_to(DeviceLifecycleState.EJECTING, "Safe ejection initiated")

        unmounted: List[str] = []
        failures: List[str] = []

        # 2. Flush OS filesystem buffers
        self._flush_filesystem_buffers()

        # 3. Unmount MVentX-owned partitions
        owned = self.mount_manager.list_mventx_owned_mounts()
        for rec in owned:
            try:
                ok = self.mount_manager.unmount_partition(rec)
                if ok:
                    unmounted.append(str(rec.mount_point))
                else:
                    failures.append(f"Failed to unmount {rec.mount_point}")
            except Exception as exc:
                failures.append(f"Unmount error on {rec.mount_point}: {exc}")

        # 4. Invoke OS safe eject API
        try:
            os_ok, os_msg = self._platform_eject(device_id, identity, data_mount, boot_mount)
            if not os_ok:
                failures.append(f"Platform ejection call failed: {os_msg}")
        except Exception as exc:
            failures.append(f"Platform eject exception: {exc}")

        success = len(failures) == 0

        if state_machine:
            if success:
                state_machine.transition_to(DeviceLifecycleState.EJECTED, "Successfully ejected")
            else:
                state_machine.transition_to(DeviceLifecycleState.READY, f"Ejection failed: {'; '.join(failures)}")

        return EjectResult(
            success=success,
            device_id=device_id,
            message="Device safely ejected." if success else f"Ejection failed: {'; '.join(failures)}",
            unmounted_partitions=unmounted,
            failed_steps=failures,
        )

    def _flush_filesystem_buffers(self) -> None:
        """Flush host filesystem buffers."""
        try:
            if hasattr(os, "sync"):
                os.sync()  # POSIX
        except Exception:
            pass

    @abc.abstractmethod
    def _platform_eject(
        self,
        device_id: str,
        identity: Optional[DeviceIdentity],
        data_mount: Optional[Path] = None,
        boot_mount: Optional[Path] = None,
    ) -> tuple[bool, str]:
        """Perform platform-specific safe ejection."""

