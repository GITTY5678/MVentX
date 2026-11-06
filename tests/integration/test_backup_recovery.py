"""Integration tests for automated configuration backup, rotation, and recovery."""

import json
from pathlib import Path
import tempfile
import time
import unittest

from core.iso_manager import ISORegistrationManager
from core.storage.storage_manager import StorageManager
from tests.unit.test_iso_validation import make_mock_iso


class TestBackupRecovery(unittest.TestCase):
    """Test suite verifying automated backup creation, rotation, restoration, and safety."""

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

        # Stage synthetic ISO files
        self.iso1 = self.storage.iso_directory_path / "alpine-standard-3.20.0-x86_64.iso"
        make_mock_iso(self.iso1, volume_id="ALPINE_320", system_id="LINUX")

        self.iso2 = self.storage.iso_directory_path / "debian-12.5.0-amd64-netinst.iso"
        make_mock_iso(self.iso2, volume_id="DEBIAN_125", system_id="LINUX")

        self.iso3 = self.storage.iso_directory_path / "archlinux-2026.iso"
        make_mock_iso(self.iso3, volume_id="ARCH_2026", system_id="LINUX")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_automated_backup_on_mutation(self):
        """Test that mutations trigger automated timestamped backups in backups/."""
        # Initial registration of ISO 1
        self.manager.register_iso(self.iso1, display_name="Alpine Standard")
        backups = self.storage.list_backups()
        self.assertGreaterEqual(len(backups), 1)

        # Rename mutation
        time.sleep(0.01)  # small pause for distinct timestamp
        rec = self.manager.list_environments()[0]
        self.manager.rename_environment(rec.id, "Alpine Linux Minimal")
        backups_after = self.storage.list_backups()
        self.assertGreater(len(backups_after), len(backups))

        # Check content of latest backup
        latest_backup = backups_after[0]
        with open(latest_backup, "r", encoding="utf-8") as f:
            bdata = json.load(f)
        self.assertEqual(bdata["version"], "1.0.0")

    def test_backup_rotation_limits(self):
        """Test that backup rotation strictly respects max_retained_backups limit."""
        max_retained = 5
        # Generate 8 sequential backups directly
        for i in range(8):
            time.sleep(0.01)
            cfg = self.storage.load_configuration()
            cfg["meta"]["title"] = f"Backup Iteration {i}"
            self.storage.write_configuration_atomically(cfg, create_backup=True)
            # prune with max_retained
            self.storage.backup_configuration(max_retained_backups=max_retained)

        retained = self.storage.list_backups()
        self.assertLessEqual(len(retained), max_retained)

    def test_backup_restoration_lifecycle(self):
        """Test full backup restoration: rewrites mventx.json, syncs menu, and preserves ISOs."""
        # 1. Setup baseline with ISO 1 and ISO 2
        rec1 = self.manager.register_iso(self.iso1, display_name="Alpine Baseline")
        rec2 = self.manager.register_iso(self.iso2, display_name="Debian Baseline")

        # Manually create a designated checkpoint backup of this state
        checkpoint_backup = self.storage.backup_configuration()

        # 2. Perform severe destructive changes in configuration
        rec3 = self.manager.register_iso(self.iso3, display_name="Arch Intruder")
        self.manager.rename_environment(rec1.id, "Tampered Name")
        self.manager.remove_environment(rec2.id, delete_file=False)

        # Confirm altered state
        current_envs = self.manager.list_environments()
        names = [e.display_name for e in current_envs]
        self.assertIn("Tampered Name", names)
        self.assertIn("Arch Intruder", names)
        self.assertNotIn("Debian Baseline", names)

        # 3. Restore the checkpoint backup
        restored_data = self.storage.restore_backup(checkpoint_backup, sync_menu=True)
        self.assertEqual(len(restored_data["environments"]), 2)

        # 4. Reload manager and verify configuration fidelity
        fresh_manager = ISORegistrationManager(self.storage)
        restored_envs = fresh_manager.list_environments()
        self.assertEqual(len(restored_envs), 2)
        r_names = [e.display_name for e in restored_envs]
        self.assertIn("Alpine Baseline", r_names)
        self.assertIn("Debian Baseline", r_names)
        self.assertNotIn("Arch Intruder", r_names)

        # 5. Verify boot menu was resynchronized
        menu_text = self.storage.generated_menu_path.read_text(encoding="utf-8")
        self.assertIn("Alpine Baseline", menu_text)
        self.assertIn("Debian Baseline", menu_text)
        self.assertNotIn("Arch Intruder", menu_text)

        # 6. CRITICAL: Verify restoration NEVER deleted or modified ISO files on disk
        self.assertTrue(self.iso1.exists(), "Alpine ISO must remain intact")
        self.assertTrue(self.iso2.exists(), "Debian ISO must remain intact")
        self.assertTrue(self.iso3.exists(), "Arch ISO must remain intact on disk even when unregistered")

    def test_restore_invalid_backup_rejected(self):
        """Test that attempting to restore a corrupted backup is rejected without modifying config."""
        # Prepare valid baseline
        self.manager.register_iso(self.iso1, display_name="Original System")
        original_cfg = self.storage.load_configuration()

        # Create corrupted backup file (invalid JSON syntax)
        corrupted_path = self.storage.backups_directory_path / "corrupted.json"
        corrupted_path.write_text("{ this is NOT valid json", encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            self.storage.restore_backup(corrupted_path)
        self.assertIn("Corrupted backup", str(ctx.exception))

        # Verify configuration remains unchanged
        current_cfg = self.storage.load_configuration()
        self.assertEqual(current_cfg, original_cfg)

        # Create invalid structure backup (valid JSON, but missing required keys)
        bad_struct_path = self.storage.backups_directory_path / "bad_structure.json"
        bad_struct_path.write_text(json.dumps({"some_key": 123}), encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            self.storage.restore_backup(bad_struct_path)
        self.assertIn("missing required keys", str(ctx.exception))

        # Verify configuration still unchanged
        self.assertEqual(self.storage.load_configuration(), original_cfg)

    def test_restore_nonexistent_backup_raises_error(self):
        """Test that restoring a non-existent backup file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            self.storage.restore_backup(self.storage.backups_directory_path / "nonexistent.json")


if __name__ == "__main__":
    unittest.main()
