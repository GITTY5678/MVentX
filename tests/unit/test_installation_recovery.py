"""Unit tests for MVentX Installation Recovery Subsystem (Milestone 13)."""

from pathlib import Path
import tempfile
import unittest

from installer.installation_recovery import InstallationRecoveryService, RecoveryReport


class TestInstallationRecovery(unittest.TestCase):
    """Test suite verifying failure cleanup, stage tracking, and recovery actions."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_test_recovery_"))
        self.recovery = InstallationRecoveryService()

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stage_tracking(self):
        """Verify tracking of active installation pipeline stage."""
        self.assertIsNone(self.recovery.current_stage)
        self.recovery.track_stage(4, "Creating GPT partition table")
        self.assertEqual(self.recovery.current_stage, "Step 4: Creating GPT partition table")

    def test_cleanup_temporary_dirs_and_files(self):
        """Verify cleanup_on_error cleans tracked artifacts and target disk."""
        staging_dir = self.temp_dir / "staging"
        staging_dir.mkdir()
        (staging_dir / "test.txt").write_text("temp content")

        temp_file = self.temp_dir / "temp_marker.tmp"
        temp_file.write_text("temp")

        target_file = self.temp_dir / "target_disk.img"
        target_file.write_bytes(b"\x00" * 1024)

        self.recovery.track_temp_dir(staging_dir)
        self.recovery.track_temp_file(temp_file)
        self.recovery.track_stage(7, "Formatting filesystem")

        report = self.recovery.cleanup_on_error(remove_target_file=target_file)

        self.assertIsInstance(report, RecoveryReport)
        self.assertEqual(report.stage_at_failure, "Step 7: Formatting filesystem")
        self.assertTrue(report.target_disk_cleaned)
        self.assertFalse(staging_dir.exists())
        self.assertFalse(temp_file.exists())
        self.assertFalse(target_file.exists())

    def test_recover_interrupted_partitioning(self):
        """Verify interrupted partitioning wipes GPT headers and removes corrupt file."""
        corrupt_disk = self.temp_dir / "corrupted_partitioning.img"
        # Write 64 sectors of mock partition data
        corrupt_disk.write_bytes(b"CORRUPTED_GPT_DATA" * 500)

        success = self.recovery.recover_interrupted_partitioning(corrupt_disk)
        self.assertTrue(success)
        self.assertFalse(corrupt_disk.exists())

    def test_recover_interrupted_filesystem(self):
        """Verify interrupted filesystem removes partial staging directory."""
        partial_fs = self.temp_dir / "partial_esp"
        partial_fs.mkdir()
        (partial_fs / "EFI" / "BOOT").mkdir(parents=True)

        success = self.recovery.recover_interrupted_filesystem(partial_fs)
        self.assertTrue(success)
        self.assertFalse(partial_fs.exists())

    def test_recover_failed_bootloader(self):
        """Verify failed bootloader deployment purges corrupt EFI and boot directories."""
        staging = self.temp_dir / "failed_esp"
        staging.mkdir()
        (staging / "EFI" / "BOOT").mkdir(parents=True)
        (staging / "boot" / "grub").mkdir(parents=True)
        (staging / "unrelated_doc.txt").write_text("keep")

        success = self.recovery.recover_failed_bootloader(staging)
        self.assertTrue(success)
        self.assertFalse((staging / "EFI").exists())
        self.assertFalse((staging / "boot").exists())
        self.assertTrue((staging / "unrelated_doc.txt").exists())


if __name__ == "__main__":
    unittest.main()

