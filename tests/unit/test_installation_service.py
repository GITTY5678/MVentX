"""Unit tests for MVentX Partition Planning and Installation Subsystem (Milestone 9)."""

from pathlib import Path
import tempfile
import unittest

from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo
from core.storage.device_manager import DeviceManager, DeviceSafetyError
from installer.device_preparation import DevicePreparationError, DevicePreparationService
from installer.installation_service import InstallationError, InstallationResult, InstallationService
from installer.installation_verifier import InstallationVerifier
from installer.partition_planner import (
    DEFAULT_ESP_SIZE_MB,
    MIN_DISK_CAPACITY_MB,
    MIN_ESP_SIZE_MB,
    PartitionPlanError,
    PartitionPlanner,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestPartitionPlanner(unittest.TestCase):
    """Test suite for partition boundary calculation and alignment."""

    def test_partition_plan_calculation(self):
        """Verify 1 MiB alignment, valid LBA bounds, and non-overlapping partitions."""
        plan = PartitionPlanner.calculate_plan(
            disk_size_bytes=256 * 1024 * 1024,
            esp_size_mb=64,
        )
        self.assertEqual(plan.sector_size, 512)
        self.assertEqual(plan.first_usable_lba, 2048)  # 1 MiB alignment
        self.assertEqual(plan.esp_partition.start_lba % 2048, 0)
        self.assertEqual(plan.data_partition.start_lba % 2048, 0)
        self.assertGreater(plan.data_partition.start_lba, plan.esp_partition.end_lba)
        self.assertLessEqual(plan.data_partition.end_lba, plan.last_usable_lba)
        self.assertEqual(plan.esp_partition.filesystem, "FAT32")
        self.assertEqual(plan.data_partition.filesystem, "exFAT")

    def test_rejects_insufficient_disk_capacity(self):
        """Verify planner rejects disks smaller than minimum required capacity."""
        with self.assertRaises(PartitionPlanError) as ctx:
            PartitionPlanner.calculate_plan(disk_size_bytes=64 * 1024 * 1024)
        self.assertIn("minimum required capacity", str(ctx.exception))

    def test_rejects_undersized_esp(self):
        """Verify planner rejects ESP sizes smaller than practical minimum."""
        with self.assertRaises(PartitionPlanError) as ctx:
            PartitionPlanner.calculate_plan(
                disk_size_bytes=256 * 1024 * 1024,
                esp_size_mb=16,
            )
        self.assertIn("smaller than the minimum practical size", str(ctx.exception))


class TestDeviceSafety(unittest.TestCase):
    """Test suite verifying strict system drive rejection and safety rules."""

    def setUp(self):
        self.device_manager = DeviceManager(allow_physical_formatting=False)

    def test_rejects_system_disk(self):
        """Verify assertion raises DeviceSafetyError for system disks."""
        sys_disk = DeviceInfo(
            device_id="\\\\.\\PHYSICALDRIVE0",
            name="Samsung NVMe 980 Pro",
            size_bytes=1000 * 1024 * 1024 * 1024,
            is_system_disk=True,
            partitions=[
                PartitionInfo(partition_number=1, label="OS", mount_point="C:\\", is_system=True)
            ],
        )
        with self.assertRaises(DeviceSafetyError) as ctx:
            self.device_manager.assert_safe_target(sys_disk)
        self.assertIn("active Operating System system/boot disk", str(ctx.exception))

    def test_rejects_read_only_device(self):
        """Verify assertion raises DeviceSafetyError for read-only devices."""
        ro_disk = DeviceInfo(
            device_id="\\\\.\\PHYSICALDRIVE2",
            name="SD Card Lock Enabled",
            size_bytes=32 * 1024 * 1024 * 1024,
            is_read_only=True,
        )
        with self.assertRaises(DeviceSafetyError) as ctx:
            self.device_manager.assert_safe_target(ro_disk)
        self.assertIn("read-only", str(ctx.exception))

    def test_blocks_physical_formatting_by_default(self):
        """Verify physical device formatting is blocked by default safety policy."""
        phys_usb = DeviceInfo(
            device_id="\\\\.\\PHYSICALDRIVE3",
            name="SanDisk Ultra USB 3.0",
            size_bytes=16 * 1024 * 1024 * 1024,
            is_removable=True,
            is_system_disk=False,
            device_type=DeviceType.REMOVABLE_USB,
        )
        with self.assertRaises(DeviceSafetyError) as ctx:
            self.device_manager.assert_safe_target(phys_usb)
        self.assertIn("Physical device formatting is currently disabled", str(ctx.exception))


class TestInstallationServiceVirtual(unittest.TestCase):
    """Test suite verifying end-to-end virtual disk installation and verification."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_test_install_svc_"))
        self.installer = InstallationService()

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_virtual_disk_installation(self):
        """Execute complete 12-step installation to virtual disk image and verify 100% pass."""
        test_img = self.temp_dir / "mventx_installed.img"

        steps_called = []
        def on_progress(step: int, total: int, msg: str) -> None:
            steps_called.append((step, msg))

        result = self.installer.install_to_virtual_disk(
            target_image_path=test_img,
            disk_size_mb=256,
            esp_size_mb=64,
            menu_title="MVentX Milestone 9 Test Menu",
            menu_timeout=15,
            progress_callback=on_progress,
        )

        self.assertTrue(result.success, f"Installation failed: {result.error_message}")
        self.assertTrue(test_img.exists())
        self.assertEqual(test_img.stat().st_size, 256 * 1024 * 1024)

        # Verify all 12 steps were reported
        self.assertEqual(len(steps_called), 12)
        self.assertEqual(steps_called[0][0], 1)
        self.assertEqual(steps_called[-1][0], 12)

        # Verify verification report
        rep = result.verification_report
        self.assertIsNotNone(rep)
        self.assertTrue(rep.is_valid)
        self.assertEqual(len(rep.errors), 0)
        self.assertGreaterEqual(rep.passed_count, 10)

    def test_install_to_physical_device_policy_lock(self):
        """Verify install_to_physical_device raises PermissionError when physical formatting is locked."""
        phys_usb = DeviceInfo(
            device_id="\\\\.\\PhysicalDrive1",
            name="SanDisk Cruzer Blade",
            size_bytes=63065554944,
            is_removable=True,
            is_system_disk=False,
            device_type=DeviceType.REMOVABLE_USB,
        )
        with self.assertRaises(PermissionError) as ctx:
            self.installer.install_to_physical_device(
                device=phys_usb,
                expected_fingerprint="dummy_fingerprint",
            )
        self.assertIn("Physical storage device formatting is disabled by policy", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

