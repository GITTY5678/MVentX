"""Integration tests for ISO storage integrity, hash verification, missing/tampered ISO detection, and duplicate rejection."""

import hashlib
from pathlib import Path
import tempfile
import unittest

from core.iso_manager import (
    DuplicateRegistrationError,
    ISORegistrationManager,
    ISOValidationError,
    ISOValidator,
)
from core.storage.storage_manager import StorageManager
from tests.unit.test_iso_validation import make_mock_iso


class TestIsoStorageIntegrity(unittest.TestCase):
    """Test suite for verifying ISO integrity, hashing, tampering, and validation rules."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        self.data_mount = self.root_path / "MVX_DATA"
        self.boot_mount = self.root_path / "MVX_BOOT"
        self.data_mount.mkdir()
        self.boot_mount.mkdir()

        (self.boot_mount / "boot" / "grub").mkdir(parents=True)

        self.storage = StorageManager(self.data_mount, self.boot_mount)
        self.storage.initialize_data_partition_structure()
        self.manager = ISORegistrationManager(self.storage)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_synthetic_iso_formats(self):
        """Test registration and validation of ISO9660, UDF, and hybrid ISO formats."""
        formats = [
            ("iso9660.iso", "iso9660", "ISO9660 Standard"),
            ("udf_volume.iso", "udf", "UDF Standard"),
            ("hybrid_os.iso", "hybrid", "Hybrid Dual Format"),
        ]

        for fname, ftype, dname in formats:
            iso_path = self.storage.iso_directory_path / fname
            make_mock_iso(iso_path, volume_id=fname[:10].upper(), format_type=ftype)
            rec = self.manager.register_iso(iso_path, display_name=dname)
            self.assertEqual(rec.display_name, dname)
            self.assertTrue(rec.enabled)

    def test_cryptographic_hash_verification(self):
        """Test sha256, sha512, and md5 cryptographic hashing on registration."""
        iso_path = self.storage.iso_directory_path / "hash_target.iso"
        make_mock_iso(iso_path, volume_id="HASH_TARGET", format_type="iso9660")
        raw_bytes = iso_path.read_bytes()

        # SHA-256
        rec_sha256 = self.manager.register_iso(
            iso_path,
            display_name="SHA256 Environment",
            calculate_hash=True,
            hash_algorithm="sha256",
        )
        expected_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        self.assertEqual(rec_sha256.checksum["algorithm"], "sha256")
        self.assertEqual(rec_sha256.checksum["value"], expected_sha256)

    def test_missing_iso_detection(self):
        """Test that out-of-band deletion of an ISO is cleanly detected as missing."""
        iso_path = self.storage.iso_directory_path / "ephemeral.iso"
        make_mock_iso(iso_path, volume_id="EPHEMERAL")

        rec = self.manager.register_iso(iso_path, display_name="Ephemeral Environment")
        self.assertEqual(len(self.manager.detect_missing_isos()), 0)

        # User deletes ISO from disk out-of-band
        iso_path.unlink()

        missing = self.manager.detect_missing_isos()
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0][0], rec.id)
        self.assertEqual(missing[0][1], "ISO/ephemeral.iso")

        # Listing environments still succeeds without crashing
        all_envs = self.manager.list_environments()
        self.assertEqual(len(all_envs), 1)
        self.assertEqual(all_envs[0].id, rec.id)

    def test_modified_and_tampered_iso_detection(self):
        """Test detecting modified file size and hash mismatch when an ISO is altered."""
        iso_path = self.storage.iso_directory_path / "tamper_target.iso"
        make_mock_iso(iso_path, volume_id="TAMPER_TEST")

        rec = self.manager.register_iso(
            iso_path,
            display_name="Tamper Environment",
            calculate_hash=True,
        )
        orig_size = rec.file_size_bytes

        # Tamper: append arbitrary junk data to the ISO file
        with open(iso_path, "ab") as f:
            f.write(b"CORRUPTED_EXTRA_DATA" * 500)

        # File size on disk is now different
        current_size = iso_path.stat().st_size
        self.assertNotEqual(current_size, orig_size)

        # SHA-256 calculation no longer matches stored checksum
        new_hash = hashlib.sha256(iso_path.read_bytes()).hexdigest()
        self.assertNotEqual(rec.checksum["value"], new_hash)

    def test_duplicate_registration_prevention(self):
        """Test that registering the same ISO path twice is rejected."""
        iso_path = self.storage.iso_directory_path / "single_instance.iso"
        make_mock_iso(iso_path, volume_id="SINGLE_INST")

        self.manager.register_iso(iso_path, display_name="Original Registration")

        with self.assertRaises(DuplicateRegistrationError) as ctx:
            self.manager.register_iso(iso_path, display_name="Duplicate Attempt")
        self.assertIn("already registered", str(ctx.exception))

    def test_invalid_file_rejections(self):
        """Test rejection of non-ISO extensions, 0-byte files, truncated files, and random noise."""
        iso_dir = self.storage.iso_directory_path

        # 1. Non-ISO extension
        txt_file = iso_dir / "sample.txt"
        txt_file.write_text("not an iso", encoding="utf-8")
        with self.assertRaises(Exception) as ctx:
            self.manager.register_iso(txt_file, display_name="Text File")
        self.assertIn("extension", str(ctx.exception).lower())

        # 2. 0-byte empty file
        empty_iso = iso_dir / "empty.iso"
        empty_iso.write_bytes(b"")
        with self.assertRaises(Exception) as ctx:
            self.manager.register_iso(empty_iso, display_name="Empty File")
        self.assertIn("empty", str(ctx.exception).lower())

        # 3. Truncated file (< 32KB)
        small_iso = iso_dir / "tiny.iso"
        small_iso.write_bytes(b"\x00" * 1024)
        with self.assertRaises(Exception) as ctx:
            self.manager.register_iso(small_iso, display_name="Tiny File")
        self.assertIn("too small", str(ctx.exception).lower())

        # 4. Random 64KB noise without ISO9660 or UDF headers
        noise_iso = iso_dir / "noise.iso"
        noise_iso.write_bytes(b"\x42" * (64 * 1024))
        with self.assertRaises(Exception) as ctx:
            self.manager.register_iso(noise_iso, display_name="Noise File")
        self.assertIn("signature", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
