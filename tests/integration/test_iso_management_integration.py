"""End-to-End Integration tests for MVentX ISO Environment Management Subsystem."""

import json
from pathlib import Path
import tempfile
import unittest

from core.boot_config.generator import GrubMenuGenerator
from core.iso_manager import (
    DuplicateRegistrationError,
    ISORegistrationManager,
    MenuSyncRollbackError,
    PathTraversalError,
)
from core.storage.storage_manager import StorageManager
from tests.unit.test_iso_validation import make_mock_iso

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "core" / "config" / "schema.json"


class TestISOManagementIntegration(unittest.TestCase):
    """End-to-End integration test covering the dual-partition storage workflow."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        # Create simulated dual partitions
        self.data_partition = self.root_path / "MVX_DATA"
        self.boot_partition = self.root_path / "MVX_BOOT"
        self.data_partition.mkdir()
        self.boot_partition.mkdir()

        # Initialize storage manager and ISO manager
        self.storage_manager = StorageManager(
            data_mount=self.data_partition,
            boot_mount=self.boot_partition,
        )
        self.storage_manager.initialize_data_partition_structure()
        (self.boot_partition / "boot" / "grub").mkdir(parents=True)

        self.iso_manager = ISORegistrationManager(self.storage_manager)

        # Stage test ISOs in MVX_DATA/ISO/
        self.ubuntu_path = self.storage_manager.iso_directory_path / "ubuntu-24.04-desktop-amd64.iso"
        make_mock_iso(
            self.ubuntu_path,
            volume_id="UBUNTU_24_04_X86_64",
            system_id="LINUX",
            publisher_id="CANONICAL",
            format_type="iso9660",
        )

        self.arch_path = self.storage_manager.iso_directory_path / "archlinux-2026.05.01-x86_64.iso"
        make_mock_iso(
            self.arch_path,
            volume_id="ARCH_202605",
            system_id="LINUX",
            publisher_id="ARCH_LINUX",
            format_type="hybrid",
        )

        self.rescue_path = self.storage_manager.iso_directory_path / "systemrescue-11.00-amd64.iso"
        make_mock_iso(
            self.rescue_path,
            volume_id="RESCUE_11",
            system_id="LINUX",
            publisher_id="SYSTEMRESCUE",
            format_type="iso9660",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_full_lifecycle_and_cross_partition_sync(self):
        """Verify the complete registration, update, disable, reorder, and deregistration workflow."""
        # 1. Initially, no environments registered despite 3 ISOs existing
        self.assertEqual(len(self.iso_manager.list_environments()), 0)
        unregistered = self.iso_manager.list_unregistered_isos()
        self.assertEqual(len(unregistered), 3)

        # Initial menu synchronization shows fallback
        self.storage_manager.sync_boot_menu()
        menu_file = self.storage_manager.generated_menu_path
        self.assertTrue(menu_file.is_file())
        self.assertIn("No environments registered or enabled", menu_file.read_text(encoding="utf-8"))

        # 2. Register first environment: Ubuntu 24.04
        env1 = self.iso_manager.register_iso(
            iso_path="ubuntu-24.04-desktop-amd64.iso",
            display_name="Ubuntu 24.04 LTS (Noble)",
            boot_method="linux_generic_loopback",
            description="Official Canonical LTS Release",
            calculate_hash=True,
        )
        self.assertEqual(env1.order, 1)
        self.assertEqual(env1.iso_path, "ISO/ubuntu-24.04-desktop-amd64.iso")
        self.assertEqual(env1.file_size_bytes, self.ubuntu_path.stat().st_size)

        # 3. Register second environment: Arch Linux
        env2 = self.iso_manager.register_iso(
            iso_path="archlinux-2026.05.01-x86_64.iso",
            display_name="Arch Linux Rolling",
            boot_method="linux_generic_loopback",
            description="Bleeding Edge Arch",
        )
        self.assertEqual(env2.order, 2)

        # Verify only 1 unregistered ISO remains
        self.assertEqual(len(self.iso_manager.list_unregistered_isos()), 1)

        # 4. Check cross-partition menu synchronization
        menu_content = menu_file.read_text(encoding="utf-8")
        self.assertIn("Ubuntu 24.04 LTS (Noble)", menu_content)
        self.assertIn("Arch Linux Rolling", menu_content)
        self.assertNotIn("No environments registered or enabled", menu_content)

        # Verify order in menu: Ubuntu entry appears before Arch entry
        idx_ubuntu = menu_content.find("Ubuntu 24.04 LTS (Noble)")
        idx_arch = menu_content.find("Arch Linux Rolling")
        self.assertLess(idx_ubuntu, idx_arch)

        # 5. Reorder environments: Place Arch first
        self.iso_manager.reorder_environments([env2.id, env1.id])
        menu_content_reordered = menu_file.read_text(encoding="utf-8")
        idx_ubuntu_new = menu_content_reordered.find("Ubuntu 24.04 LTS (Noble)")
        idx_arch_new = menu_content_reordered.find("Arch Linux Rolling")
        self.assertLess(idx_arch_new, idx_ubuntu_new)

        # 6. Rename environment
        self.iso_manager.rename_environment(env1.id, "Ubuntu Workstation 24.04")
        menu_content_renamed = menu_file.read_text(encoding="utf-8")
        self.assertIn("Ubuntu Workstation 24.04", menu_content_renamed)
        self.assertNotIn("Ubuntu 24.04 LTS (Noble)", menu_content_renamed)

        # 7. Disable Arch Linux
        self.iso_manager.disable_environment(env2.id)
        menu_content_disabled = menu_file.read_text(encoding="utf-8")
        self.assertNotIn('menuentry "Arch Linux Rolling"', menu_content_disabled)
        self.assertIn('menuentry "Ubuntu Workstation 24.04"', menu_content_disabled)

        # 8. Storage Metrics verification
        metrics = self.iso_manager.get_storage_metrics()
        self.assertEqual(metrics.registered_iso_count, 2)
        expected_size = self.ubuntu_path.stat().st_size + self.arch_path.stat().st_size
        self.assertEqual(metrics.total_registered_iso_bytes, expected_size)
        self.assertEqual(metrics.missing_iso_count, 0)

        # 9. Deregistration without file deletion
        self.iso_manager.remove_environment(env1.id, delete_file=False)
        self.assertTrue(self.ubuntu_path.is_file(), "ISO file must not be deleted when delete_file=False")

        # 10. Deregistration with confirmed file deletion
        self.iso_manager.remove_environment(env2.id, delete_file=True, confirm_deletion=True)
        self.assertFalse(self.arch_path.exists(), "ISO file must be deleted when confirmed")

        # 11. Final menu check: fallback menu is restored
        menu_final = menu_file.read_text(encoding="utf-8")
        self.assertIn("No environments registered or enabled", menu_final)


if __name__ == "__main__":
    unittest.main()

