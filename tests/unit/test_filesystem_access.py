"""Unit tests for MVentX Filesystem Access and Atomic Configuration Synchronizer."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.host.device_identity import DeviceIdentity, DeviceIdentityMismatchError
from core.host.filesystem_access import (
    ConfigurationRollbackError,
    ConfigurationSyncError,
    FilesystemAccessor,
)


class TestFilesystemAccess(unittest.TestCase):
    """Test atomic configuration writes, schema enforcement, automated backups, and rollback."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_test_fs_"))
        self.data_mount = self.temp_dir / "data"
        self.boot_mount = self.temp_dir / "boot"
        self.data_mount.mkdir(parents=True)
        self.boot_mount.mkdir(parents=True)

        self.valid_config = {
            "version": "1.0.0",
            "meta": {"title": "Test Title", "theme": "mventx", "timeout": 10},
            "environments": [
                {
                    "id": "env-001",
                    "display_name": "Ubuntu Live",
                    "type": "iso",
                    "iso_path": "/ISO/ubuntu.iso",
                    "boot_method": "linux_casper",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }
        self.identity = DeviceIdentity(
            disk_guid="test-guid-1234",
            hardware_serial="TEST_SERIAL",
            model="Virtual Disk",
            capacity_bytes=536870912,
            is_virtual=True,
        )
        self.accessor = FilesystemAccessor(
            data_mount=self.data_mount,
            boot_mount=self.boot_mount,
            expected_identity=self.identity,
            identity_fetcher=lambda: self.identity,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_check_read_write_access_valid(self):
        ok, msg = FilesystemAccessor.check_read_write_access(self.data_mount)
        self.assertTrue(ok)

    def test_write_config_atomically_success(self):
        cfg_path = self.accessor.write_config_atomically(self.valid_config, create_backup=False)
        self.assertTrue(cfg_path.is_file())
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["version"], "1.0.0")
        self.assertEqual(data["meta"]["title"], "Test Title")

    def test_write_config_schema_validation_rejection(self):
        invalid_config = {"invalid": True}
        with self.assertRaises(ConfigurationSyncError):
            self.accessor.write_config_atomically(invalid_config)

    def test_automated_backup_and_rotation(self):
        # First write
        self.accessor.write_config_atomically(self.valid_config, create_backup=False)
        # Multiple updates with backup creation
        for idx in range(5):
            updated = dict(self.valid_config)
            updated["meta"] = {"title": f"Update {idx}", "theme": "mventx", "timeout": 10}
            self.accessor.write_config_atomically(updated, create_backup=True, max_retained_backups=3)

        backups = list(self.accessor.backups_dir.glob("mventx_*.json"))
        # Max retained was set to 3
        self.assertLessEqual(len(backups), 3)

    def test_sync_configuration_with_menu_success(self):
        cfg_p, menu_p = self.accessor.sync_configuration_with_menu(self.valid_config)
        self.assertTrue(cfg_p.is_file())
        self.assertIsNotNone(menu_p)
        self.assertTrue(menu_p.is_file())
        menu_text = menu_p.read_text(encoding="utf-8")
        self.assertIn("Ubuntu Live", menu_text)

    def test_rollback_on_menu_generation_failure(self):
        # Write initial valid configuration
        self.accessor.write_config_atomically(self.valid_config, create_backup=False)

        # Make boot mount read-only / uncreatable to trigger failure during menu write
        bad_boot_mount = self.temp_dir / "uncreatable_boot_file"
        bad_boot_mount.write_text("file_not_dir", encoding="utf-8")  # parent mkdir will fail
        faulty_accessor = FilesystemAccessor(
            data_mount=self.data_mount,
            boot_mount=bad_boot_mount / "sub_path",
            expected_identity=self.identity,
            identity_fetcher=lambda: self.identity,
        )

        mutated_config = dict(self.valid_config)
        mutated_config["meta"] = {"title": "Mutated Should Rollback", "theme": "mventx", "timeout": 10}

        with self.assertRaises(ConfigurationRollbackError):
            faulty_accessor.sync_configuration_with_menu(mutated_config)

        # Confirm mventx.json rolled back to original title
        with open(self.accessor.config_file, "r", encoding="utf-8") as f:
            restored = json.load(f)
        self.assertEqual(restored["meta"]["title"], "Test Title")

    def test_identity_revalidation_guard_triggers_on_mismatch(self):
        imposter = DeviceIdentity(disk_guid="imposter-guid", is_virtual=False)
        mismatch_accessor = FilesystemAccessor(
            data_mount=self.data_mount,
            boot_mount=self.boot_mount,
            expected_identity=self.identity,
            identity_fetcher=lambda: imposter,
        )
        with self.assertRaises(DeviceIdentityMismatchError):
            mismatch_accessor.write_config_atomically(self.valid_config)


if __name__ == "__main__":
    unittest.main()
