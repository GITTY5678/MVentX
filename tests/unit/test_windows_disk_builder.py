"""Unit tests for Native Windows FAT / GPT virtual disk builder."""

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.windows.make_fat_disk import (
    SimpleFat16Builder,
    build_gpt_disk_image,
    generate_83_name,
    vfat_checksum,
)


class TestWindowsDiskBuilder(unittest.TestCase):
    """Test suite for native Windows virtual disk generation logic."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_win_test_"))
        self.esp_src = self.temp_dir / "esp"
        self.esp_src.mkdir()

        # Prepare simulated ESP directory tree
        (self.esp_src / "EFI" / "BOOT").mkdir(parents=True)
        (self.esp_src / "boot" / "grub").mkdir(parents=True)

        (self.esp_src / "EFI" / "BOOT" / "BOOTX64.EFI").write_bytes(b"MZ_DUMMY_EFI_TEST_BINARY")
        (self.esp_src / "boot" / "grub" / "grub.cfg").write_text("set timeout=10\nset default=0", encoding="utf-8")
        (self.esp_src / "boot" / "grub" / "mventx_menu.cfg").write_text(
            'menuentry "Test Environment" --id "test-env" { echo "OK"; sleep 2; }\n',
            encoding="utf-8",
        )

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_83_name_generation(self):
        """Test generation of valid 8.3 short names and collision handling."""
        seen = set()
        name1 = generate_83_name("mventx_menu.cfg", seen)
        self.assertEqual(len(name1), 11)
        self.assertTrue(name1.startswith(b"MVENTX_M"))
        self.assertTrue(name1.endswith(b"CFG"))

        # Collision handling
        name2 = generate_83_name("mventx_menu.cfg", seen)
        self.assertNotEqual(name1, name2)
        self.assertTrue(name2.startswith(b"MVENTX~1"))

    def test_vfat_checksum(self):
        """Test standard VFAT short name checksum computation."""
        short_name = b"MVENTX_MCFG"
        chk = vfat_checksum(short_name)
        self.assertIsInstance(chk, int)
        self.assertGreaterEqual(chk, 0)
        self.assertLessEqual(chk, 255)

    def test_fat16_builder_structure(self):
        """Test in-memory FAT16 filesystem generation and boot sector."""
        size_bytes = 32 * 1024 * 1024  # 32MB
        builder = SimpleFat16Builder(size_bytes=size_bytes, volume_label="MVX_BOOT")
        fs_image = builder.build_from_directory(self.esp_src)

        self.assertEqual(len(fs_image), size_bytes)
        # Check VBR jump instruction and boot signature
        self.assertEqual(fs_image[0:3], b"\xeb\x3c\x90")
        self.assertEqual(fs_image[510:512], b"\x55\xaa")
        # Check volume label in BPB
        self.assertEqual(fs_image[43:54], b"MVX_BOOT   ")
        self.assertEqual(fs_image[54:62], b"FAT16   ")

    def test_build_gpt_disk_image(self):
        """Test complete GPT disk image construction with PMBR and ESP."""
        output_img = self.temp_dir / "mventx_test.img"
        disk_size_mb = 64
        build_gpt_disk_image(
            source_dir=self.esp_src,
            output_path=output_img,
            disk_size_mb=disk_size_mb,
            volume_label="MVX_BOOT",
        )

        self.assertTrue(output_img.exists())
        self.assertEqual(output_img.stat().st_size, disk_size_mb * 1024 * 1024)

        # Inspect binary structure
        with open(output_img, "rb") as f:
            # Sector 0: Protective MBR
            f.seek(446)
            pmbr_part1 = f.read(16)
            self.assertEqual(pmbr_part1[4], 0xEE, "Partition 1 type must be 0xEE (GPT Protective)")
            f.seek(510)
            self.assertEqual(f.read(2), b"\x55\xaa", "MBR signature must be 0x55AA")

            # Sector 1: Primary GPT Header
            f.seek(512)
            gpt_sig = f.read(8)
            self.assertEqual(gpt_sig, b"EFI PART", "GPT signature must be 'EFI PART'")

            # Sector 2048: Partition 1 (FAT VBR)
            f.seek(2048 * 512)
            vbr_sig = f.read(3)
            self.assertEqual(vbr_sig, b"\xeb\x3c\x90", "VBR must begin with JMP instruction")
            f.seek(2048 * 512 + 510)
            self.assertEqual(f.read(2), b"\x55\xaa", "VBR signature must be 0x55AA")


if __name__ == "__main__":
    unittest.main()

