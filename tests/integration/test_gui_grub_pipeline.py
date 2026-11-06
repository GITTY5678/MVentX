"""End-to-End Integration tests verifying the entire GUI-to-GRUB operational pipeline across all Milestone 6 scenarios."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Ensure headless Qt offscreen platform
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QMessageBox

from core.iso_manager import EnvironmentRecord
from manager.main import create_app
from manager.services.device_service import ActiveTarget, DeviceService
from manager.services.iso_service import ISOService
from tests.unit.test_iso_validation import make_mock_iso


class TestGuiGrubPipeline(unittest.TestCase):
    """Verifies that every GUI action correctly updates authoritative JSON and regenerates GRUB boot menu."""

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

        self.iso_dir = self.data_dir / "ISO"
        self.iso_dir.mkdir(parents=True)

        # Stage synthetic test ISOs
        self.iso_alpine = self.iso_dir / "alpine-virt-3.20.0-x86_64.iso"
        make_mock_iso(self.iso_alpine, volume_id="ALPINE_320", format_type="iso9660")

        self.iso_debian = self.iso_dir / "debian-12-netinst.iso"
        make_mock_iso(self.iso_debian, volume_id="DEBIAN_12", format_type="iso9660")

        self.iso_arch = self.iso_dir / "archlinux-2026.iso"
        make_mock_iso(self.iso_arch, volume_id="ARCH_2026", format_type="iso9660")

        # Initialize GUI Application
        _, self.window = create_app(data_path=self.data_dir, boot_path=self.boot_dir)
        self.iso_service = self.window.iso_service
        self.device_service = self.window.device_service

        # Ensure initial dynamic menu is generated for baseline
        self.iso_service._registration_manager.storage_manager.sync_boot_menu()

        self.menu_file = self.boot_dir / "boot" / "grub" / "mventx_menu.cfg"
        self.config_file = self.data_dir / "config" / "mventx.json"

    def tearDown(self):
        self.info_patcher.stop()
        self.warn_patcher.stop()
        self.crit_patcher.stop()
        if hasattr(self, "window") and self.window:
            self.window.close()
        self.temp_dir.cleanup()

    def _read_config(self):
        with open(self.config_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _read_menu(self):
        return self.menu_file.read_text(encoding="utf-8")

    # --------------------------------------------------------------------------
    # Scenario A: Empty Configuration
    # --------------------------------------------------------------------------
    def test_scenario_a_empty_configuration(self):
        """Scenario A: Empty configuration renders clean fallback menu."""
        cfg = self._read_config()
        self.assertEqual(len(cfg["environments"]), 0)

        menu = self._read_menu()
        self.assertIn('menuentry "No environments registered or enabled" --id "mventx-empty"', menu)
        self.assertIn("No environments are currently enabled in mventx.json", menu)

    # --------------------------------------------------------------------------
    # Scenario B: One Registered Environment
    # --------------------------------------------------------------------------
    def test_scenario_b_one_registered_environment(self):
        """Scenario B: Exactly one registered environment appears in menu."""
        rec = self.iso_service.register_iso(
            iso_file=self.iso_alpine.name,
            display_name="Alpine Virtual 3.20",
            boot_method="linux_generic_loopback",
            enabled=True,
        )
        self.assertIsNotNone(rec)

        # 1. Authoritative JSON updated
        cfg = self._read_config()
        self.assertEqual(len(cfg["environments"]), 1)
        self.assertEqual(cfg["environments"][0]["display_name"], "Alpine Virtual 3.20")

        # 2. GRUB menu updated
        menu = self._read_menu()
        self.assertIn(f'menuentry "Alpine Virtual 3.20" --id "{rec.id}"', menu)
        self.assertNotIn("mventx-empty", menu)

        # 3. ISO file remains intact
        self.assertTrue(self.iso_alpine.is_file())

        # 4. Cold restart maintains state
        self.window.close()
        _, self.window = create_app(data_path=self.data_dir, boot_path=self.boot_dir)
        envs = self.window.iso_service.list_environments()
        self.assertEqual(len(envs), 1)
        self.assertEqual(envs[0].display_name, "Alpine Virtual 3.20")

    # --------------------------------------------------------------------------
    # Scenario C: Multiple Registered Environments
    # --------------------------------------------------------------------------
    def test_scenario_c_multiple_registered_environments(self):
        """Scenario C: Multiple registered environments appear in exact configured order."""
        rec1 = self.iso_service.register_iso(self.iso_alpine.name, "Alpine Virtual", enabled=True)
        rec2 = self.iso_service.register_iso(self.iso_debian.name, "Debian Stable", enabled=True)
        rec3 = self.iso_service.register_iso(self.iso_arch.name, "Arch Rolling", enabled=True)

        cfg = self._read_config()
        self.assertEqual(len(cfg["environments"]), 3)

        menu = self._read_menu()
        idx_alpine = menu.find('menuentry "Alpine Virtual"')
        idx_debian = menu.find('menuentry "Debian Stable"')
        idx_arch = menu.find('menuentry "Arch Rolling"')

        self.assertGreater(idx_alpine, -1)
        self.assertGreater(idx_debian, idx_alpine)
        self.assertGreater(idx_arch, idx_debian)

    # --------------------------------------------------------------------------
    # Scenario D: Disabled Environment
    # --------------------------------------------------------------------------
    def test_scenario_d_disabled_environment(self):
        """Scenario D: Disabled environment is completely absent from generated menu."""
        rec1 = self.iso_service.register_iso(self.iso_alpine.name, "Alpine Active", enabled=True)
        rec2 = self.iso_service.register_iso(self.iso_debian.name, "Debian Inactive", enabled=True)

        # Disable Debian
        self.iso_service.toggle_environment_enabled(rec2.id, False)

        cfg = self._read_config()
        self.assertFalse(cfg["environments"][1]["enabled"])

        menu = self._read_menu()
        self.assertIn('menuentry "Alpine Active"', menu)
        self.assertNotIn('menuentry "Debian Inactive"', menu)
        self.assertTrue(self.iso_debian.is_file(), "Disabled ISO file must remain untouched")

    # --------------------------------------------------------------------------
    # Scenario E: Re-enabled Environment
    # --------------------------------------------------------------------------
    def test_scenario_e_reenabled_environment(self):
        """Scenario E: Re-enabled environment returns to active menu."""
        rec1 = self.iso_service.register_iso(self.iso_alpine.name, "Alpine Standby", enabled=False)
        menu_disabled = self._read_menu()
        self.assertNotIn('menuentry "Alpine Standby"', menu_disabled)

        # Re-enable
        self.iso_service.toggle_environment_enabled(rec1.id, True)

        cfg = self._read_config()
        self.assertTrue(cfg["environments"][0]["enabled"])

        menu_enabled = self._read_menu()
        self.assertIn('menuentry "Alpine Standby"', menu_enabled)

    # --------------------------------------------------------------------------
    # Scenario F: Reordered Environments
    # --------------------------------------------------------------------------
    def test_scenario_f_reordered_environments(self):
        """Scenario F: Reordering environments updates menu sequence accordingly."""
        rec1 = self.iso_service.register_iso(self.iso_alpine.name, "First (Alpine)")
        rec2 = self.iso_service.register_iso(self.iso_debian.name, "Second (Debian)")

        # Move Debian up to position 1
        self.iso_service.move_environment(rec2.id, "up")

        cfg = self._read_config()
        self.assertEqual(cfg["environments"][0]["id"], rec2.id)
        self.assertEqual(cfg["environments"][1]["id"], rec1.id)

        menu = self._read_menu()
        idx_debian = menu.find('menuentry "Second (Debian)"')
        idx_alpine = menu.find('menuentry "First (Alpine)"')
        self.assertLess(idx_debian, idx_alpine, "Debian must appear before Alpine after reorder")

    # --------------------------------------------------------------------------
    # Scenario G: Renamed Environment
    # --------------------------------------------------------------------------
    def test_scenario_g_renamed_environment(self):
        """Scenario G: Renaming an environment updates display name in JSON and menu."""
        rec = self.iso_service.register_iso(self.iso_alpine.name, "Old Display Name")
        self.iso_service.rename_environment(rec.id, "New Brand Identity v2")

        cfg = self._read_config()
        self.assertEqual(cfg["environments"][0]["display_name"], "New Brand Identity v2")

        menu = self._read_menu()
        self.assertIn('menuentry "New Brand Identity v2"', menu)
        self.assertNotIn("Old Display Name", menu)

    # --------------------------------------------------------------------------
    # Scenario H: Removed Environment
    # --------------------------------------------------------------------------
    def test_scenario_h_removed_environment(self):
        """Scenario H: Removing registration purges entry from menu while ISO remains on disk."""
        rec = self.iso_service.register_iso(self.iso_alpine.name, "Temporary Distro")
        self.assertTrue(self.iso_alpine.is_file())

        # Remove without deleting underlying file
        self.iso_service.remove_environment(rec.id, delete_file=False)

        cfg = self._read_config()
        self.assertEqual(len(cfg["environments"]), 0)

        menu = self._read_menu()
        self.assertNotIn("Temporary Distro", menu)
        self.assertIn("mventx-empty", menu)

        # Underlying ISO must still be physically present
        self.assertTrue(self.iso_alpine.is_file(), "ISO file must not be deleted on registration removal")

    # --------------------------------------------------------------------------
    # Scenario I: Invalid Configuration Handling
    # --------------------------------------------------------------------------
    def test_scenario_i_invalid_configuration_handling(self):
        """Scenario I: Invalid config mutations fail safely without producing corrupt GRUB menu."""
        # Attempt to register with empty display name
        result = self.iso_service.register_iso(self.iso_alpine.name, "   ")
        self.assertIsNone(result)

        # Menu remains valid and fallback
        menu = self._read_menu()
        self.assertIn("mventx-empty", menu)

    # --------------------------------------------------------------------------
    # Scenario J: Special-Character Display Names & Injection Prevention
    # --------------------------------------------------------------------------
    def test_scenario_j_special_characters_and_escaping(self):
        """Scenario J: Quotes, slashes, and characters are escaped safely, while injection is rejected."""
        # Valid special characters
        rec = self.iso_service.register_iso(
            self.iso_alpine.name,
            'Alpine "Edge" [6.6-LTS] (x86_64)',
        )
        self.assertIsNotNone(rec)

        menu = self._read_menu()
        self.assertIn('menuentry "Alpine \\"Edge\\" [6.6-LTS] (x86_64)"', menu)

        # Malicious command injection attempts must be rejected
        injections = [
            'Bad"; reboot; echo "',
            "Bad; halt",
            "Bad $(reboot)",
            "Bad `reboot`",
        ]
        for inj in injections:
            res = self.iso_service.register_iso(self.iso_debian.name, inj)
            self.assertIsNone(res, f"Should have rejected injection: {inj}")

    # --------------------------------------------------------------------------
    # Global Settings: Title and Timeout
    # --------------------------------------------------------------------------
    def test_global_settings_update_pipeline(self):
        """Verify updating boot menu title and timeout in GUI updates JSON and menu."""
        success = self.iso_service.update_config_metadata(
            title="Rescue & Diagnostic Platform",
            timeout=25,
        )
        self.assertTrue(success)

        cfg = self._read_config()
        self.assertEqual(cfg["meta"]["title"], "Rescue & Diagnostic Platform")
        self.assertEqual(cfg["meta"]["timeout"], 25)

        menu = self._read_menu()
        self.assertIn('set mventx_menu_title="Rescue & Diagnostic Platform"', menu)
        self.assertIn("set timeout=25", menu)

    # --------------------------------------------------------------------------
    # Backup & Recovery Pipeline
    # --------------------------------------------------------------------------
    def test_backup_and_recovery_pipeline(self):
        """Verify restoring an earlier backup updates JSON and menu without deleting ISOs."""
        # Stage 1: Register Alpine and capture explicit checkpoint backup
        rec1 = self.iso_service.register_iso(self.iso_alpine.name, "Alpine Base")
        checkpoint = self.iso_service._registration_manager.storage_manager.backup_configuration()

        # Stage 2: Register Debian and remove Alpine
        rec2 = self.iso_service.register_iso(self.iso_debian.name, "Debian Replacement")
        self.iso_service.remove_environment(rec1.id, delete_file=False)

        menu_mutated = self._read_menu()
        self.assertIn("Debian Replacement", menu_mutated)
        self.assertNotIn("Alpine Base", menu_mutated)

        # Stage 3: Restore checkpoint backup
        restored = self.iso_service.restore_backup(checkpoint)
        self.assertTrue(restored)

        cfg_restored = self._read_config()
        self.assertEqual(len(cfg_restored["environments"]), 1)
        self.assertEqual(cfg_restored["environments"][0]["display_name"], "Alpine Base")

        menu_restored = self._read_menu()
        self.assertIn("Alpine Base", menu_restored)
        self.assertNotIn("Debian Replacement", menu_restored)

        # Both ISO files must be physically present on disk
        self.assertTrue(self.iso_alpine.is_file())
        self.assertTrue(self.iso_debian.is_file())


if __name__ == "__main__":
    unittest.main()
