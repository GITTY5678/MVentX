"""Unit tests for PhysicalDeviceWorkflow and physical storage safety protocol (Milestone 13)."""

from pathlib import Path
import unittest
from unittest.mock import MagicMock

from core.host.device_classification import MVentXStatus
from core.host.device_identity import DeviceIdentity
from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo
from core.storage.device_manager import DeviceManager, DeviceSafetyError
from installer.physical_device_workflow import (
    DestructiveOperationPlan,
    PhysicalDeviceWorkflow,
    PhysicalSafetyViolationError,
)


class TestPhysicalDeviceWorkflow(unittest.TestCase):
    """Test suite for physical USB preparation safety shields and workflow."""

    def setUp(self) -> None:
        self.mock_dev_mgr = MagicMock(spec=DeviceManager)
        self.mock_dev_mgr.allow_physical_formatting = False
        def _check_safe(d: DeviceInfo):
            if d.is_system_disk:
                raise DeviceSafetyError(f"Cannot format system disk '{d.name}'")
        self.mock_dev_mgr.assert_safe_target.side_effect = _check_safe

        self.workflow = PhysicalDeviceWorkflow(
            device_manager=self.mock_dev_mgr,
            allow_physical_formatting=False,
        )

        self.valid_usb = DeviceInfo(
            device_id="\\\\.\\PhysicalDrive3",
            name="SanDisk Ultra USB 3.0",
            size_bytes=32 * 1024 * 1024 * 1024,  # 32 GB
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            is_system_disk=False,
            vendor="SanDisk",
            model="Ultra USB 3.0",
            serial_number="4C530001",
            partitions=[
                PartitionInfo(
                    partition_number=1,
                    name="Partition 1",
                    size_bytes=32 * 1024 * 1024 * 1024,
                    filesystem="fat32",
                    mount_point="E:\\",
                    label="OLD_USB",
                )
            ],
        )

    def test_identify_and_validate_rejects_system_disk(self) -> None:
        sys_disk = DeviceInfo(
            device_id="\\\\.\\PhysicalDrive0",
            name="Samsung NVMe SSD 1TB",
            size_bytes=1000 * 1024 * 1024 * 1024,
            device_type=DeviceType.FIXED_DISK,
            is_removable=False,
            is_system_disk=True,
        )
        with self.assertRaises(DeviceSafetyError):
            self.workflow.identify_and_validate_device(sys_disk)

    def test_identify_and_validate_rejects_non_removable_disk(self) -> None:
        fixed_disk = DeviceInfo(
            device_id="\\\\.\\PhysicalDrive1",
            name="Seagate Barracuda 2TB",
            size_bytes=2000 * 1024 * 1024 * 1024,
            device_type=DeviceType.FIXED_DISK,
            is_removable=False,
            is_system_disk=False,
        )
        with self.assertRaises(PhysicalSafetyViolationError) as ctx:
            self.workflow.identify_and_validate_device(fixed_disk)
        self.assertIn("not classified as removable USB storage", str(ctx.exception))

    def test_identify_and_validate_rejects_undersized_disk(self) -> None:
        tiny_disk = DeviceInfo(
            device_id="\\\\.\\PhysicalDrive2",
            name="Tiny USB Stick",
            size_bytes=32 * 1024 * 1024,  # 32 MiB (< 128 MiB)
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            is_system_disk=False,
        )
        with self.assertRaises(PhysicalSafetyViolationError) as ctx:
            self.workflow.identify_and_validate_device(tiny_disk)
        self.assertIn("below minimum required capacity", str(ctx.exception))

    def test_identify_and_validate_valid_usb(self) -> None:
        identity = self.workflow.identify_and_validate_device(self.valid_usb)
        self.assertIsInstance(identity, DeviceIdentity)
        self.assertEqual(identity.device_path, "\\\\.\\PhysicalDrive3")
        self.assertEqual(identity.hardware_serial, "4C530001")
        self.assertTrue(len(identity.compute_fingerprint()) > 0)

    def test_generate_plan(self) -> None:
        plan = self.workflow.generate_plan(self.valid_usb, esp_size_mb=64)
        self.assertIsInstance(plan, DestructiveOperationPlan)
        self.assertEqual(plan.device_id, "\\\\.\\PhysicalDrive3")
        self.assertEqual(plan.vendor, "SanDisk")
        self.assertEqual(plan.capacity_gb, 32.0)
        self.assertTrue(any("WIPE" in a for a in plan.destructive_actions))
        self.assertTrue(any("ALLOCATE: Partition 1 'MVX_BOOT'" in a for a in plan.destructive_actions))
        self.assertTrue(any("ALLOCATE: Partition 2 'MVX_DATA'" in a for a in plan.destructive_actions))

        summary = plan.summary()
        self.assertIn("DESTRUCTIVE PHYSICAL DRIVE INITIALIZATION PLAN", summary)
        self.assertIn("SanDisk Ultra USB 3.0", summary)
        self.assertIn("32.00 GB", summary)

    def test_two_phase_confirmation_policy_lock(self) -> None:
        # Default: allow_physical_formatting is False
        plan = self.workflow.generate_plan(self.valid_usb)
        with self.assertRaises(PhysicalSafetyViolationError) as ctx:
            self.workflow.verify_two_phase_confirmation(
                plan=plan,
                confirmed_phase_one=True,
                confirmed_phase_two=True,
                typed_device_confirmation="SanDisk Ultra USB 3.0",
            )
        self.assertIn("Physical device preparation is disabled by system policy", str(ctx.exception))

    def test_two_phase_confirmation_validation(self) -> None:
        # Enable physical formatting
        unlocked_workflow = PhysicalDeviceWorkflow(
            device_manager=self.mock_dev_mgr,
            allow_physical_formatting=True,
        )
        plan = unlocked_workflow.generate_plan(self.valid_usb)

        # Phase 1 missing
        with self.assertRaises(PhysicalSafetyViolationError) as ctx:
            unlocked_workflow.verify_two_phase_confirmation(
                plan=plan,
                confirmed_phase_one=False,
                confirmed_phase_two=True,
                typed_device_confirmation="SanDisk Ultra USB 3.0",
            )
        self.assertIn("Phase 1 confirmation checkbox", str(ctx.exception))

        # Phase 2 missing
        with self.assertRaises(PhysicalSafetyViolationError) as ctx:
            unlocked_workflow.verify_two_phase_confirmation(
                plan=plan,
                confirmed_phase_one=True,
                confirmed_phase_two=False,
                typed_device_confirmation="SanDisk Ultra USB 3.0",
            )
        self.assertIn("Phase 2 destructive modal confirmation", str(ctx.exception))

        # Typed mismatch
        with self.assertRaises(PhysicalSafetyViolationError) as ctx:
            unlocked_workflow.verify_two_phase_confirmation(
                plan=plan,
                confirmed_phase_one=True,
                confirmed_phase_two=True,
                typed_device_confirmation="Wrong USB Drive",
            )
        self.assertIn("Device confirmation mismatch", str(ctx.exception))

        # Successful confirmation with drive name
        unlocked_workflow.verify_two_phase_confirmation(
            plan=plan,
            confirmed_phase_one=True,
            confirmed_phase_two=True,
            typed_device_confirmation="SanDisk Ultra USB 3.0",
        )

        # Successful confirmation with "YES"
        unlocked_workflow.verify_two_phase_confirmation(
            plan=plan,
            confirmed_phase_one=True,
            confirmed_phase_two=True,
            typed_device_confirmation="YES",
        )

    def test_revalidate_device_before_write(self) -> None:
        initial_identity = DeviceIdentity.from_device_info(self.valid_usb)
        fingerprint = initial_identity.compute_fingerprint()

        # Success case: device present and unchanged
        self.mock_dev_mgr.list_devices.return_value = [self.valid_usb]
        revalidated = self.workflow.revalidate_device_before_write(self.valid_usb, fingerprint)
        self.assertEqual(revalidated.device_id, self.valid_usb.device_id)

        # Device disappeared
        self.mock_dev_mgr.list_devices.return_value = []
        with self.assertRaises(PhysicalSafetyViolationError) as ctx:
            self.workflow.revalidate_device_before_write(self.valid_usb, fingerprint)
        self.assertIn("disappeared from the storage bus", str(ctx.exception))

        # Device swapped / fingerprint altered
        swapped_usb = DeviceInfo(
            device_id="\\\\.\\PhysicalDrive3",
            name="Different Drive",
            size_bytes=64 * 1024 * 1024 * 1024,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            serial_number="DIFFERENT_SERIAL",
        )
        self.mock_dev_mgr.list_devices.return_value = [swapped_usb]
        with self.assertRaises(PhysicalSafetyViolationError) as ctx:
            self.workflow.revalidate_device_before_write(self.valid_usb, fingerprint)
        self.assertIn("Device identity shifted", str(ctx.exception))

        # Device marked as system disk
        promoted_system = DeviceInfo(
            device_id="\\\\.\\PhysicalDrive3",
            name="SanDisk Ultra USB 3.0",
            size_bytes=32 * 1024 * 1024 * 1024,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            is_system_disk=True,
            serial_number="4C530001",
        )
        promoted_fp = DeviceIdentity.from_device_info(promoted_system).compute_fingerprint()
        self.mock_dev_mgr.list_devices.return_value = [promoted_system]
        with self.assertRaises(PhysicalSafetyViolationError) as ctx:
            self.workflow.revalidate_device_before_write(promoted_system, promoted_fp)
        self.assertIn("flagged as an active system disk", str(ctx.exception))

    def test_checklists(self) -> None:
        boot_list = PhysicalDeviceWorkflow.get_hardware_boot_checklist()
        self.assertTrue(len(boot_list) >= 7)
        self.assertTrue(any("UEFI" in item for item in boot_list))

        linux_list = PhysicalDeviceWorkflow.get_linux_test_checklist()
        self.assertTrue(len(linux_list) >= 6)
        self.assertTrue(any("ISO" in item for item in linux_list))


if __name__ == "__main__":
    unittest.main()
