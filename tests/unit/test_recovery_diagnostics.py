"""Unit and fault-injection test suite for Milestone 15 Recovery and Diagnostics."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Ensure headless Qt offscreen platform
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QMessageBox

from core.config.repair import ConfigRepairer, ConfigRepairResult
from core.config.validator import ConfigValidator
from core.diagnostics.diagnostic_report import DiagnosticReport, DiagnosticReportGenerator
from core.iso_manager import ISORegistrationManager
from core.storage.storage_manager import StorageManager
from manager.services.device_service import DeviceService
from manager.services.iso_service import ISOService
from manager.ui.settings import SettingsScreen


class TestRecoveryAndDiagnostics(unittest.TestCase):
    """Test suite covering fault-injection recovery, backup rotation, and diagnostics."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        self.data_dir = self.root_path / "MVX_DATA"
        self.boot_dir = self.root_path / "MVX_BOOT"
        self.data_dir.mkdir()
        self.boot_dir.mkdir()

        self.storage = StorageManager(data_mount=self.data_dir, boot_mount=self.boot_dir)
        self.storage.initialize_data_partition_structure()
        (self.boot_dir / "boot" / "grub").mkdir(parents=True, exist_ok=True)
        (self.boot_dir / "EFI" / "BOOT").mkdir(parents=True, exist_ok=True)
        (self.boot_dir / "EFI" / "BOOT" / "BOOTX64.EFI").write_bytes(b"\x00" * 512)

        # Add valid mock ISO in ISO/
        from tests.unit.test_iso_validation import make_mock_iso
        self.mock_iso = self.storage.iso_directory_path / "alpine-virt.iso"
        make_mock_iso(self.mock_iso, volume_id="ALPINE")

    def tearDown(self):
        self.temp_dir.cleanup()

    # --------------------------------------------------------------------------
    # 1. Fault-Injection: Truncated JSON & Power Failure Simulation
    # --------------------------------------------------------------------------

    def test_truncated_json_recovers_from_latest_backup(self):
        """Simulate power-loss mid-write resulting in truncated mventx.json."""
        reg_mgr = ISORegistrationManager(self.storage)
        reg_mgr.register_iso(self.mock_iso.name, "Alpine Linux", enabled=True)
        # Create backup with the registered environment
        self.storage.backup_configuration()

        # Confirm valid backup exists
        backups = self.storage.list_backups()
        self.assertTrue(len(backups) >= 1)

        # Corrupt / truncate active mventx.json
        config_path = self.storage.config_file_path
        config_path.write_text('{"version": "1.0.0", "meta": {"title": "MV', encoding="utf-8")

        # Load through StorageManager with auto_repair
        cfg = self.storage.load_configuration(auto_repair=True)

        self.assertEqual(cfg["version"], "1.0.0")
        self.assertEqual(len(cfg["environments"]), 1)
        self.assertEqual(cfg["environments"][0]["display_name"], "Alpine Linux")

    def test_truncated_json_with_no_backups_creates_clean_default(self):
        """Truncated JSON with no backups rebuilds clean default while preserving ISO files."""
        # Empty backups directory
        for b in self.storage.backups_directory_path.glob("*"):
            b.unlink()

        # Write truncated config
        self.storage.config_file_path.write_text('{"version": "1.0.', encoding="utf-8")

        cfg, res = ConfigRepairer.load_or_recover(
            config_path=self.storage.config_file_path,
            backups_dir=self.storage.backups_directory_path,
            iso_dir=self.storage.iso_directory_path,
        )

        self.assertTrue(res.repaired)
        self.assertFalse(res.recovered_from_backup)
        self.assertEqual(cfg["version"], "1.0.0")
        self.assertEqual(cfg["environments"], [])
        # Ensure ISO file was NOT touched
        self.assertTrue(self.mock_iso.is_file())

    # --------------------------------------------------------------------------
    # 2. Missing Configuration File Handling
    # --------------------------------------------------------------------------

    def test_missing_config_restores_from_backup_or_initializes(self):
        """Verify missing config file restores from backup if present."""
        reg_mgr = ISORegistrationManager(self.storage)
        reg_mgr.register_iso(self.mock_iso.name, "Alpine Virt")
        # Create backup with registered environment
        self.storage.backup_configuration()

        # Delete active mventx.json
        self.storage.config_file_path.unlink()
        self.assertFalse(self.storage.config_file_path.exists())

        cfg, res = ConfigRepairer.load_or_recover(
            config_path=self.storage.config_file_path,
            backups_dir=self.storage.backups_directory_path,
            iso_dir=self.storage.iso_directory_path,
        )

        self.assertTrue(res.repaired)
        self.assertTrue(res.recovered_from_backup)
        self.assertEqual(len(cfg["environments"]), 1)
        self.assertTrue(self.storage.config_file_path.is_file())

    # --------------------------------------------------------------------------
    # 3. Corrupted Schema & Partial Environment Salvage
    # --------------------------------------------------------------------------

    def test_salvage_corrupted_environments_array(self):
        """Verify repair_structure salvages intact environments and discards broken items."""
        broken_data = {
            "version": "1.0.0",
            "meta": {"title": "Repaired Title", "timeout": 8},
            "environments": [
                {
                    "id": "valid_1",
                    "display_name": "Valid Environment 1",
                    "boot_method": "linux_generic_loopback",
                    "iso_path": "ISO/valid1.iso",
                    "enabled": True,
                    "order": 5,
                },
                "completely_invalid_string_entry",
                {"missing_everything": True},
                {
                    "id": "valid_2",
                    "display_name": "Valid Environment 2",
                    "boot_method": "casper",
                    "iso_path": "ISO/valid2.iso",
                    "enabled": False,
                },
            ],
        }

        repaired, salvaged, corrupt = ConfigRepairer.repair_structure(broken_data)

        self.assertEqual(salvaged, 2)
        self.assertEqual(corrupt, 2)
        self.assertEqual(repaired["meta"]["title"], "Repaired Title")
        self.assertEqual(len(repaired["environments"]), 2)
        self.assertEqual(repaired["environments"][0]["id"], "valid_1")
        self.assertEqual(repaired["environments"][0]["order"], 1)  # Sequential order
        self.assertEqual(repaired["environments"][1]["id"], "valid_2")
        self.assertEqual(repaired["environments"][1]["order"], 2)

    # --------------------------------------------------------------------------
    # 4. Corrupted Backups Handling
    # --------------------------------------------------------------------------

    def test_skips_corrupted_backup_files(self):
        """Verify _find_latest_valid_backup skips malformed backups and picks the valid one."""
        backups_dir = self.storage.backups_directory_path

        # Create valid older backup
        valid_cfg = {
            "version": "1.0.0",
            "meta": {"title": "Valid Older"},
            "environments": [],
        }
        older_backup = backups_dir / "mventx_20260101_100000.json"
        older_backup.write_text(json.dumps(valid_cfg), encoding="utf-8")

        # Create corrupted newer backup
        newer_corrupt = backups_dir / "mventx_20260102_100000.json"
        newer_corrupt.write_text("CORRUPTED_JSON_CONTENT", encoding="utf-8")

        found_cfg, found_path = ConfigRepairer._find_latest_valid_backup(backups_dir)

        self.assertIsNotNone(found_cfg)
        self.assertEqual(found_path.name, older_backup.name)
        self.assertEqual(found_cfg["meta"]["title"], "Valid Older")

    # --------------------------------------------------------------------------
    # 5. Diagnostic Report Generation
    # --------------------------------------------------------------------------

    def test_diagnostic_report_generation(self):
        """Verify DiagnosticReportGenerator produces complete report with recommendations."""
        reg_mgr = ISORegistrationManager(self.storage)
        reg_mgr.register_iso(self.mock_iso.name, "Alpine Linux Virt")

        # Also add a missing ISO registration
        config = self.storage.load_configuration()
        config["environments"].append({
            "id": "missing_env",
            "display_name": "Missing Distro",
            "boot_method": "linux_generic_loopback",
            "iso_path": "ISO/non_existent.iso",
            "enabled": True,
            "order": 2,
        })
        self.storage.write_configuration_atomically(config, create_backup=False)

        generator = DiagnosticReportGenerator(
            data_mount=self.data_dir,
            boot_mount=self.boot_dir,
        )
        report = generator.generate_report()

        # Check report attributes
        self.assertIsInstance(report, DiagnosticReport)
        self.assertTrue(report.host_info.get("os_platform"))
        self.assertTrue(report.host_info.get("python_version"))
        self.assertEqual(report.partition_health.get("boot_access"), "Read-Write")
        self.assertEqual(report.partition_health.get("data_access"), "Read-Write")
        self.assertEqual(len(report.environments_inventory), 2)

        # Check that missing ISO was detected
        missing_entries = [e for e in report.environments_inventory if not e["file_exists"]]
        self.assertEqual(len(missing_entries), 1)
        self.assertEqual(missing_entries[0]["id"], "missing_env")
        self.assertTrue(any("non_existent.iso" in issue for issue in report.issues_detected))

        # Check export formats
        json_str = report.to_json()
        parsed_json = json.loads(json_str)
        self.assertIn("host_info", parsed_json)

        md_str = report.to_markdown()
        self.assertIn("# MVentX Diagnostic & Troubleshooting Report", md_str)
        self.assertIn("Alpine Linux Virt", md_str)
        self.assertIn("Missing Distro", md_str)

    # --------------------------------------------------------------------------
    # 6. Settings Screen Diagnostic Export
    # --------------------------------------------------------------------------

    def test_settings_screen_diagnostic_export(self):
        """Verify settings screen triggers diagnostic report generation without error."""
        dev_service = DeviceService()
        dev_service.set_custom_target(self.data_dir, self.boot_dir, "Virtual Staging")
        iso_service = ISOService(dev_service)

        settings = SettingsScreen(iso_service)

        export_target = self.root_path / "exported_report.md"
        with patch.object(QMessageBox, "information"):
            with patch.object(QMessageBox, "warning"):
                with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(str(export_target), "Markdown (*.md)")):
                    settings._export_diagnostic_report("markdown")

        self.assertTrue(export_target.is_file())
        content = export_target.read_text(encoding="utf-8")
        self.assertIn("MVentX Diagnostic & Troubleshooting Report", content)


if __name__ == "__main__":
    unittest.main()
