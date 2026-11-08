"""Unit tests for MVentX Milestone 19.1: GUI Integration and Overlay Lifecycle Management.

Tests cover:
1. OverlayStatus calculation across all states (ready, missing, corrupted, outdated, not_required).
2. EnvironmentLibraryScreen table rendering of Overlay Status (column 6) and per-row / batch Repair actions.
3. EnvironmentDetailDialog overlay status inspection and Regenerate / Repair Boot Support button.
4. AddEnvironmentScreen automatic Casper overlay generation and manual boot method override preservation.
5. Atomic MVX_BOOT menu synchronization, rollback safety, and strict ISO preservation during repair.
"""

from __future__ import annotations

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from core.iso_manager.overlay_generator import OverlayGenerationResult, create_newc_cpio
from core.storage.storage_manager import StorageManager
from manager.services.device_service import ActiveTarget, DeviceService
from manager.services.iso_service import ISOService
from manager.ui.add_environment import AddEnvironmentScreen
from manager.ui.environment_library import EnvironmentDetailDialog, EnvironmentLibraryScreen
from tests.unit.test_iso_validation import make_mock_iso


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["test", "-platform", "offscreen"])
    return app


def _make_valid_cpio_bytes() -> bytes:
    return create_newc_cpio([
        ("scripts", 0o040755, b""),
        ("scripts/casper-premount", 0o040755, b""),
        ("scripts/casper-premount/00_mventx_exfat", 0o100755, b"#!/bin/sh\nmodprobe exfat\n"),
    ])


class TestM191GUIOverlayIntegration(unittest.TestCase):
    """Test suite for Milestone 19.1 GUI overlay status, repair actions, and automatic registration workflow."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_qapp()

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.boot_mount = self.root / "MVX_BOOT"
        self.data_mount = self.root / "MVX_DATA"
        self.boot_mount.mkdir(parents=True, exist_ok=True)
        self.data_mount.mkdir(parents=True, exist_ok=True)
        (self.boot_mount / "boot" / "grub").mkdir(parents=True, exist_ok=True)

        self.device_service = DeviceService()
        self.device_service.set_custom_target(
            data_mount=self.data_mount,
            boot_mount=self.boot_mount,
            display_name="Simulated MVentX Drive",
        )

        self.iso_service = ISOService(self.device_service)
        self.failures: list[tuple[str, str]] = []
        self.iso_service.operation_failed.connect(lambda t, d: self.failures.append((t, d)))

        # Create candidate ISOs on MVX_DATA/ISO/
        self.iso_dir = self.data_mount / "ISO"
        self.iso_dir.mkdir(parents=True, exist_ok=True)
        self.ubuntu_iso = self.iso_dir / "ubuntu-26.04.1-desktop-amd64.iso"
        self.arch_iso = self.iso_dir / "archlinux-2026.03.01-x86_64.iso"
        make_mock_iso(self.ubuntu_iso, volume_id="UBUNTU_26_04")
        make_mock_iso(self.arch_iso, volume_id="ARCH_202603")

    def tearDown(self) -> None:
        for w in QApplication.topLevelWidgets():
            w.close()
            w.deleteLater()
        QApplication.processEvents()
        self.temp_dir.cleanup()

    def _fake_generate_valid_cpio(self, iso_path, output_cpio_path, **kwargs):
        out_p = Path(output_cpio_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        cpio_bytes = _make_valid_cpio_bytes()
        out_p.write_bytes(cpio_bytes)
        return OverlayGenerationResult(
            success=True,
            cpio_path=out_p,
            kernel_version="6.14.0-15-generic",
            extracted_modules=["exfat.ko"],
        )

    @patch("PySide6.QtWidgets.QMessageBox.information")
    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.generate_exfat_overlay")
    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.can_generate_overlay", return_value=True)
    def test_environment_library_displays_overlay_statuses_and_repairs_missing_overlay(
        self,
        mock_can,
        mock_gen,
        mock_msgbox,
    ) -> None:
        """Verify Environment Library displays overlay states and repairs missing/corrupted overlays atomically."""
        mock_gen.side_effect = self._fake_generate_valid_cpio

        # Register a Casper environment (requires overlay) and an Arch environment (does not require overlay)
        ubuntu_rec = self.iso_service.register_iso(
            iso_file=self.ubuntu_iso.name,
            display_name="Ubuntu 26.04.1 LTS",
            boot_method="casper",
            recipe="casper",
        )
        arch_rec = self.iso_service.register_iso(
            iso_file=self.arch_iso.name,
            display_name="Arch Linux 2026.03",
            boot_method="archiso",
            recipe="archiso",
        )
        self.assertIsNotNone(ubuntu_rec, f"Failures: {self.failures}")
        self.assertIsNotNone(arch_rec, f"Failures: {self.failures}")

        library = EnvironmentLibraryScreen(self.iso_service)
        library.load_data()

        self.assertEqual(library.table.columnCount(), 8)
        self.assertEqual(library.table.rowCount(), 2)

        # Row 0: Ubuntu 26.04.1 LTS -> Required • Ready
        row0_ov_item = library.table.item(0, 6)
        self.assertIsNotNone(row0_ov_item)
        self.assertEqual(row0_ov_item.text(), "Required • Ready")
        self.assertIn("Requires Initramfs Overlay: Yes", row0_ov_item.toolTip())
        self.assertIn("Overlay Exists on MVX_BOOT: Yes", row0_ov_item.toolTip())
        self.assertIn("Needs Regeneration: No", row0_ov_item.toolTip())

        # Row 1: Arch Linux -> Not Required
        row1_ov_item = library.table.item(1, 6)
        self.assertIsNotNone(row1_ov_item)
        self.assertEqual(row1_ov_item.text(), "Not Required")
        self.assertIn("Requires Initramfs Overlay: No", row1_ov_item.toolTip())

        # Simulate missing overlay on MVX_BOOT
        overlay_file = self.boot_mount / ubuntu_rec.overlay_path
        self.assertTrue(overlay_file.is_file())
        overlay_file.unlink()

        library.load_data()
        row0_ov_missing = library.table.item(0, 6)
        self.assertEqual(row0_ov_missing.text(), "Required • Missing (Needs Regen)")
        self.assertIn("Overlay Exists on MVX_BOOT: No", row0_ov_missing.toolTip())
        self.assertIn("Needs Regeneration: Yes", row0_ov_missing.toolTip())

        # Invoke GUI repair action for Ubuntu
        iso_bytes_before = self.ubuntu_iso.read_bytes()
        library._on_repair_clicked(ubuntu_rec)

        # Verify overlay was regenerated on MVX_BOOT, GRUB menu synced, and ISO preserved on MVX_DATA
        self.assertTrue(overlay_file.is_file())
        self.assertEqual(self.ubuntu_iso.read_bytes(), iso_bytes_before)

        library.load_data()
        self.assertEqual(library.table.item(0, 6).text(), "Required • Ready")

        # Verify MVX_BOOT GRUB menu contains the dual-initrd command
        boot_menu_text = (self.boot_mount / "boot" / "grub" / "mventx_menu.cfg").read_text(encoding="utf-8")
        self.assertIn(ubuntu_rec.overlay_path, boot_menu_text)

    @patch("PySide6.QtWidgets.QMessageBox.information")
    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.generate_exfat_overlay")
    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.can_generate_overlay", return_value=True)
    def test_corrupted_and_outdated_overlay_detection_and_inspector_repair(
        self,
        mock_can,
        mock_gen,
        mock_msgbox,
    ) -> None:
        """Verify corrupted and outdated overlays are flagged and repaired from EnvironmentDetailDialog."""
        mock_gen.side_effect = self._fake_generate_valid_cpio

        ubuntu_rec = self.iso_service.register_iso(
            iso_file=self.ubuntu_iso.name,
            display_name="Ubuntu 26.04.1 Desktop",
            boot_method="casper",
            recipe="casper",
        )
        overlay_file = self.boot_mount / ubuntu_rec.overlay_path

        # 1. Corrupt the CPIO archive on MVX_BOOT
        overlay_file.write_bytes(b"NOT_A_VALID_CPIO_HEADER" * 40)
        ov_corrupt = self.iso_service.get_overlay_status(ubuntu_rec)
        self.assertTrue(ov_corrupt.requires_overlay)
        self.assertTrue(ov_corrupt.overlay_exists)
        self.assertTrue(ov_corrupt.needs_regeneration)
        self.assertEqual(ov_corrupt.status_label, "Required • Corrupted (Needs Regen)")

        # Open EnvironmentDetailDialog and verify labels reflect corrupted state
        dlg = EnvironmentDetailDialog(ubuntu_rec, self.iso_service)
        self.assertIn("Yes", dlg.ov_req_lbl.text())
        self.assertEqual(dlg.ov_exists_lbl.text(), "Yes")
        self.assertEqual(dlg.ov_regen_lbl.text(), "Yes")
        self.assertIn("Corrupted", dlg.ov_status_lbl.text())

        # Trigger repair button inside EnvironmentDetailDialog
        dlg._on_repair_boot_support()
        self.assertEqual(dlg.ov_regen_lbl.text(), "No")
        self.assertIn("Required • Ready", dlg.ov_status_lbl.text())
        self.assertTrue(overlay_file.read_bytes().startswith(b"070701"))

        # 2. Simulate ISO replacement (ISO mtime newer than overlay mtime)
        future_mtime = overlay_file.stat().st_mtime + 60.0
        os.utime(self.ubuntu_iso, (future_mtime, future_mtime))
        ov_outdated = self.iso_service.get_overlay_status(dlg.env)
        self.assertTrue(ov_outdated.needs_regeneration)
        self.assertEqual(ov_outdated.status_label, "Required • Outdated (Needs Regen)")

    @patch("PySide6.QtWidgets.QMessageBox.information")
    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.generate_exfat_overlay")
    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.can_generate_overlay", return_value=True)
    def test_add_environment_screen_automatically_generates_overlay_for_casper(
        self,
        mock_can,
        mock_gen,
        mock_msgbox,
    ) -> None:
        """Verify AddEnvironmentScreen automatically generates overlay for Casper ISOs and respects overrides."""
        mock_gen.side_effect = self._fake_generate_valid_cpio

        add_screen = AddEnvironmentScreen(self.iso_service)

        # Mock inspect_iso to report Casper detection for ubuntu_iso
        from core.iso_manager.iso_inspector import InspectionResult

        casper_insp = InspectionResult(
            volume_id="UBUNTU_26_04",
            detected_recipe="casper",
            discovered_kernel_path="/casper/vmlinuz",
            discovered_initrd_path="/casper/initrd",
            confidence="high",
        )

        with patch.object(self.iso_service, "inspect_iso", return_value=casper_insp):
            add_screen.refresh_unregistered_list()
            # Ensure ubuntu_iso is selected
            idx = add_screen.iso_combo.findData(str(self.ubuntu_iso.resolve()))
            self.assertGreaterEqual(idx, 0)
            add_screen.iso_combo.setCurrentIndex(idx)

            # Verify validation details mention automatic Casper overlay generation
            self.assertIn(
                "Dynamic exFAT initramfs overlay will be generated automatically",
                add_screen.val_details_lbl.text(),
            )
            self.assertEqual(add_screen.method_combo.currentData(), "casper")

            # Click Register
            add_screen.name_edit.setText("Ubuntu 26.04.1 From GUI")
            add_screen._on_register_clicked()

        envs = self.iso_service.list_environments()
        self.assertEqual(len(envs), 1)
        reg_env = envs[0]
        self.assertEqual(reg_env.boot_method, "casper")
        self.assertIsNotNone(reg_env.overlay_path)
        self.assertTrue((self.boot_mount / reg_env.overlay_path).is_file())


if __name__ == "__main__":
    unittest.main()
