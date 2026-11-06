"""Unit tests for ISO Environment Registration and Lifecycle Management."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.boot_config.generator import (
    ConfigSecurityError,
    ConfigValidationError,
    GrubMenuGenerator,
)
from core.iso_manager import (
    DuplicateRegistrationError,
    EnvironmentNotFoundError,
    EnvironmentRecord,
    ISORegistrationManager,
    MenuSyncRollbackError,
    PathTraversalError,
    RegistrationError,
)
from core.storage.storage_manager import StorageManager
from tests.unit.test_iso_validation import make_mock_iso


class TestISORegistrationEngine(unittest.TestCase):
    """Comprehensive test suite for the ISO registration backend."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        # Setup mock MVX_DATA and MVX_BOOT mount points
        self.data_mount = self.root_path / "MVX_DATA"
        self.boot_mount = self.root_path / "MVX_BOOT"
        self.data_mount.mkdir()
        self.boot_mount.mkdir()

        # Prepare boot mount grub directory
        (self.boot_mount / "boot" / "grub").mkdir(parents=True)

        self.storage_manager = StorageManager(
            data_mount=self.data_mount,
            boot_mount=self.boot_mount,
        )
        self.manager = ISORegistrationManager(self.storage_manager)

        # Create sample ISO in MVX_DATA/ISO/
        self.ubuntu_iso = self.manager.iso_directory / "ubuntu-24.04-desktop-amd64.iso"
        make_mock_iso(
            self.ubuntu_iso,
            volume_id="UBUNTU_24_04_X86_64",
            system_id="LINUX",
            publisher_id="CANONICAL",
            format_type="iso9660",
        )

        self.fedora_iso = self.manager.iso_directory / "fedora-40-x86_64.iso"
        make_mock_iso(
            self.fedora_iso,
            volume_id="FEDORA_40_X86_64",
            system_id="LINUX",
            publisher_id="RED_HAT",
            format_type="iso9660",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    # --------------------------------------------------------------------------
    # 1. Manual Registration
    # --------------------------------------------------------------------------

    def test_manual_registration_success(self):
        """Verify successful manual registration of an ISO."""
        record = self.manager.register_iso(
            iso_path="ubuntu-24.04-desktop-amd64.iso",
            display_name="Ubuntu 24.04 LTS Desktop",
            boot_method="linux_generic_loopback",
            description="Canonical Ubuntu 24.04 Noble Numbat",
            enabled=True,
            calculate_hash=True,
        )

        self.assertIsInstance(record, EnvironmentRecord)
        self.assertEqual(record.display_name, "Ubuntu 24.04 LTS Desktop")
        self.assertEqual(record.type, "iso")
        self.assertEqual(record.boot_method, "linux_generic_loopback")
        self.assertTrue(record.enabled)
        self.assertEqual(record.order, 1)
        self.assertEqual(record.iso_path, "ISO/ubuntu-24.04-desktop-amd64.iso")
        self.assertIsNotNone(record.file_size_bytes)
        self.assertGreater(record.file_size_bytes, 0)
        self.assertIsNotNone(record.registered_at)
        self.assertIsNotNone(record.checksum)

        # Verify authoritative mventx.json contains the entry
        config = self.storage_manager.load_configuration()
        envs = config["environments"]
        self.assertEqual(len(envs), 1)
        self.assertEqual(envs[0]["id"], record.id)
        self.assertEqual(envs[0]["display_name"], "Ubuntu 24.04 LTS Desktop")

        # Verify metadata file created
        meta_file = self.manager.metadata_directory / f"{record.id}.json"
        self.assertTrue(meta_file.is_file())

        # Verify GRUB menu was synchronized
        menu_path = self.storage_manager.generated_menu_path
        self.assertTrue(menu_path.is_file())
        menu_content = menu_path.read_text(encoding="utf-8")
        self.assertIn("Ubuntu 24.04 LTS Desktop", menu_content)
        self.assertIn(record.id, menu_content)

    def test_never_automatically_register_isos(self):
        """Verify that merely having ISOs in the directory does not register them."""
        # Setup: 2 ISOs exist on disk, 0 registered
        environments = self.manager.list_environments()
        self.assertEqual(len(environments), 0)

        # Unregistered ISOs are listed for UI selection
        unregistered = self.manager.list_unregistered_isos()
        self.assertEqual(len(unregistered), 2)

        # Register only ONE ISO
        self.manager.register_iso(
            iso_path=self.ubuntu_iso.name,
            display_name="Ubuntu Only",
        )

        # Verify exactly 1 registered
        environments = self.manager.list_environments()
        self.assertEqual(len(environments), 1)
        self.assertEqual(environments[0].display_name, "Ubuntu Only")

        # The other ISO remains unregistered
        unregistered = self.manager.list_unregistered_isos()
        self.assertEqual(len(unregistered), 1)
        self.assertEqual(unregistered[0].name, "fedora-40-x86_64.iso")

    def test_duplicate_registration_rejection(self):
        """Verify duplicate registration of the same ISO is rejected."""
        self.manager.register_iso(
            iso_path="ubuntu-24.04-desktop-amd64.iso",
            display_name="Ubuntu 24.04",
        )

        # Attempt to register again with same relative path
        with self.assertRaises(DuplicateRegistrationError):
            self.manager.register_iso(
                iso_path="ubuntu-24.04-desktop-amd64.iso",
                display_name="Ubuntu Duplicate",
            )

        # Attempt to register with full path 'ISO/ubuntu-...'
        with self.assertRaises(DuplicateRegistrationError):
            self.manager.register_iso(
                iso_path="ISO/ubuntu-24.04-desktop-amd64.iso",
                display_name="Ubuntu Duplicate 2",
            )

        # Attempt to register using absolute path to the same file
        with self.assertRaises(DuplicateRegistrationError):
            self.manager.register_iso(
                iso_path=self.ubuntu_iso,
                display_name="Ubuntu Duplicate 3",
            )

    def test_directory_traversal_protection(self):
        """Verify directory traversal attempts outside MVX_DATA/ISO/ are blocked."""
        # Create an ISO outside MVX_DATA/ISO/
        outside_iso = self.root_path / "secret.iso"
        make_mock_iso(outside_iso)

        with self.assertRaises(PathTraversalError):
            self.manager.register_iso(
                iso_path="../secret.iso",
                display_name="Secret Outside ISO",
            )

        with self.assertRaises(PathTraversalError):
            self.manager.register_iso(
                iso_path=outside_iso,
                display_name="Secret Outside ISO 2",
            )

    def test_invalid_file_rejections(self):
        """Verify rejection of non-existent, empty, non-iso, and corrupt files."""
        # Non-existent file
        with self.assertRaises(FileNotFoundError):
            self.manager.register_iso(
                iso_path="does-not-exist.iso",
                display_name="Ghost ISO",
            )

        # Non-iso extension
        txt_file = self.manager.iso_directory / "notes.txt"
        txt_file.write_text("Hello")
        with self.assertRaises(RegistrationError) as ctx:
            self.manager.register_iso(
                iso_path="notes.txt",
                display_name="Text File",
            )
        self.assertIn("extension must be '.iso'", str(ctx.exception))

        # Empty ISO file
        empty_iso = self.manager.iso_directory / "empty.iso"
        empty_iso.touch()
        with self.assertRaises(RegistrationError) as ctx:
            self.manager.register_iso(
                iso_path="empty.iso",
                display_name="Empty ISO",
            )
        self.assertIn("empty", str(ctx.exception))

        # Corrupt ISO header
        corrupt_iso = self.manager.iso_directory / "corrupt.iso"
        corrupt_iso.write_bytes(b"\xaa" * 40000)
        with self.assertRaises(RegistrationError) as ctx:
            self.manager.register_iso(
                iso_path="corrupt.iso",
                display_name="Corrupt ISO",
            )
        self.assertIn("No valid ISO9660 or UDF", str(ctx.exception))

    def test_custom_display_names_and_security(self):
        """Verify display name sanitization and injection prevention."""
        # Valid custom name with punctuation and unicode
        rec = self.manager.register_iso(
            iso_path="ubuntu-24.04-desktop-amd64.iso",
            display_name="My Custom Ubuntu (Workstation & Dev) [v24.04]",
        )
        self.assertEqual(rec.display_name, "My Custom Ubuntu (Workstation & Dev) [v24.04]")

        # Semicolon injection attempt
        with self.assertRaises(ConfigSecurityError):
            self.manager.register_iso(
                iso_path="fedora-40-x86_64.iso",
                display_name="Fedora; reboot;",
            )

        # Command substitution injection attempt
        with self.assertRaises(ConfigSecurityError):
            self.manager.register_iso(
                iso_path="fedora-40-x86_64.iso",
                display_name="Fedora $(reboot)",
            )

    # --------------------------------------------------------------------------
    # 2. Environment Lifecycle Management
    # --------------------------------------------------------------------------

    def test_enable_disable_environment(self):
        """Verify enabling and disabling environments updates config and boot menu."""
        rec = self.manager.register_iso(
            iso_path="ubuntu-24.04-desktop-amd64.iso",
            display_name="Ubuntu 24.04",
            enabled=True,
        )

        menu_file = self.storage_manager.generated_menu_path
        self.assertIn("Ubuntu 24.04", menu_file.read_text(encoding="utf-8"))

        # Disable
        disabled_rec = self.manager.disable_environment(rec.id)
        self.assertFalse(disabled_rec.enabled)

        # Verify excluded from active menu (fallback menu shown when no envs active)
        menu_content = menu_file.read_text(encoding="utf-8")
        self.assertNotIn('menuentry "Ubuntu 24.04"', menu_content)
        self.assertIn("No environments are currently enabled", menu_content)

        # Re-enable
        enabled_rec = self.manager.enable_environment(rec.id)
        self.assertTrue(enabled_rec.enabled)
        self.assertIn('menuentry "Ubuntu 24.04"', menu_file.read_text(encoding="utf-8"))

    def test_rename_environment(self):
        """Verify renaming an environment updates config and boot menu."""
        rec = self.manager.register_iso(
            iso_path="ubuntu-24.04-desktop-amd64.iso",
            display_name="Initial Name",
        )

        updated = self.manager.rename_environment(rec.id, "Renamed Ubuntu 24.04")
        self.assertEqual(updated.display_name, "Renamed Ubuntu 24.04")

        # Verify in boot menu
        menu_content = self.storage_manager.generated_menu_path.read_text(encoding="utf-8")
        self.assertIn("Renamed Ubuntu 24.04", menu_content)
        self.assertNotIn("Initial Name", menu_content)

        # Non-existent environment
        with self.assertRaises(EnvironmentNotFoundError):
            self.manager.rename_environment("ghost_id", "New Name")

    def test_reorder_environments(self):
        """Verify reordering registered environments."""
        rec1 = self.manager.register_iso("ubuntu-24.04-desktop-amd64.iso", "Ubuntu")
        rec2 = self.manager.register_iso("fedora-40-x86_64.iso", "Fedora")

        self.assertEqual(rec1.order, 1)
        self.assertEqual(rec2.order, 2)

        # Reverse the order
        reordered = self.manager.reorder_environments([rec2.id, rec1.id])
        self.assertEqual(len(reordered), 2)
        self.assertEqual(reordered[0].id, rec2.id)
        self.assertEqual(reordered[0].order, 1)
        self.assertEqual(reordered[1].id, rec1.id)
        self.assertEqual(reordered[1].order, 2)

        # Incomplete / invalid reorder list
        with self.assertRaises(ValueError):
            self.manager.reorder_environments([rec1.id])

    def test_remove_environment_preserves_file_by_default(self):
        """Verify removing registration does NOT delete the ISO file."""
        rec = self.manager.register_iso("ubuntu-24.04-desktop-amd64.iso", "Ubuntu")
        self.assertTrue(self.ubuntu_iso.is_file())

        # Remove without deleting file
        success = self.manager.remove_environment(rec.id, delete_file=False)
        self.assertTrue(success)

        # Verify removed from registry
        envs = self.manager.list_environments()
        self.assertEqual(len(envs), 0)

        # Invariant: ISO file is preserved on disk!
        self.assertTrue(self.ubuntu_iso.is_file())

        # Metadata file is cleaned up
        meta_file = self.manager.metadata_directory / f"{rec.id}.json"
        self.assertFalse(meta_file.exists())

    def test_remove_environment_optional_file_deletion(self):
        """Verify deleting underlying file requires explicit confirmation."""
        rec = self.manager.register_iso("ubuntu-24.04-desktop-amd64.iso", "Ubuntu")

        # Attempt to delete without confirmation -> ValueError
        with self.assertRaises(ValueError) as ctx:
            self.manager.remove_environment(rec.id, delete_file=True, confirm_deletion=False)
        self.assertIn("explicit confirmation", str(ctx.exception))
        self.assertTrue(self.ubuntu_iso.is_file())

        # Delete with explicit confirmation
        success = self.manager.remove_environment(rec.id, delete_file=True, confirm_deletion=True)
        self.assertTrue(success)
        self.assertFalse(self.ubuntu_iso.exists())

    # --------------------------------------------------------------------------
    # 3. Storage Metrics & Missing File Detection
    # --------------------------------------------------------------------------

    def test_detect_missing_isos(self):
        """Verify detection of registered environments whose ISOs were moved or deleted."""
        rec = self.manager.register_iso("ubuntu-24.04-desktop-amd64.iso", "Ubuntu")
        self.assertEqual(len(self.manager.detect_missing_isos()), 0)

        # Delete ISO file directly behind the scenes
        self.ubuntu_iso.unlink()

        missing = self.manager.detect_missing_isos()
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0][0], rec.id)
        self.assertEqual(missing[0][1], "ISO/ubuntu-24.04-desktop-amd64.iso")

    def test_storage_metrics_reporting(self):
        """Verify storage capacity reporting and distinction between ISO size and disk usage."""
        self.manager.register_iso("ubuntu-24.04-desktop-amd64.iso", "Ubuntu")
        self.manager.register_iso("fedora-40-x86_64.iso", "Fedora")

        metrics = self.manager.get_storage_metrics()
        self.assertGreater(metrics.total_bytes, 0)
        self.assertGreater(metrics.available_bytes, 0)
        self.assertEqual(metrics.registered_iso_count, 2)
        self.assertGreater(metrics.total_registered_iso_bytes, 0)
        self.assertEqual(metrics.missing_iso_count, 0)

        # Invariant: Total registered ISO size != Total partition used size
        self.assertLessEqual(metrics.total_registered_iso_bytes, metrics.used_bytes)

    # --------------------------------------------------------------------------
    # 4. Atomic Updates, Backups, and Menu Sync Rollback
    # --------------------------------------------------------------------------

    def test_atomic_backups_created_on_mutations(self):
        """Verify timestamped backups are generated in MVX_DATA/backups/."""
        self.manager.register_iso("ubuntu-24.04-desktop-amd64.iso", "Ubuntu")
        self.manager.register_iso("fedora-40-x86_64.iso", "Fedora")

        backups = list(self.manager.storage_manager.backups_directory_path.glob("mventx_*.json"))
        self.assertGreaterEqual(len(backups), 2)

    def test_rollback_on_menu_sync_failure(self):
        """Verify configuration is rolled back to prior state if menu generation fails."""
        # 1. Register valid initial environment
        initial_rec = self.manager.register_iso("ubuntu-24.04-desktop-amd64.iso", "Ubuntu Initial")
        initial_config = self.storage_manager.load_configuration()
        self.assertEqual(len(initial_config["environments"]), 1)

        # 2. Simulate menu generator throwing an error on next sync
        with patch.object(StorageManager, "sync_boot_menu", side_effect=RuntimeError("Disk write error")):
            with self.assertRaises(MenuSyncRollbackError):
                self.manager.register_iso("fedora-40-x86_64.iso", "Fedora Failure")

        # 3. Verify configuration rolled back to initial state
        current_config = self.storage_manager.load_configuration()
        self.assertEqual(len(current_config["environments"]), 1)
        self.assertEqual(current_config["environments"][0]["id"], initial_rec.id)

        # 4. Verify no orphan metadata file for the failed registration
        fedora_meta_files = list(self.manager.metadata_directory.glob("fedora*.json"))
        self.assertEqual(len(fedora_meta_files), 0)


if __name__ == "__main__":
    unittest.main()
