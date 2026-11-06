"""Unit tests for ISO file validation and metadata extraction."""

import hashlib
from pathlib import Path
import tempfile
import unittest

from core.iso_manager.metadata import ISOMetadata, ISOMetadataExtractor
from core.iso_manager.validation import (
    ISO_SECTOR_SIZE,
    VRA_START_OFFSET,
    ISOValidationResult,
    ISOValidator,
)


def make_mock_iso(
    output_path: Path,
    volume_id: str = "TEST_UBUNTU_X86_64",
    system_id: str = "LINUX",
    publisher_id: str = "CANONICAL",
    format_type: str = "iso9660",  # "iso9660", "udf", "hybrid"
    has_el_torito: bool = True,
) -> None:
    """Create a minimal syntactically valid ISO image for testing."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        # 1. First 16 sectors (32 KiB) System Area
        f.write(b"\x00" * VRA_START_OFFSET)

        if format_type in ("iso9660", "hybrid"):
            # Sector 16: Primary Volume Descriptor (PVD)
            pvd = bytearray(ISO_SECTOR_SIZE)
            pvd[0] = 0x01  # Primary Volume Descriptor
            pvd[1:6] = b"CD001"
            pvd[6] = 0x01  # Version
            pvd[8:40] = system_id.encode("latin-1")[:32].ljust(32)
            pvd[40:72] = volume_id.encode("latin-1")[:32].ljust(32)
            pvd[318:446] = publisher_id.encode("latin-1")[:128].ljust(128)
            f.write(pvd)

            # Optional El Torito Boot Record
            if has_el_torito:
                boot_rec = bytearray(ISO_SECTOR_SIZE)
                boot_rec[0] = 0x00  # Boot Record
                boot_rec[1:6] = b"CD001"
                boot_rec[6] = 0x01
                boot_rec[7:39] = b"EL TORITO SPECIFICATION".ljust(32)
                f.write(boot_rec)

        if format_type in ("udf", "hybrid"):
            # UDF Beginning Extended Area (BEA01)
            bea = bytearray(ISO_SECTOR_SIZE)
            bea[0] = 0x00
            bea[1:6] = b"BEA01"
            bea[6] = 0x01
            f.write(bea)

            # UDF NSR02 descriptor
            nsr = bytearray(ISO_SECTOR_SIZE)
            nsr[0] = 0x00
            nsr[1:6] = b"NSR02"
            nsr[6] = 0x01
            f.write(nsr)

            # UDF Terminating Extended Area (TEA01)
            tea = bytearray(ISO_SECTOR_SIZE)
            tea[0] = 0x00
            tea[1:6] = b"TEA01"
            tea[6] = 0x01
            f.write(tea)

        if format_type in ("iso9660", "hybrid"):
            # Volume Descriptor Set Terminator
            term = bytearray(ISO_SECTOR_SIZE)
            term[0] = 0xFF
            term[1:6] = b"CD001"
            term[6] = 0x01
            f.write(term)


class TestISOValidator(unittest.TestCase):
    """Test suite for ISOValidator and ISOValidationResult."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_non_existent_file(self):
        result = ISOValidator.validate_iso(self.dir_path / "missing.iso")
        self.assertFalse(result.is_valid)
        self.assertTrue(any("does not exist" in err for err in result.errors))

    def test_non_iso_extension(self):
        txt_file = self.dir_path / "test.txt"
        txt_file.write_text("not an iso")
        result = ISOValidator.validate_iso(txt_file)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("extension must be '.iso'" in err for err in result.errors))

    def test_empty_iso_file(self):
        empty_iso = self.dir_path / "empty.iso"
        empty_iso.touch()
        result = ISOValidator.validate_iso(empty_iso)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("empty" in err for err in result.errors))

    def test_truncated_iso_file(self):
        truncated = self.dir_path / "truncated.iso"
        truncated.write_bytes(b"\x00" * 1024)
        result = ISOValidator.validate_iso(truncated)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("too small" in err for err in result.errors))

    def test_corrupt_signature_iso(self):
        corrupt = self.dir_path / "corrupt.iso"
        # 34 KiB of random bytes without CD001 or UDF descriptors
        corrupt.write_bytes(b"\xaa" * (VRA_START_OFFSET + ISO_SECTOR_SIZE * 2))
        result = ISOValidator.validate_iso(corrupt)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("No valid ISO9660 or UDF" in err for err in result.errors))

    def test_valid_iso9660(self):
        valid_iso = self.dir_path / "ubuntu-24.04-desktop-amd64.iso"
        make_mock_iso(
            valid_iso,
            volume_id="UBUNTU_24_04_X86_64",
            system_id="LINUX",
            publisher_id="CANONICAL",
            format_type="iso9660",
            has_el_torito=True,
        )

        result = ISOValidator.validate_iso(valid_iso)
        self.assertTrue(result.is_valid)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.filesystem_format, "ISO9660")
        self.assertEqual(result.volume_identifier, "UBUNTU_24_04_X86_64")
        self.assertEqual(result.system_identifier, "LINUX")
        self.assertTrue(result.has_el_torito_boot)
        self.assertIn("confirm image structure only", result.disclaimer)

    def test_valid_udf(self):
        udf_iso = self.dir_path / "test_udf.iso"
        make_mock_iso(udf_iso, format_type="udf")

        result = ISOValidator.validate_iso(udf_iso)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.filesystem_format, "UDF")

    def test_valid_hybrid_iso(self):
        hybrid_iso = self.dir_path / "hybrid.iso"
        make_mock_iso(hybrid_iso, format_type="hybrid")

        result = ISOValidator.validate_iso(hybrid_iso)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.filesystem_format, "ISO9660/UDF (Hybrid)")

    def test_calculate_and_verify_checksum(self):
        iso_file = self.dir_path / "hashed.iso"
        make_mock_iso(iso_file)

        sha256 = ISOValidator.calculate_checksum(iso_file, "sha256")
        self.assertEqual(len(sha256), 64)
        self.assertTrue(ISOValidator.verify_checksum(iso_file, sha256, "sha256"))
        self.assertFalse(ISOValidator.verify_checksum(iso_file, "0" * 64, "sha256"))

        md5 = ISOValidator.calculate_checksum(iso_file, "md5")
        self.assertEqual(len(md5), 32)
        self.assertTrue(ISOValidator.verify_checksum(iso_file, md5, "md5"))


class TestISOMetadataExtractor(unittest.TestCase):
    """Test suite for ISOMetadataExtractor."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_extract_metadata_and_architecture_detection(self):
        iso_path = self.dir_path / "fedora-40-x86_64.iso"
        make_mock_iso(
            iso_path,
            volume_id="FEDORA_40_X86_64",
            system_id="LINUX",
            publisher_id="RED_HAT",
            format_type="iso9660",
        )

        meta = ISOMetadataExtractor.extract_metadata(iso_path, calculate_hash=True)
        self.assertEqual(meta.file_name, "fedora-40-x86_64.iso")
        self.assertEqual(meta.volume_identifier, "FEDORA_40_X86_64")
        self.assertEqual(meta.system_identifier, "LINUX")
        self.assertEqual(meta.publisher_identifier, "RED_HAT")
        self.assertEqual(meta.detected_architecture, "x86_64")
        self.assertEqual(meta.suggested_boot_method, "linux_generic_loopback")
        self.assertTrue(meta.has_el_torito_boot)
        self.assertIsNotNone(meta.registered_at)
        self.assertIsNotNone(meta.checksum)
        self.assertEqual(meta.checksum.get("algorithm"), "sha256")

    def test_save_load_delete_metadata(self):
        iso_path = self.dir_path / "debian-12-arm64.iso"
        make_mock_iso(iso_path, volume_id="DEBIAN_12_ARM64")

        meta = ISOMetadataExtractor.extract_metadata(iso_path)
        meta_dir = self.dir_path / "metadata"

        # Save
        saved_file = ISOMetadataExtractor.save_metadata(meta_dir, "debian_12", meta)
        self.assertTrue(saved_file.is_file())

        # Load
        loaded = ISOMetadataExtractor.load_metadata(meta_dir, "debian_12")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.file_name, "debian-12-arm64.iso")
        self.assertEqual(loaded.detected_architecture, "arm64")

        # Delete
        deleted = ISOMetadataExtractor.delete_metadata(meta_dir, "debian_12")
        self.assertTrue(deleted)
        self.assertIsNone(ISOMetadataExtractor.load_metadata(meta_dir, "debian_12"))


if __name__ == "__main__":
    unittest.main()

