"""Unit tests for MVentX Milestone 2: Dual-Partition USB Storage Architecture."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.storage.disk_validator import (
    BASIC_DATA_GUID,
    ESP_TYPE_GUID,
    DiskValidationError,
    DiskValidator,
)
from core.storage.device_detection import DeviceDetector, DiscoveredPartition
from core.storage.storage_manager import StorageManager
from scripts.windows.make_dual_partition_disk import build_dual_partition_gpt_disk


class TestDualPartitionStorage(unittest.TestCase):
    """Test suite verifying dual-partition layout, filesystems, and storage management."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_m2_test_"))
        self.esp_src = self.temp_dir / "esp"
        self.esp_src.mkdir()

        # Prepare staged boot files for MVX_BOOT
        (self.esp_src / "EFI" / "BOOT").mkdir(parents=True)
        (self.esp_src / "boot" / "grub" / "fonts").mkdir(parents=True)

        (self.esp_src / "EFI" / "BOOT" / "BOOTX64.EFI").write_bytes(b"MZ_DUMMY_GRUB_EFI_PAYLOAD")
        (self.esp_src / "boot" / "grub" / "grub.cfg").write_text("set timeout=10\nset default=0\n", encoding="utf-8")
        (self.esp_src / "boot" / "grub" / "mventx_menu.cfg").write_text(
            'menuentry "Test Environment" --id "test" { echo "OK"; }\n',
            encoding="utf-8",
        )
        (self.esp_src / "boot" / "grub" / "fonts" / "ascii.pf2").write_bytes(b"FONT_DATA")

        # Output disk image path
        self.disk_path = self.temp_dir / "mventx_dual_test.img"

        # Build standard 256MB dual-partition disk
        build_dual_partition_gpt_disk(
            esp_source_dir=self.esp_src,
            output_path=self.disk_path,
            disk_size_mb=256,
            esp_size_mb=64,
        )

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_gpt_two_partitions_and_size(self):
        """Test that disk image has exactly two partitions and correct total size."""
        self.assertTrue(self.disk_path.exists())
        self.assertEqual(self.disk_path.stat().st_size, 256 * 1024 * 1024)

        validator = DiskValidator(self.disk_path)
        report = validator.validate_all()

        partitions = report["partitions"]
        self.assertEqual(len(partitions), 2)
        self.assertEqual(partitions[0]["name"], "MVX_BOOT")
        self.assertEqual(partitions[1]["name"], "MVX_DATA")

    def test_partition_type_guids(self):
        """Test that Partition 1 has ESP GUID and Partition 2 has Basic Data GUID."""
        validator = DiskValidator(self.disk_path)
        report = validator.validate_all()
        partitions = report["partitions"]

        self.assertEqual(partitions[0]["type_guid"], str(ESP_TYPE_GUID))
        self.assertEqual(partitions[1]["type_guid"], str(BASIC_DATA_GUID))

    def test_partition_boundaries_and_alignment(self):
        """Test that partitions are aligned to 1MiB (2048 sectors) and do not overlap."""
        validator = DiskValidator(self.disk_path)
        report = validator.validate_all()
        p1 = report["partitions"][0]
        p2 = report["partitions"][1]

        # 1MiB alignment check
        self.assertEqual(p1["start_lba"], 2048)
        self.assertEqual(p1["start_lba"] % 2048, 0)
        self.assertEqual(p2["start_lba"] % 2048, 0)

        # Non-overlapping boundary check
        self.assertLess(p1["start_lba"], p1["end_lba"])
        self.assertLess(p1["end_lba"], p2["start_lba"])
        self.assertLess(p2["start_lba"], p2["end_lba"])

        # Capacity checks
        self.assertEqual(p1["size_mb"], 64.0)
        self.assertGreater(p2["size_mb"], 180.0)

    def test_fat32_efi_partition(self):
        """Test that Partition 1 is a valid FAT32 filesystem labeled MVX_BOOT."""
        validator = DiskValidator(self.disk_path)
        report = validator.validate_all()

        boot_info = report["mvx_boot"]
        self.assertEqual(boot_info["label"], "MVX_BOOT")
        self.assertEqual(boot_info["filesystem"], "FAT32")
        self.assertEqual(boot_info["bytes_per_sector"], 512)
        self.assertEqual(boot_info["root_cluster"], 2)

    def test_exfat_data_partition(self):
        """Test that Partition 2 is a valid exFAT filesystem labeled MVX_DATA."""
        validator = DiskValidator(self.disk_path)
        report = validator.validate_all()

        data_info = report["mvx_data"]
        self.assertEqual(data_info["label"], "MVX_DATA")
        self.assertEqual(data_info["filesystem"], "exFAT")
        self.assertIn("ISO", data_info["subdirectories"])
        self.assertIn("config", data_info["subdirectories"])
        self.assertIn("metadata", data_info["subdirectories"])
        self.assertIn("backups", data_info["subdirectories"])

    def test_expected_directory_structure_and_config_placement(self):
        """Test StorageManager directory structure verification and authoritative config."""
        simulated_data = self.temp_dir / "simulated_data"
        simulated_boot = self.temp_dir / "simulated_boot"

        sm = StorageManager(data_mount=simulated_data, boot_mount=simulated_boot)
        sm.initialize_data_partition_structure()

        # Check required directories on data partition
        self.assertTrue(sm.iso_directory_path.is_dir())
        self.assertTrue(sm.config_directory_path.is_dir())
        self.assertTrue(sm.metadata_directory_path.is_dir())
        self.assertTrue(sm.backups_directory_path.is_dir())
        self.assertTrue(sm.config_file_path.is_file())

        is_valid, errors = sm.validate_data_partition_structure()
        self.assertTrue(is_valid, f"Data partition structure must be valid: {errors}")

        # Check authoritative config content
        cfg = sm.load_configuration()
        self.assertIn("version", cfg)
        self.assertIn("environments", cfg)

    def test_storage_manager_atomic_write_and_backup(self):
        """Test atomic writing to config/mventx.json and automated backup creation in backups/."""
        simulated_data = self.temp_dir / "sim_data2"
        sm = StorageManager(data_mount=simulated_data)
        sm.initialize_data_partition_structure()

        initial_cfg = sm.load_configuration()
        initial_cfg["meta"]["title"] = "Updated Title"

        # Write updated config with automatic backup
        sm.write_configuration_atomically(initial_cfg, create_backup=True)

        reloaded = sm.load_configuration()
        self.assertEqual(reloaded["meta"]["title"], "Updated Title")

        # Verify a backup was created in backups/
        backups = list(sm.backups_directory_path.glob("mventx_*.json"))
        self.assertGreaterEqual(len(backups), 1)

    def test_storage_manager_menu_sync(self):
        """Test syncing authoritative config from MVX_DATA to generated menu on MVX_BOOT."""
        simulated_data = self.temp_dir / "sim_data3"
        simulated_boot = self.temp_dir / "sim_boot3"

        sm = StorageManager(data_mount=simulated_data, boot_mount=simulated_boot)
        custom_config = {
            "version": "1.0.0",
            "meta": {"title": "Synced Menu Title", "timeout": 7},
            "environments": [
                {
                    "id": "env-1",
                    "display_name": "Synced Linux Workstation",
                    "type": "iso",
                    "iso_path": "ISO/workstation.iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }
        sm.initialize_data_partition_structure(default_config=custom_config)

        # Sync to boot partition
        menu_path = sm.sync_boot_menu()
        self.assertIsNotNone(menu_path)
        self.assertTrue(menu_path.exists())

        menu_text = menu_path.read_text(encoding="utf-8")
        self.assertIn('set mventx_menu_title="Synced Menu Title"', menu_text)
        self.assertIn('menuentry "Synced Linux Workstation" --id "env-1"', menu_text)

    def test_invalid_partition_layouts(self):
        """Test that DiskValidator catches corrupt or invalid disk images."""
        corrupt_disk = self.temp_dir / "corrupt.img"

        # Corrupt MBR
        shutil.copyfile(self.disk_path, corrupt_disk)
        with open(corrupt_disk, "r+b") as f:
            f.seek(510)
            f.write(b"\x00\x00")  # Invalidate boot sig
        with self.assertRaises(DiskValidationError):
            DiskValidator(corrupt_disk).validate_all()

        # Invalidate GPT signature
        shutil.copyfile(self.disk_path, corrupt_disk)
        with open(corrupt_disk, "r+b") as f:
            f.seek(512)
            f.write(b"BAD_PART")
        with self.assertRaises(DiskValidationError):
            DiskValidator(corrupt_disk).validate_all()

    def test_device_detector_safety(self):
        """Test DeviceDetector safety checks on system partitions."""
        # C: drive should never be validated as an MVentX volume
        is_valid, errors = DeviceDetector.validate_mventx_volume("C:\\", expected_label="MVX_DATA")
        self.assertFalse(is_valid)

        # Non-existent drive should fail cleanly
        is_valid2, errors2 = DeviceDetector.validate_mventx_volume("Z:\\NonExistentPath")
        self.assertFalse(is_valid2)


if __name__ == "__main__":
    unittest.main()

