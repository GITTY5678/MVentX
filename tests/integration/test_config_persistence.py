"""Integration tests for configuration persistence, atomic updates, and menu synchronization across restarts."""

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


class TestConfigPersistence(unittest.TestCase):
    """Test persistence across restarts, atomic writes, and rollback behavior."""

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

        # Stage synthetic ISOs
        self.iso1_path = self.storage.iso_directory_path / "distro1.iso"
        make_mock_iso(self.iso1_path, volume_id="DISTRO_ONE", system_id="LINUX")

        self.iso2_path = self.storage.iso_directory_path / "distro2.iso"
        make_mock_iso(self.iso2_path, volume_id="DISTRO_TWO", system_id="LINUX")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_config_persistence_across_manager_restarts(self):
        """Test registering, modifying, reordering environments, and reloading from cold restart."""
        # 1. Register distro 1
        rec1 = self.manager.register_iso(
            self.iso1_path,
            display_name="Production Linux",
            enabled=True,
            calculate_hash=True,
        )

        # 2. Register distro 2
        rec2 = self.manager.register_iso(
            self.iso2_path,
            display_name="Rescue Toolkit",
            enabled=True,
            calculate_hash=True,
        )

        # 3. Disable distro 1, rename distro 2, and reverse order
        self.manager.disable_environment(rec1.id)
        self.manager.rename_environment(rec2.id, "Emergency Diagnostics")
        self.manager.reorder_environments([rec2.id, rec1.id])

        # Verify on-disk mventx.json
        cfg_disk = self.storage.load_configuration()
        self.assertEqual(len(cfg_disk["environments"]), 2)
        self.assertEqual(cfg_disk["environments"][0]["id"], rec2.id)
        self.assertEqual(cfg_disk["environments"][0]["display_name"], "Emergency Diagnostics")
        self.assertTrue(cfg_disk["environments"][0]["enabled"])
        self.assertEqual(cfg_disk["environments"][1]["id"], rec1.id)
        self.assertFalse(cfg_disk["environments"][1]["enabled"])

        # 4. Simulate complete application shutdown and restart
        del self.manager
        del self.storage

        fresh_storage = StorageManager(self.data_mount, self.boot_mount)
        fresh_manager = ISORegistrationManager(fresh_storage)

        # Reload environments
        envs = fresh_manager.list_environments()
        self.assertEqual(len(envs), 2)
        self.assertEqual(envs[0].id, rec2.id)
        self.assertEqual(envs[0].display_name, "Emergency Diagnostics")
        self.assertTrue(envs[0].enabled)
        self.assertEqual(envs[0].iso_path, "ISO/distro2.iso")
        self.assertGreater(envs[0].file_size_bytes, 0)
        self.assertIsNotNone(envs[0].checksum)

        self.assertEqual(envs[1].id, rec1.id)
        self.assertEqual(envs[1].display_name, "Production Linux")
        self.assertFalse(envs[1].enabled)

    def test_atomic_config_write_guarantee(self):
        """Test atomic replacement guarantee and no lingering temporary files."""
        initial_cfg = self.storage.load_configuration()
        initial_cfg["meta"]["title"] = "Updated Multiboot USB"
        self.storage.write_configuration_atomically(initial_cfg, create_backup=False)

        # Verify updated config
        reloaded = self.storage.load_configuration()
        self.assertEqual(reloaded["meta"]["title"], "Updated Multiboot USB")

        # Verify no .tmp files remain in config directory
        tmp_files = list(self.storage.config_directory_path.glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)

    def test_atomic_rollback_on_menu_sync_failure(self):
        """Test that failure to write boot menu rolls back mventx.json to original state."""
        # Initial state: 0 registered environments
        initial_cfg = self.storage.load_configuration()
        self.assertEqual(len(initial_cfg["environments"]), 0)

        # Mock GrubMenuGenerator.write_menu_file to raise an OSError
        with patch.object(GrubMenuGenerator, "write_menu_file", side_effect=OSError("Disk Full or I/O Error")):
            with self.assertRaises(MenuSyncRollbackError):
                self.manager.register_iso(self.iso1_path, display_name="Should Fail")

        # Configuration must have rolled back completely
        rolled_back_cfg = self.storage.load_configuration()
        self.assertEqual(len(rolled_back_cfg["environments"]), 0)

    def test_menu_sync_fidelity_with_disabled_environments(self):
        """Test that menu generator renders enabled environments and handles disabled states properly."""
        rec1 = self.manager.register_iso(self.iso1_path, display_name="Active Distro", enabled=True)
        rec2 = self.manager.register_iso(self.iso2_path, display_name="Disabled Distro", enabled=False)

        menu_content = self.storage.generated_menu_path.read_text(encoding="utf-8")
        self.assertIn("Active Distro", menu_content)
        # Disabled environment is not emitted as bootable menuentry
        self.assertNotIn('menuentry "Disabled Distro"', menu_content)

        # Disable both environments
        self.manager.disable_environment(rec1.id)
        updated_menu = self.storage.generated_menu_path.read_text(encoding="utf-8")
        self.assertIn("No environments registered or enabled", updated_menu)


if __name__ == "__main__":
    unittest.main()
