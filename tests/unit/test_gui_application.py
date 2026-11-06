"""Unit and integration tests for the PySide6 MVentX Desktop Manager."""

import os
from pathlib import Path
import tempfile
import unittest

# Ensure headless Qt offscreen platform
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from core.iso_manager import EnvironmentRecord
from manager.main import create_app
from manager.services.device_service import ActiveTarget, DeviceService
from manager.services.iso_service import ISOService
from manager.ui.main_window import MainWindow
from tests.unit.test_iso_validation import make_mock_iso


class TestDesktopManagerGUI(unittest.TestCase):
    """Test suite covering the PySide6 desktop management interface."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])

    def setUp(self):
        self.info_patcher = patch.object(QMessageBox, "information", return_value=QMessageBox.Ok)
        self.warn_patcher = patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok)
        self.crit_patcher = patch.object(QMessageBox, "critical", return_value=QMessageBox.Ok)
        self.info_patcher.start()
        self.warn_patcher.start()
        self.crit_patcher.start()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        self.data_dir = self.root_path / "MVX_DATA"
        self.boot_dir = self.root_path / "MVX_BOOT"
        self.data_dir.mkdir()
        self.boot_dir.mkdir()
        (self.boot_dir / "boot" / "grub").mkdir(parents=True)

        # Stage mock ISO in MVX_DATA/ISO/
        self.iso_dir = self.data_dir / "ISO"
        self.iso_dir.mkdir(parents=True)
        self.ubuntu_iso = self.iso_dir / "ubuntu-24.04-desktop-amd64.iso"
        make_mock_iso(
            self.ubuntu_iso,
            volume_id="UBUNTU_24_04_X86_64",
            system_id="LINUX",
            publisher_id="CANONICAL",
            format_type="iso9660",
        )

        self.fedora_iso = self.iso_dir / "fedora-40-x86_64.iso"
        make_mock_iso(
            self.fedora_iso,
            volume_id="FEDORA_40_X86_64",
            system_id="LINUX",
            publisher_id="RED_HAT",
            format_type="iso9660",
        )

        # Create application and window targeting our test directories
        _, self.window = create_app(data_path=self.data_dir, boot_path=self.boot_dir)
        self.device_service = self.window.device_service
        self.iso_service = self.window.iso_service

    def tearDown(self):
        self.info_patcher.stop()
        self.warn_patcher.stop()
        self.crit_patcher.stop()
        self.window.close()
        self.temp_dir.cleanup()

    def test_application_initialization_and_window_properties(self):
        """Verify window initializes with correct title, size, and styling."""
        self.assertIn("MVentX Manager", self.window.windowTitle())
        self.assertEqual(self.window.content_stack.count(), 6)
        self.assertTrue(self.device_service.has_active_target())
        self.assertEqual(self.device_service.active_target.data_mount, self.data_dir.resolve())

    def test_sidebar_navigation_switches_screens(self):
        """Verify sidebar navigation buttons switch stacked screens correctly."""
        # Initial screen is Dashboard (0)
        self.assertEqual(self.window.content_stack.currentIndex(), 0)

        # Switch to Library (1)
        self.window.switch_screen(1)
        self.assertEqual(self.window.content_stack.currentIndex(), 1)
        self.assertTrue(self.window.btn_nav_library.isChecked())

        # Switch to Add Environment (2)
        self.window.switch_screen(2)
        self.assertEqual(self.window.content_stack.currentIndex(), 2)
        self.assertTrue(self.window.btn_nav_add.isChecked())

        # Switch to Storage Manager (3)
        self.window.switch_screen(3)
        self.assertEqual(self.window.content_stack.currentIndex(), 3)
        self.assertTrue(self.window.btn_nav_storage.isChecked())

        # Switch to USB Installer (4)
        self.window.switch_screen(4)
        self.assertEqual(self.window.content_stack.currentIndex(), 4)
        self.assertTrue(self.window.btn_nav_installer.isChecked())

        # Switch to Settings (5)
        self.window.switch_screen(5)
        self.assertEqual(self.window.content_stack.currentIndex(), 5)
        self.assertTrue(self.window.btn_nav_settings.isChecked())

        # Return to Dashboard (0)
        self.window.switch_screen(0)
        self.assertEqual(self.window.content_stack.currentIndex(), 0)
        self.assertTrue(self.window.btn_nav_dashboard.isChecked())

    def test_manual_registration_workflow_from_service(self):
        """Verify registering an ISO updates library table, config, and GRUB menu."""
        # 1. Unregistered list should detect 2 ISOs
        unreg = self.iso_service.list_unregistered_isos()
        self.assertEqual(len(unreg), 2)

        # 2. Register Ubuntu
        rec = self.iso_service.register_iso(
            iso_file=self.ubuntu_iso.name,
            display_name="Ubuntu 24.04 LTS Desktop",
            boot_method="linux_generic_loopback",
            description="LTS Workstation",
            enabled=True,
        )
        self.assertIsNotNone(rec)
        self.assertEqual(rec.display_name, "Ubuntu 24.04 LTS Desktop")

        # 3. Verify Library screen table rows
        self.window.switch_screen(1)
        self.assertEqual(self.window.library_screen.table.rowCount(), 1)
        item = self.window.library_screen.table.item(0, 1)
        self.assertEqual(item.text(), "Ubuntu 24.04 LTS Desktop")

        # 4. Verify menu synchronization on boot partition
        menu_path = self.boot_dir / "boot" / "grub" / "mventx_menu.cfg"
        self.assertTrue(menu_path.is_file())
        self.assertIn("Ubuntu 24.04 LTS Desktop", menu_path.read_text(encoding="utf-8"))

    def test_enable_disable_action(self):
        """Verify toggle enable/disable updates table badge and GRUB menu."""
        rec = self.iso_service.register_iso(self.ubuntu_iso.name, "Ubuntu 24.04")
        self.assertTrue(rec.enabled)

        # Disable
        self.iso_service.toggle_environment_enabled(rec.id, False)
        envs = self.iso_service.list_environments()
        self.assertFalse(envs[0].enabled)

        # Verify excluded from active menu
        menu_path = self.boot_dir / "boot" / "grub" / "mventx_menu.cfg"
        self.assertIn("No environments are currently enabled", menu_path.read_text(encoding="utf-8"))

        # Re-enable
        self.iso_service.toggle_environment_enabled(rec.id, True)
        envs = self.iso_service.list_environments()
        self.assertTrue(envs[0].enabled)
        self.assertIn('menuentry "Ubuntu 24.04"', menu_path.read_text(encoding="utf-8"))

    def test_rename_action(self):
        """Verify renaming updates table and GRUB menu."""
        rec = self.iso_service.register_iso(self.ubuntu_iso.name, "Original Name")
        success = self.iso_service.rename_environment(rec.id, "Updated Custom Name")
        self.assertTrue(success)

        envs = self.iso_service.list_environments()
        self.assertEqual(envs[0].display_name, "Updated Custom Name")

        menu_path = self.boot_dir / "boot" / "grub" / "mventx_menu.cfg"
        self.assertIn("Updated Custom Name", menu_path.read_text(encoding="utf-8"))

    def test_reorder_action(self):
        """Verify moving environments reorders entries."""
        rec1 = self.iso_service.register_iso(self.ubuntu_iso.name, "Ubuntu")
        rec2 = self.iso_service.register_iso(self.fedora_iso.name, "Fedora")

        self.assertEqual(rec1.order, 1)
        self.assertEqual(rec2.order, 2)

        # Move Fedora up
        self.iso_service.move_environment(rec2.id, "up")
        envs = self.iso_service.list_environments()
        self.assertEqual(envs[0].id, rec2.id)
        self.assertEqual(envs[0].order, 1)
        self.assertEqual(envs[1].id, rec1.id)
        self.assertEqual(envs[1].order, 2)

    def test_remove_preserves_iso_by_default(self):
        """Verify removal leaves the physical ISO file intact by default."""
        rec = self.iso_service.register_iso(self.ubuntu_iso.name, "Ubuntu")
        self.assertTrue(self.ubuntu_iso.is_file())

        self.iso_service.remove_environment(rec.id, delete_file=False)
        self.assertEqual(len(self.iso_service.list_environments()), 0)
        self.assertTrue(self.ubuntu_iso.is_file(), "ISO file must not be deleted")

    def test_remove_with_deletion_confirmation(self):
        """Verify confirmed deletion removes file from disk."""
        rec = self.iso_service.register_iso(self.ubuntu_iso.name, "Ubuntu")
        self.assertTrue(self.ubuntu_iso.is_file())

        self.iso_service.remove_environment(rec.id, delete_file=True, confirm_deletion=True)
        self.assertEqual(len(self.iso_service.list_environments()), 0)
        self.assertFalse(self.ubuntu_iso.exists(), "ISO file must be deleted when confirmed")

    def test_settings_screen_updates_timeout_and_title(self):
        """Verify settings screen updates boot title and timeout."""
        self.window.switch_screen(4)
        self.window.settings_screen.title_edit.setText("Custom Multiboot USB")
        self.window.settings_screen.timeout_spin.setValue(15)
        self.window.settings_screen._on_save_boot_settings()

        meta = self.iso_service.get_config_metadata()
        self.assertEqual(meta.get("title"), "Custom Multiboot USB")
        self.assertEqual(meta.get("timeout"), 15)

        menu_content = (self.boot_dir / "boot" / "grub" / "mventx_menu.cfg").read_text(encoding="utf-8")
        self.assertIn('set mventx_menu_title="Custom Multiboot USB"', menu_content)
        self.assertIn("set timeout=15", menu_content)

    def test_disconnected_device_handling(self):
        """Verify application gracefully handles when no device is selected."""
        self.device_service.set_active_target(None)
        self.assertFalse(self.device_service.has_active_target())
        self.assertFalse(self.iso_service.is_ready)

        # Operations return empty results rather than crashing
        self.assertEqual(self.iso_service.list_environments(), [])
        self.assertIsNone(self.iso_service.get_storage_metrics())
        self.assertEqual(self.iso_service.detect_missing_isos(), [])

        # Refresh dashboard does not raise exception
        self.window.dashboard_screen.refresh_data()
        self.assertEqual(self.window.dashboard_screen.device_title.text(), "No MVentX Device Selected")


if __name__ == "__main__":
    unittest.main()
