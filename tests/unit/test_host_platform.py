"""Unit tests for MVentX Host Platform and Safe Eject Subsystems."""

import unittest
from pathlib import Path

from core.host.device_identity import DeviceIdentity
from core.host.device_state import DeviceLifecycleState, DeviceStateMachine
from core.host.host_platform import HostPlatform, PlatformType
from core.host.mount_manager import BaseMountManager, MountRecord
from core.host.safe_eject import BaseSafeEjector, DeviceBusyError, EjectResult


class DummyMountManager(BaseMountManager):
    """Stub mount manager for safe eject testing."""

    def locate_mount(self, partition, device_path):
        return None

    def mount_partition(self, partition, device_path, desired_mount=None, read_only=False):
        raise NotImplementedError()

    def unmount_partition(self, mount_record: MountRecord, force: bool = False) -> bool:
        return True


class DummySafeEjector(BaseSafeEjector):
    """Stub safe ejector for testing."""

    def __init__(self, mount_mgr, platform_success: bool = True):
        super().__init__(mount_mgr)
        self.platform_success = platform_success
        self.ejected_call_count = 0

    def _platform_eject(self, device_id, identity, data_mount=None, boot_mount=None):
        self.ejected_call_count += 1
        return self.platform_success, "Dummy eject executed"


class TestHostPlatformAndSafeEject(unittest.TestCase):
    """Test platform detection and safe device ejection logic."""

    def test_platform_detection(self):
        p_type = HostPlatform.get_platform_type()
        self.assertIn(p_type, (PlatformType.WINDOWS, PlatformType.LINUX, PlatformType.MACOS, PlatformType.UNKNOWN))
        caps = HostPlatform.get_capabilities()
        self.assertEqual(caps.platform_type, p_type)

    def test_safe_eject_success(self):
        mount_mgr = DummyMountManager()
        ejector = DummySafeEjector(mount_mgr, platform_success=True)
        sm = DeviceStateMachine(DeviceLifecycleState.READY)
        identity = DeviceIdentity(disk_guid="disk-123", is_virtual=True)

        res = ejector.eject_device(
            device_id="test_dev_0",
            identity=identity,
            state_machine=sm,
        )
        self.assertTrue(res.success)
        self.assertEqual(sm.current_state, DeviceLifecycleState.EJECTED)
        self.assertEqual(ejector.ejected_call_count, 1)

    def test_safe_eject_blocked_when_busy(self):
        mount_mgr = DummyMountManager()
        ejector = DummySafeEjector(mount_mgr, platform_success=True)
        sm = DeviceStateMachine(DeviceLifecycleState.READY)
        sm.transition_to(DeviceLifecycleState.BUSY, "Writing ISO")

        with self.assertRaises(DeviceBusyError):
            ejector.eject_device(
                device_id="test_dev_0",
                identity=None,
                state_machine=sm,
            )
        self.assertEqual(sm.current_state, DeviceLifecycleState.BUSY)
        self.assertEqual(ejector.ejected_call_count, 0)

    def test_safe_eject_recovery_on_platform_failure(self):
        mount_mgr = DummyMountManager()
        failing_ejector = DummySafeEjector(mount_mgr, platform_success=False)
        sm = DeviceStateMachine(DeviceLifecycleState.READY)

        res = failing_ejector.eject_device(
            device_id="test_dev_0",
            identity=None,
            state_machine=sm,
        )
        self.assertFalse(res.success)
        # Should recover back to READY state on failure
        self.assertEqual(sm.current_state, DeviceLifecycleState.READY)


if __name__ == "__main__":
    unittest.main()

