"""Integration tests for storage failure modes, partition absences, and corruption recovery."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.boot_config.generator import GrubMenuGenerator
from core.iso_manager import (
    ISORegistrationManager,
    MenuSyncRollbackError,
)
from core.storage.storage_manager import StorageManager
from tests.unit.test_iso_validation import make_mock_iso


class TestStorageFailures(unittest.TestCase):
    """Test suite verifying system resilience under storage failures and corruption."""

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

        self.iso_path = self.storage.iso_directory_path / "rescue.iso"
        make_mock_iso(self.iso_path, volume_id="RESCUE_TEST")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_data_partition_handling(self):
        """Test graceful failure when MVX_DATA mount is missing."""
        missing_data = self.root_path / "NON_EXISTENT_DATA"
        storage = StorageManager(data_mount=missing_data, boot_mount=self.boot_mount)
        valid, reasons = storage.validate_data_partition_structure()
        self.assertFalse(valid)
        self.assertTrue(any("does not exist" in r for r in reasons))

        with self.assertRaises(FileNotFoundError):
            storage.load_configuration()

    def test_missing_boot_partition_handling(self):
        """Test graceful operation when MVX_BOOT is omitted or unmounted."""
        # When boot_mount is None, storage manager handles it without crashing
        headless_storage = StorageManager(data_mount=self.data_mount, boot_mount=None)
        res = headless_storage.sync_boot_menu()
        self.assertIsNone(res)

        # Registration can still proceed without boot partition if sync is optional
        headless_manager = ISORegistrationManager(headless_storage)
        rec = headless_manager.register_iso(self.iso_path, display_name="Headless Environment")
        self.assertEqual(rec.display_name, "Headless Environment")

    def test_corrupted_config_json_handling(self):
        """Test handling of syntax-corrupted or empty mventx.json file."""
        cfg_file = self.storage.config_file_path

        # 1. Truncated / malformed JSON
        cfg_file.write_text("{ \"version\": \"1.0.0\", \"meta\": ", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            self.storage.load_configuration()

        # 2. Empty file
        cfg_file.write_text("", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            self.storage.load_configuration()

    def test_menu_sync_rollback_on_rename_failure(self):
        """Test that menu synchronization failure during rename rolls back display name."""
        rec = self.manager.register_iso(self.iso_path, display_name="Original Name")
        self.assertEqual(self.manager.list_environments()[0].display_name, "Original Name")

        with patch.object(GrubMenuGenerator, "write_menu_file", side_effect=IOError("Write protected boot partition")):
            with self.assertRaises(MenuSyncRollbackError):
                self.manager.rename_environment(rec.id, "New Name That Should Rollback")

        # Configuration must remain as Original Name
        reloaded_rec = self.manager.list_environments()[0]
        self.assertEqual(reloaded_rec.display_name, "Original Name")

    def test_menu_sync_rollback_on_remove_failure(self):
        """Test that menu synchronization failure during removal rolls back registration removal."""
        rec = self.manager.register_iso(self.iso_path, display_name="Doomed System")
        self.assertEqual(len(self.manager.list_environments()), 1)

        with patch.object(GrubMenuGenerator, "write_menu_file", side_effect=IOError("Boot partition unmounted")):
            with self.assertRaises(MenuSyncRollbackError):
                self.manager.remove_environment(rec.id, delete_file=False)

        # Environment registration must remain intact
        self.assertEqual(len(self.manager.list_environments()), 1)
        self.assertEqual(self.manager.list_environments()[0].id, rec.id)


if __name__ == "__main__":
    unittest.main()

