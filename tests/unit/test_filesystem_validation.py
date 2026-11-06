"""Unit tests for FAT32 and exFAT filesystem structural validation and direct cluster reading."""

import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from core.storage.disk_validator import DiskValidationError, DiskValidator
from scripts.windows.make_dual_partition_disk import build_dual_partition_gpt_disk


class TestFilesystemValidation(unittest.TestCase):
    """Test suite for deep FAT32 MVX_BOOT and exFAT MVX_DATA filesystem validation."""

    @classmethod
    def setUpClass(cls):
        cls.shared_temp = Path(tempfile.mkdtemp(prefix="mventx_fs_val_"))
        cls.esp_src = cls.shared_temp / "esp"
        cls.esp_src.mkdir()
        (cls.esp_src / "EFI" / "BOOT").mkdir(parents=True)
        (cls.esp_src / "boot" / "grub").mkdir(parents=True)
        (cls.esp_src / "EFI" / "BOOT" / "BOOTX64.EFI").write_bytes(b"MZ_DUMMY_GRUB_BIN")
        (cls.esp_src / "boot" / "grub" / "grub.cfg").write_text("# MVentX Test Config\nset timeout=10\n", encoding="utf-8")
        (cls.esp_src / "boot" / "grub" / "mventx_menu.cfg").write_text("menuentry 'Test' {}\n", encoding="utf-8")

        cls.base_disk = cls.shared_temp / "base_disk.img"
        build_dual_partition_gpt_disk(
            esp_source_dir=cls.esp_src,
            output_path=cls.base_disk,
            disk_size_mb=256,
            esp_size_mb=64,
        )

    @classmethod
    def tearDownClass(cls):
        if cls.shared_temp.exists():
            shutil.rmtree(cls.shared_temp)

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_fs_case_"))
        self.disk_copy = self.temp_dir / "fs_test.img"
        shutil.copyfile(self.base_disk, self.disk_copy)
        self.validator = DiskValidator(self.disk_copy)

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def _mutate_disk(self, offset: int, data: bytes):
        """Helper to mutate raw bytes on disk."""
        with open(self.disk_copy, "r+b") as f:
            f.seek(offset)
            f.write(data)

    # --------------------------------------------------------------------------
    # FAT32 MVX_BOOT Tests
    # --------------------------------------------------------------------------

    def test_fat32_valid_passes(self):
        """Test that unaltered FAT32 partition passes validation."""
        report = self.validator.validate_all()
        fat32 = report["mvx_boot"]
        self.assertEqual(fat32["label"], "MVX_BOOT")
        self.assertEqual(fat32["filesystem"], "FAT32")
        self.assertGreaterEqual(fat32["total_clusters"], 65525)

    def test_fat32_vbr_signature_invalid(self):
        """Test that FAT32 VBR without 0x55AA signature fails."""
        # Partition 1 starts at LBA 2048 (offset 2048 * 512)
        p1_offset = 2048 * 512
        self._mutate_disk(p1_offset + 510, b"\x00\x00")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("boot signature is not 0x55AA", str(ctx.exception))

    def test_fat32_fsinfo_signature_invalid(self):
        """Test that corrupted FSInfo signature fails validation."""
        p1_offset = 2048 * 512
        # FSInfo is at sector 1
        self._mutate_disk(p1_offset + 512, b"BAD_")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("FSInfo sector signature mismatch", str(ctx.exception))

    def test_fat32_backup_vbr_mismatch(self):
        """Test that backup VBR at sector 6 differing from primary VBR fails."""
        p1_offset = 2048 * 512
        # Sector 6 backup VBR
        self._mutate_disk(p1_offset + 6 * 512 + 10, b"\xFF\xFF")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("backup VBR at sector 6 does not match", str(ctx.exception))

    def test_fat32_label_mismatch(self):
        """Test that incorrect volume label in FAT32 VBR fails."""
        p1_offset = 2048 * 512
        # Offset 71 in FAT32 VBR is 11-byte volume label
        self._mutate_disk(p1_offset + 71, b"WRONG_LABEL")
        # Also mutate backup VBR so backup check passes
        self._mutate_disk(p1_offset + 6 * 512 + 71, b"WRONG_LABEL")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("expected 'MVX_BOOT'", str(ctx.exception))

    def test_direct_fat32_read_success(self):
        """Test reading grub.cfg directly out of raw FAT32 clusters."""
        data = self.validator.read_file_from_fat32("boot/grub/grub.cfg")
        text = data.decode("utf-8")
        self.assertIn("# MVentX Test Config", text)
        self.assertIn("set timeout=10", text)

    def test_direct_fat32_read_nested_binary(self):
        """Test reading BOOTX64.EFI directly out of raw FAT32 clusters."""
        data = self.validator.read_file_from_fat32("EFI/BOOT/BOOTX64.EFI")
        self.assertEqual(data, b"MZ_DUMMY_GRUB_BIN")

    def test_direct_fat32_read_not_found(self):
        """Test reading non-existent file on FAT32 raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            self.validator.read_file_from_fat32("non_existent_dir/file.txt")

    def test_direct_fat32_read_is_directory(self):
        """Test reading a directory as a file raises IsADirectoryError."""
        with self.assertRaises(IsADirectoryError):
            self.validator.read_file_from_fat32("boot/grub")

    # --------------------------------------------------------------------------
    # exFAT MVX_DATA Tests
    # --------------------------------------------------------------------------

    def test_exfat_valid_passes(self):
        """Test that unaltered exFAT partition passes validation."""
        report = self.validator.validate_all()
        exfat = report["mvx_data"]
        self.assertEqual(exfat["label"], "MVX_DATA")
        self.assertEqual(exfat["filesystem"], "exFAT")
        self.assertIn("ISO", exfat["subdirectories"])
        self.assertIn("config", exfat["subdirectories"])
        self.assertIn("metadata", exfat["subdirectories"])
        self.assertIn("backups", exfat["subdirectories"])

    def test_exfat_oem_signature_mismatch(self):
        """Test that invalid exFAT OEM name fails validation."""
        p2_offset = 133120 * 512
        # Offset 3 in exFAT VBR is OEM Name ("EXFAT   ")
        self._mutate_disk(p2_offset + 3, b"NOTEXFAT")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("must be 'EXFAT   '", str(ctx.exception))

    def test_exfat_boot_checksum_mismatch(self):
        """Test that modifying boot sector without updating sector 11 checksum fails."""
        p2_offset = 133120 * 512
        # Modify byte in VBR (e.g. byte 20)
        self._mutate_disk(p2_offset + 20, b"\x77")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("exFAT boot checksum mismatch", str(ctx.exception))

    def test_exfat_backup_boot_region_mismatch(self):
        """Test that backup boot region (sectors 12-23) differing from main fails."""
        p2_offset = 133120 * 512
        # Mutate sector 12 (backup VBR)
        self._mutate_disk(p2_offset + 12 * 512 + 5, b"\x99")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("backup boot region does not match", str(ctx.exception))

    def test_direct_exfat_read_config_json(self):
        """Test reading config/mventx.json directly out of raw exFAT clusters."""
        data = self.validator.read_file_from_exfat("config/mventx.json")
        cfg = json.loads(data.decode("utf-8"))
        self.assertEqual(cfg["version"], "1.0.0")
        self.assertEqual(cfg["meta"]["title"], "MVentX Multiboot System")
        self.assertIsInstance(cfg["environments"], list)

    def test_direct_exfat_read_not_found(self):
        """Test reading non-existent file on exFAT raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            self.validator.read_file_from_exfat("config/missing_file.json")

    def test_direct_exfat_read_is_directory(self):
        """Test reading a directory path on exFAT raises IsADirectoryError."""
        with self.assertRaises(IsADirectoryError):
            self.validator.read_file_from_exfat("ISO")


if __name__ == "__main__":
    unittest.main()
