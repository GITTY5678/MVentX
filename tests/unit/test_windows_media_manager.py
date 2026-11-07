"""Unit tests for Windows Media Manager (Milestone 11).

Tests storage capacity calculations, cluster alignment overhead, safety margin,
payload integrity verification, cancellation handling, and non-destructive deregistration.
"""

from pathlib import Path
import shutil
import tempfile
import unittest

from core.iso_manager.windows_iso_inspector import (
    WindowsArchitecture,
    WindowsInspectionResult,
    WindowsPayloadType,
)
from core.iso_manager.windows_media_manager import (
    ExtractionIntegrityError,
    ExtractionInterruptedError,
    InsufficientStorageError,
    StorageRequirement,
    WindowsMediaManager,
    WindowsStorageError,
)


class TestWindowsMediaManager(unittest.TestCase):
    """Test suite for WindowsMediaManager."""

    def test_calculate_storage_requirement(self):
        """Verify raw size + 5% cluster overhead + safety margin math."""
        raw_size = 5 * 1024 * 1024 * 1024  # 5 GiB
        insp = WindowsInspectionResult(
            is_windows_media=True,
            total_iso_size_bytes=raw_size,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            safety_margin = 500 * 1024 * 1024  # 500 MiB
            req = WindowsMediaManager.calculate_storage_requirement(
                insp,
                destination_mount=tmp_dir,
                safety_margin_bytes=safety_margin,
            )

            expected_overhead = int(raw_size * 0.05)
            expected_total = raw_size + expected_overhead + safety_margin

            self.assertEqual(req.raw_payload_bytes, raw_size)
            self.assertEqual(req.cluster_overhead_bytes, expected_overhead)
            self.assertEqual(req.safety_margin_bytes, safety_margin)
            self.assertEqual(req.total_required_bytes, expected_total)
            self.assertTrue(req.total_required_gb > 5.7)

    def test_insufficient_storage_flag(self):
        """Verify storage sufficiency check when destination lacks space."""
        # Unreasonably huge size 500 TB to guarantee insufficient space
        huge_size = 500 * 1024 * 1024 * 1024 * 1024
        insp = WindowsInspectionResult(
            is_windows_media=True,
            total_iso_size_bytes=huge_size,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            req = WindowsMediaManager.calculate_storage_requirement(insp, destination_mount=tmp_dir)
            self.assertFalse(req.is_sufficient)

    def test_verify_extracted_media_integrity_success(self):
        """Verify success when critical boot and payload files exist and are non-empty."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "efi" / "boot").mkdir(parents=True)
            (root / "sources").mkdir(parents=True)

            (root / "efi" / "boot" / "bootx64.efi").write_bytes(b"EFI_BOOTLOADER_CODE")
            (root / "sources" / "boot.wim").write_bytes(b"WINPE_WIM_CODE")
            (root / "sources" / "install.wim").write_bytes(b"INSTALL_WIM_CODE")

            insp = WindowsInspectionResult(
                is_windows_media=True,
                has_uefi_boot=True,
                has_winpe=True,
                has_install_payload=True,
            )

            result = WindowsMediaManager.verify_extracted_media_integrity(root, insp)
            self.assertTrue(result)

    def test_verify_extracted_media_missing_bootloader(self):
        """Verify integrity failure when EFI bootloader is missing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "sources").mkdir(parents=True)
            (root / "sources" / "boot.wim").write_bytes(b"WINPE_WIM_CODE")
            (root / "sources" / "install.wim").write_bytes(b"INSTALL_WIM_CODE")

            insp = WindowsInspectionResult(is_windows_media=True, has_uefi_boot=True)
            with self.assertRaises(ExtractionIntegrityError) as ctx:
                WindowsMediaManager.verify_extracted_media_integrity(root, insp)
            self.assertIn("EFI bootloader", str(ctx.exception))

    def test_verify_extracted_media_missing_winpe(self):
        """Verify integrity failure when sources/boot.wim is missing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "efi" / "boot").mkdir(parents=True)
            (root / "sources").mkdir(parents=True)
            (root / "efi" / "boot" / "bootx64.efi").write_bytes(b"EFI_BOOTLOADER_CODE")
            (root / "sources" / "install.wim").write_bytes(b"INSTALL_WIM_CODE")

            insp = WindowsInspectionResult(is_windows_media=True, has_winpe=True)
            with self.assertRaises(ExtractionIntegrityError) as ctx:
                WindowsMediaManager.verify_extracted_media_integrity(root, insp)
            self.assertIn("boot.wim", str(ctx.exception))

    def test_verify_extracted_media_missing_payload(self):
        """Verify integrity failure when install.wim/esd is missing or 0 bytes."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "efi" / "boot").mkdir(parents=True)
            (root / "sources").mkdir(parents=True)
            (root / "efi" / "boot" / "bootx64.efi").write_bytes(b"EFI_BOOTLOADER_CODE")
            (root / "sources" / "boot.wim").write_bytes(b"WINPE_WIM_CODE")
            (root / "sources" / "install.wim").write_bytes(b"")  # 0 bytes

            insp = WindowsInspectionResult(is_windows_media=True, has_install_payload=True)
            with self.assertRaises(ExtractionIntegrityError) as ctx:
                WindowsMediaManager.verify_extracted_media_integrity(root, insp)
            self.assertIn("install.wim", str(ctx.exception))

    def test_cleanup_staged_media(self):
        """Verify explicit deletion helper only removes directory when invoked."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "windows_staged"
            target.mkdir()
            (target / "dummy.txt").write_text("data")

            self.assertTrue(target.exists())
            deleted = WindowsMediaManager.cleanup_staged_media(target)
            self.assertTrue(deleted)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()

