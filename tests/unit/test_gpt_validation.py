"""Unit tests for GPT partition table structural validation."""

import shutil
import struct
import tempfile
import unittest
import uuid
from pathlib import Path

from core.storage.disk_validator import (
    BASIC_DATA_GUID,
    ESP_TYPE_GUID,
    DiskValidationError,
    DiskValidator,
)
from scripts.windows.make_dual_partition_disk import build_dual_partition_gpt_disk


class TestGptValidation(unittest.TestCase):
    """Test suite for comprehensive GPT structural validation including negative test cases."""

    @classmethod
    def setUpClass(cls):
        cls.shared_temp = Path(tempfile.mkdtemp(prefix="mventx_gpt_val_"))
        cls.esp_src = cls.shared_temp / "esp"
        cls.esp_src.mkdir()
        (cls.esp_src / "EFI" / "BOOT").mkdir(parents=True)
        (cls.esp_src / "boot" / "grub").mkdir(parents=True)
        (cls.esp_src / "EFI" / "BOOT" / "BOOTX64.EFI").write_bytes(b"MZ_DUMMY_BOOTX64")
        (cls.esp_src / "boot" / "grub" / "grub.cfg").write_text("set timeout=10\n", encoding="utf-8")
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
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_gpt_case_"))
        self.disk_copy = self.temp_dir / "test_copy.img"
        shutil.copyfile(self.base_disk, self.disk_copy)
        self.validator = DiskValidator(self.disk_copy)

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def _mutate_disk(self, offset: int, data: bytes):
        """Helper to write raw bytes at a specific offset in disk_copy."""
        with open(self.disk_copy, "r+b") as f:
            f.seek(offset)
            f.write(data)

    def test_valid_gpt_passes(self):
        """Test that unaltered dual-partition disk passes full GPT validation."""
        report = self.validator.validate_all()
        self.assertTrue(report["pmbr_valid"])
        self.assertTrue(report["gpt_valid"])
        self.assertEqual(len(report["partitions"]), 2)
        self.assertEqual(report["partitions"][0]["name"], "MVX_BOOT")
        self.assertEqual(report["partitions"][1]["name"], "MVX_DATA")

    def test_truncated_image(self):
        """Test that truncated image (< 34 sectors) is rejected."""
        short_file = self.temp_dir / "short.img"
        short_file.write_bytes(b"\x00" * 1024)
        with self.assertRaises(DiskValidationError) as ctx:
            DiskValidator(short_file)
        self.assertIn("too small", str(ctx.exception))

    def test_invalid_pmbr_signature(self):
        """Test that PMBR missing 0x55AA at offset 510 fails validation."""
        self._mutate_disk(510, b"\x00\x00")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("missing standard MBR boot signature", str(ctx.exception))

    def test_invalid_pmbr_partition_type(self):
        """Test that PMBR partition 1 type not 0xEE fails validation."""
        # Offset 446 is partition 1, offset 446 + 4 is sys_ind
        self._mutate_disk(450, b"\x83")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("must be 0xEE", str(ctx.exception))

    def test_invalid_pmbr_start_lba(self):
        """Test that PMBR start LBA not 1 fails validation."""
        # Offset 446 + 8 is start_lba (uint32)
        self._mutate_disk(454, struct.pack("<I", 2))
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("MBR Protective partition start LBA must be 1", str(ctx.exception))

    def test_primary_gpt_signature_invalid(self):
        """Test that corrupted primary GPT header signature fails validation."""
        self._mutate_disk(512, b"CORRUPT!")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("Invalid primary GPT signature", str(ctx.exception))

    def test_primary_gpt_header_crc_mismatch(self):
        """Test that corrupting primary GPT header bytes without recalculating CRC fails."""
        # Offset 512 + 48 is FirstUsableLBA; alter it slightly
        self._mutate_disk(512 + 48, struct.pack("<Q", 40))
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("Primary GPT header CRC32 mismatch", str(ctx.exception))

    def test_primary_gpt_partition_array_crc_mismatch(self):
        """Test that corrupting partition array entry fails primary partition array CRC."""
        # Partition array starts at LBA 2 (offset 1024)
        self._mutate_disk(1024 + 56, b"CORRUPTED_NAME_ENTRY")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("Primary GPT partition array CRC32 mismatch", str(ctx.exception))

    def test_backup_gpt_signature_invalid(self):
        """Test that corrupting backup GPT signature fails validation."""
        total_sectors = self.disk_copy.stat().st_size // 512
        backup_lba = total_sectors - 1
        self._mutate_disk(backup_lba * 512, b"CORRUPT!")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("Backup GPT header signature is invalid", str(ctx.exception))

    def test_backup_gpt_header_crc_mismatch(self):
        """Test that altering backup GPT fields without CRC update fails validation."""
        total_sectors = self.disk_copy.stat().st_size // 512
        backup_lba = total_sectors - 1
        # Corrupt LastUsableLBA at offset 56 in backup header
        self._mutate_disk(backup_lba * 512 + 48, struct.pack("<Q", 100))
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("Backup GPT header CRC32 mismatch", str(ctx.exception))

    def test_backup_gpt_guid_mismatch(self):
        """Test that backup GPT disk GUID differing from primary GPT fails validation."""
        total_sectors = self.disk_copy.stat().st_size // 512
        backup_lba = total_sectors - 1
        # Change GUID and recompute header CRC so header CRC passes but GUID match fails
        with open(self.disk_copy, "r+b") as f:
            f.seek(backup_lba * 512)
            hdr = bytearray(f.read(92))
            hdr[56:72] = uuid.uuid4().bytes_le
            hdr[16:20] = b"\x00\x00\x00\x00"
            import binascii
            crc = binascii.crc32(hdr) & 0xFFFFFFFF
            hdr[16:20] = struct.pack("<I", crc)
            f.seek(backup_lba * 512)
            f.write(hdr)

        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("Backup GPT Disk GUID mismatch", str(ctx.exception))

    def test_backup_partition_array_mismatch(self):
        """Test that tampering with backup partition array fails comparison against primary array."""
        total_sectors = self.disk_copy.stat().st_size // 512
        backup_array_lba = total_sectors - 33
        self._mutate_disk(backup_array_lba * 512 + 10, b"\xFF\xFF\xFF\xFF")
        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("Backup GPT partition array", str(ctx.exception))

    def test_partition_count_not_two(self):
        """Test that GPT with fewer or more than 2 partitions is rejected."""
        # Blank out Partition 2 in both primary and backup arrays
        import binascii
        total_sectors = self.disk_copy.stat().st_size // 512
        backup_array_lba = total_sectors - 33
        backup_lba = total_sectors - 1

        with open(self.disk_copy, "r+b") as f:
            f.seek(1024)
            entries = bytearray(f.read(128 * 128))
            # zero out entry 1 (partition 2)
            entries[128:256] = b"\x00" * 128
            parray_crc = binascii.crc32(entries) & 0xFFFFFFFF
            f.seek(1024)
            f.write(entries)

            # Write same to backup partition array
            f.seek(backup_array_lba * 512)
            f.write(entries)

            # Update primary header
            f.seek(512)
            hdr = bytearray(f.read(92))
            hdr[88:92] = struct.pack("<I", parray_crc)
            hdr[16:20] = b"\x00\x00\x00\x00"
            hcrc = binascii.crc32(hdr) & 0xFFFFFFFF
            hdr[16:20] = struct.pack("<I", hcrc)
            f.seek(512)
            f.write(hdr)

            # Update backup header
            f.seek(backup_lba * 512)
            bhdr = bytearray(f.read(92))
            bhdr[88:92] = struct.pack("<I", parray_crc)
            bhdr[16:20] = b"\x00\x00\x00\x00"
            bhcrc = binascii.crc32(bhdr) & 0xFFFFFFFF
            bhdr[16:20] = struct.pack("<I", bhcrc)
            f.seek(backup_lba * 512)
            f.write(bhdr)

        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("Expected at least 2 partitions", str(ctx.exception))

    def test_partition_type_guid_invalid(self):
        """Test that unexpected partition type GUID fails validation."""
        # Alter partition 1 type GUID to Linux swap in both primary and backup
        import binascii
        total_sectors = self.disk_copy.stat().st_size // 512
        backup_array_lba = total_sectors - 33
        backup_lba = total_sectors - 1

        swap_guid = uuid.UUID("0657fd6d-a4ab-43c4-84e5-0933c84b4f4f").bytes_le
        with open(self.disk_copy, "r+b") as f:
            f.seek(1024)
            entries = bytearray(f.read(128 * 128))
            entries[0:16] = swap_guid
            parray_crc = binascii.crc32(entries) & 0xFFFFFFFF
            f.seek(1024)
            f.write(entries)

            f.seek(backup_array_lba * 512)
            f.write(entries)

            # Update primary header
            f.seek(512)
            hdr = bytearray(f.read(92))
            hdr[88:92] = struct.pack("<I", parray_crc)
            hdr[16:20] = b"\x00\x00\x00\x00"
            hcrc = binascii.crc32(hdr) & 0xFFFFFFFF
            hdr[16:20] = struct.pack("<I", hcrc)
            f.seek(512)
            f.write(hdr)

            # Update backup header
            f.seek(backup_lba * 512)
            bhdr = bytearray(f.read(92))
            bhdr[88:92] = struct.pack("<I", parray_crc)
            bhdr[16:20] = b"\x00\x00\x00\x00"
            bhcrc = binascii.crc32(bhdr) & 0xFFFFFFFF
            bhdr[16:20] = struct.pack("<I", bhcrc)
            f.seek(backup_lba * 512)
            f.write(bhdr)

        with self.assertRaises(DiskValidationError) as ctx:
            self.validator.validate_all()
        self.assertIn("not EFI System Partition", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
