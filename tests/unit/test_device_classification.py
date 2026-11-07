"""Unit tests for MVentX Multi-Attribute Device Classification."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.host.device_classification import (
    DeviceClassifier,
    MVentXClassification,
    MVentXStatus,
)
from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo


class TestDeviceClassification(unittest.TestCase):
    """Test classification of storage devices without alteration or auto-mounting."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_test_cls_"))
        self.data_mount = self.temp_dir / "data"
        self.boot_mount = self.temp_dir / "boot"

        # Initialize valid directory skeleton
        (self.data_mount / "ISO").mkdir(parents=True)
        (self.data_mount / "config").mkdir(parents=True)
        (self.data_mount / "metadata").mkdir(parents=True)
        (self.data_mount / "backups").mkdir(parents=True)

        valid_config = {
            "version": "1.0.0",
            "meta": {"title": "Test MVentX", "theme": "mventx", "timeout": 10},
            "environments": [
                {
                    "id": "env-001",
                    "display_name": "Ubuntu Test",
                    "type": "iso",
                    "iso_path": "/ISO/ubuntu.iso",
                    "boot_method": "linux_casper",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }
        with open(self.data_mount / "config" / "mventx.json", "w", encoding="utf-8") as f:
            json.dump(valid_config, f)

        # Boot mount files
        (self.boot_mount / "EFI" / "BOOT").mkdir(parents=True)
        (self.boot_mount / "boot" / "grub").mkdir(parents=True)
        (self.boot_mount / "EFI" / "BOOT" / "BOOTX64.EFI").write_bytes(b"TEST_EFI_BINARY_DATA" * 50)
        (self.boot_mount / "boot" / "grub" / "grub.cfg").write_text("menuentry 'test' {}", encoding="utf-8")
        (self.boot_mount / "boot" / "grub" / "mventx_menu.cfg").write_text("# fallback", encoding="utf-8")

        self.p1 = PartitionInfo(
            partition_number=1,
            name="Partition 1",
            label="MVX_BOOT",
            filesystem="FAT32",
            size_bytes=134217728,
            type_guid="c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
            mount_point=str(self.boot_mount),
            is_mventx_boot=True,
        )
        self.p2 = PartitionInfo(
            partition_number=2,
            name="Partition 2",
            label="MVX_DATA",
            filesystem="EXFAT",
            size_bytes=401587712,
            type_guid="ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
            mount_point=str(self.data_mount),
            is_mventx_data=True,
        )

        self.valid_device = DeviceInfo(
            device_id=r"\\.\PHYSICALDRIVE3",
            name="Test USB Drive",
            size_bytes=536870912,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            partitions=[self.p1, self.p2],
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_classify_valid_mventx_device(self):
        cls_report = DeviceClassifier.classify_device(self.valid_device)
        self.assertEqual(cls_report.status, MVentXStatus.VALID_MVENTX)
        self.assertTrue(cls_report.is_valid)
        self.assertEqual(cls_report.environment_count, 1)
        self.assertEqual(cls_report.menu_title, "Test MVentX")
        self.assertEqual(cls_report.menu_timeout, 10)

    def test_classify_ordinary_device_without_mventx(self):
        ordinary = DeviceInfo(
            device_id=r"\\.\PHYSICALDRIVE4",
            name="Backup Drive",
            size_bytes=1000000000,
            device_type=DeviceType.FIXED_DISK,
            partitions=[
                PartitionInfo(
                    partition_number=1,
                    label="DATA",
                    filesystem="NTFS",
                    size_bytes=1000000000,
                )
            ],
        )
        cls_report = DeviceClassifier.classify_device(ordinary)
        self.assertEqual(cls_report.status, MVentXStatus.ORDINARY_STORAGE)
        self.assertFalse(cls_report.is_valid)

    def test_classify_incomplete_installation_missing_boot(self):
        incomplete = DeviceInfo(
            device_id=r"\\.\PHYSICALDRIVE5",
            name="Half MVentX",
            size_bytes=536870912,
            partitions=[self.p2],  # Only MVX_DATA, no MVX_BOOT
        )
        cls_report = DeviceClassifier.classify_device(incomplete)
        self.assertEqual(cls_report.status, MVentXStatus.INCOMPLETE_INSTALLATION)
        self.assertFalse(cls_report.is_valid)
        self.assertTrue(any("Missing EFI boot partition" in d for d in cls_report.diagnostics))

    def test_classify_corrupted_json_syntax(self):
        # Corrupt JSON syntax in mventx.json
        cfg_file = self.data_mount / "config" / "mventx.json"
        cfg_file.write_text("{ unclosed_json: true, ", encoding="utf-8")

        cls_report = DeviceClassifier.classify_device(self.valid_device)
        self.assertEqual(cls_report.status, MVentXStatus.CORRUPTED_INSTALLATION)
        self.assertFalse(cls_report.is_valid)
        self.assertTrue(any("Corrupted configuration JSON syntax" in d for d in cls_report.diagnostics))

    def test_classify_schema_validation_failure(self):
        # Write valid JSON that violates schema (missing 'version' or wrong type)
        cfg_file = self.data_mount / "config" / "mventx.json"
        invalid_schema_data = {
            "meta": {"title": "No Version"},
            "environments": "NOT_A_LIST",  # Must be array
        }
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(invalid_schema_data, f)

        cls_report = DeviceClassifier.classify_device(self.valid_device)
        self.assertEqual(cls_report.status, MVentXStatus.CORRUPTED_INSTALLATION)
        self.assertFalse(cls_report.is_valid)
        self.assertTrue(any("Configuration schema validation failed" in d for d in cls_report.diagnostics))

    def test_classify_inaccessible_mount(self):
        p2_unmounted = PartitionInfo(
            partition_number=2,
            label="MVX_DATA",
            filesystem="EXFAT",
            size_bytes=401587712,
            type_guid="ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
            mount_point=None,  # Not mounted!
            is_mventx_data=True,
        )
        dev = DeviceInfo(
            device_id=r"\\.\PHYSICALDRIVE6",
            name="Unmounted USB",
            size_bytes=536870912,
            partitions=[self.p1, p2_unmounted],
        )
        cls_report = DeviceClassifier.classify_device(dev)
        self.assertEqual(cls_report.status, MVentXStatus.INACCESSIBLE)
        self.assertFalse(cls_report.is_valid)


if __name__ == "__main__":
    unittest.main()
