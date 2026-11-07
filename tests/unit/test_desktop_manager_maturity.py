"""Unit tests for Milestone 14 Desktop Manager Maturity features.

Tests cover:
- Dashboard metric calculation, free space, validation badges, read/write flags, action shortcuts
- Background workers (ISOCopyWorker, ChecksumWorker, SyncBootConfigWorker) and cancellation
- Environment Library sorting, searching, batch operations, and inspector
- Safe ejection and device disconnection / reconnection handling
"""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure headless Qt offscreen platform
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from core.host.device_classification import MVentXClassification, MVentXStatus
from core.iso_manager import EnvironmentRecord
from manager.main import create_app
from manager.services.device_service import ActiveTarget, DeviceService
from manager.services.iso_service import ISOService
from manager.services.workers import ChecksumWorker, ISOCopyWorker, SyncBootConfigWorker
from tests.unit.test_iso_validation import make_mock_iso


class TestDesktopManagerMaturity(unittest.TestCase):
    """Test suite covering Milestone 14 Desktop Manager enhancements."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])

    def setUp(self):
        self.info_patcher = patch.object(QMessageBox, "information", return_value=QMessageBox.Ok)
        self.warn_patcher = patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok)
        self.crit_patcher = patch.object(QMessageBox, "critical", return_value=QMessageBox.Ok)
        self.quest_patcher = patch.object(QMessageBox, "question", return_value=QMessageBox.Yes)
        self.info_patcher.start()
        self.warn_patcher.start()
        self.crit_patcher.start()
        self.quest_patcher.start()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        self.data_dir = self.root_path / "MVX_DATA"
        self.boot_dir = self.root_path / "MVX_BOOT"
        self.data_dir.mkdir()
        self.boot_dir.mkdir()
        (self.boot_dir / "boot" / "grub").mkdir(parents=True)

        # Stage mock ISOs
        self.iso_dir = self.data_dir / "ISO"
        self.iso_dir.mkdir(parents=True)
        self.iso_ubuntu = self.iso_dir / "ubuntu.iso"
        self.iso_fedora = self.iso_dir / "fedora.iso"
        self.iso_arch = self.iso_dir / "archlinux.iso"

        make_mock_iso(self.iso_ubuntu, volume_id="UBUNTU")
        make_mock_iso(self.iso_fedora, volume_id="FEDORA")
        make_mock_iso(self.iso_arch, volume_id="ARCH")

        # Pad to specific sizes: Fedora (80KB) > Ubuntu (50KB) > Arch (35KB)
        with open(self.iso_ubuntu, "ab") as f:
            f.write(b"\x00" * 15000)
        with open(self.iso_fedora, "ab") as f:
            f.write(b"\x00" * 45000)

        _, self.window = create_app(data_path=self.data_dir, boot_path=self.boot_dir)
        self.device_service = self.window.device_service
        self.iso_service = self.window.iso_service

    def tearDown(self):
        self.info_patcher.stop()
        self.warn_patcher.stop()
        self.crit_patcher.stop()
        self.quest_patcher.stop()
        self.window.close()
        self.temp_dir.cleanup()

    # --------------------------------------------------------------------------
    # 1. Dashboard Metrics, Badges, and Action Shortcuts
    # --------------------------------------------------------------------------

    def test_dashboard_metrics_and_badges(self):
        """Verify dashboard displays correct metrics, free space, validation badge, and RW health."""
        # Register Ubuntu and Fedora
        self.iso_service.register_iso(self.iso_ubuntu.name, "Ubuntu 24.04")
        self.iso_service.register_iso(self.iso_fedora.name, "Fedora 40")

        dashboard = self.window.dashboard_screen
        dashboard.refresh_data()

        # Check badge
        self.assertIn("VALID", dashboard.badge_status.text())

        # Check registered envs card (2 registered, 1 unregistered archlinux.iso = 3 total)
        self.assertEqual(dashboard.card_envs.value_lbl.text(), "2")
        self.assertIn("3 total ISOs found", dashboard.card_envs.subtitle_lbl.text())

        # Check capacity card
        self.assertIn("Free:", dashboard.card_capacity.subtitle_lbl.text())

        # Check action shortcuts enabled
        self.assertTrue(dashboard.btn_open_explorer.isEnabled())
        self.assertTrue(dashboard.btn_copy_iso.isEnabled())
        self.assertTrue(dashboard.btn_eject_usb.isEnabled())

        # Check partition RW status
        self.assertIn("Read-Write", dashboard.p1_status.text())
        self.assertIn("Read-Write", dashboard.p2_status.text())

    def test_dashboard_status_badges_across_classifications(self):
        """Verify badge changes according to device status."""
        dashboard = self.window.dashboard_screen
        target = self.device_service.active_target

        # Corrupted status
        target.classification = MVentXClassification(
            status=MVentXStatus.CORRUPTED_INSTALLATION,
            device_id="test",
        )
        dashboard._update_validation_badge(target)
        self.assertEqual(dashboard.badge_status.text(), "CORRUPTED")

        # Incomplete / Uninitialized status
        target.classification = MVentXClassification(
            status=MVentXStatus.INCOMPLETE_INSTALLATION,
            device_id="test",
        )
        dashboard._update_validation_badge(target)
        self.assertEqual(dashboard.badge_status.text(), "INCOMPLETE")

    # --------------------------------------------------------------------------
    # 2. Background Workers and Cancellation
    # --------------------------------------------------------------------------

    def test_iso_copy_worker_success(self):
        """Verify ISOCopyWorker copies file and emits progress and finished signals."""
        src_file = self.root_path / "test_src.iso"
        src_file.write_bytes(b"X" * (512 * 1024))  # 512 KiB

        dest_dir = self.root_path / "copy_dest"
        worker = ISOCopyWorker(
            source_path=src_file,
            destination_dir=dest_dir,
            dest_filename="copied.iso",
            chunk_size=64 * 1024,
        )

        progress_calls = []
        worker.progress.connect(lambda c, t, p, s: progress_calls.append(p))

        finished_paths = []
        worker.finished_success.connect(lambda p: finished_paths.append(p))

        worker.start()
        worker.wait(5000)
        self.app.processEvents()

        self.assertTrue((dest_dir / "copied.iso").is_file())
        self.assertEqual(len(finished_paths), 1)
        self.assertTrue(len(progress_calls) > 0)
        self.assertEqual(progress_calls[-1], 100)

    def test_iso_copy_worker_cancellation(self):
        """Verify cancelling ISOCopyWorker halts copy and deletes partial file."""
        src_file = self.root_path / "test_big.iso"
        src_file.write_bytes(b"A" * (2 * 1024 * 1024))  # 2 MiB

        dest_dir = self.root_path / "cancel_dest"
        worker = ISOCopyWorker(
            source_path=src_file,
            destination_dir=dest_dir,
            dest_filename="partial.iso",
            chunk_size=16 * 1024,  # Small chunks to catch cancel
        )

        cancelled_called = []
        worker.cancelled.connect(lambda: cancelled_called.append(True))

        worker.cancel()
        worker.start()
        worker.wait(5000)
        self.app.processEvents()

        self.assertTrue(len(cancelled_called) > 0)
        self.assertFalse((dest_dir / "partial.iso").exists(), "Partial file must be unlinked upon cancellation")

    def test_checksum_worker(self):
        """Verify ChecksumWorker calculates correct SHA-256 in background thread."""
        test_file = self.root_path / "checksum_test.bin"
        test_content = b"MVentX SHA-256 test content for Milestone 14"
        test_file.write_bytes(test_content)

        import hashlib
        expected_hash = hashlib.sha256(test_content).hexdigest()

        worker = ChecksumWorker(test_file, chunk_size=10)
        results = []
        worker.finished_success.connect(lambda h: results.append(h))

        worker.start()
        worker.wait(5000)
        self.app.processEvents()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], expected_hash)

    def test_sync_boot_config_worker(self):
        """Verify SyncBootConfigWorker executes sync_boot_menu in background thread."""
        storage = self.device_service.get_storage_manager()
        worker = SyncBootConfigWorker(storage)

        results = []
        worker.finished_success.connect(lambda msg: results.append(msg))

        worker.start()
        worker.wait(5000)
        self.app.processEvents()

        self.assertEqual(len(results), 1)
        self.assertIn("Boot menu synced", results[0])

    # --------------------------------------------------------------------------
    # 3. Environment Library Sorting, Filtering, and Batch Actions
    # --------------------------------------------------------------------------

    def test_library_sorting_and_filtering(self):
        """Verify library table filters and sorts by name, size, order, and method."""
        self.iso_service.register_iso(self.iso_ubuntu.name, "Ubuntu", boot_method="casper")
        self.iso_service.register_iso(self.iso_fedora.name, "Fedora", boot_method="dracut")
        self.iso_service.register_iso(self.iso_arch.name, "Arch", boot_method="archiso")

        library = self.window.library_screen
        library.load_data()
        self.assertEqual(library.table.rowCount(), 3)

        # 1. Test Text Filter
        library.search_input.setText("fedora")
        self.assertEqual(library.table.rowCount(), 1)
        self.assertEqual(library.table.item(0, 1).text(), "Fedora")

        # Clear filter
        library.search_input.setText("")
        self.assertEqual(library.table.rowCount(), 3)

        # 2. Sort by Name (A → Z)
        library.sort_combo.setCurrentIndex(1)
        self.assertEqual(library.table.item(0, 1).text(), "Arch")
        self.assertEqual(library.table.item(1, 1).text(), "Fedora")
        self.assertEqual(library.table.item(2, 1).text(), "Ubuntu")

        # 3. Sort by Name (Z → A)
        library.sort_combo.setCurrentIndex(2)
        self.assertEqual(library.table.item(0, 1).text(), "Ubuntu")
        self.assertEqual(library.table.item(1, 1).text(), "Fedora")
        self.assertEqual(library.table.item(2, 1).text(), "Arch")

        # 4. Sort by Size (Largest First) - Fedora (50KB) > Ubuntu (20KB) > Arch (10KB)
        library.sort_combo.setCurrentIndex(3)
        self.assertEqual(library.table.item(0, 1).text(), "Fedora")
        self.assertEqual(library.table.item(1, 1).text(), "Ubuntu")
        self.assertEqual(library.table.item(2, 1).text(), "Arch")

    def test_library_batch_operations(self):
        """Verify batch enable, disable, and remove operations."""
        rec1 = self.iso_service.register_iso(self.iso_ubuntu.name, "Ubuntu")
        rec2 = self.iso_service.register_iso(self.iso_fedora.name, "Fedora")

        library = self.window.library_screen
        library.load_data()

        # Select all rows
        library.table.selectAll()

        # Batch Disable
        library._execute_batch_toggle(False)
        envs = self.iso_service.list_environments()
        self.assertFalse(envs[0].enabled)
        self.assertFalse(envs[1].enabled)

        # Batch Enable
        library.table.selectAll()
        library._execute_batch_toggle(True)
        envs = self.iso_service.list_environments()
        self.assertTrue(envs[0].enabled)
        self.assertTrue(envs[1].enabled)

        # Batch Remove
        library.table.selectAll()
        library._execute_batch_remove()
        self.assertEqual(len(self.iso_service.list_environments()), 0)
        # Verify ISO files were preserved
        self.assertTrue(self.iso_ubuntu.is_file())
        self.assertTrue(self.iso_fedora.is_file())

    # --------------------------------------------------------------------------
    # 4. Hotplug & Safe Ejection
    # --------------------------------------------------------------------------

    def test_device_arrival_and_departure_signals(self):
        """Verify device connected/disconnected signals update UI without crashing."""
        # Trigger arrival
        self.device_service.device_connected.emit("SanDisk Cruzer 32GB")
        self.assertIn("SanDisk Cruzer", self.window.statusBar().currentMessage())

        # Trigger departure
        self.device_service.device_disconnected.emit("SanDisk Cruzer 32GB")
        self.assertIn("disconnected", self.window.statusBar().currentMessage())

    def test_safe_ejection_trigger_and_state_transition(self):
        """Verify safe ejection resets active target and updates status bar."""
        self.assertTrue(self.device_service.has_active_target())

        # Eject active device
        self.device_service.eject_active_device()

        # Target should be reset
        self.assertFalse(self.device_service.has_active_target())
        self.assertIn("No MVentX Device", self.window.dashboard_screen.device_title.text())
        self.assertEqual(self.window.dashboard_screen.badge_status.text(), "DISCONNECTED")


if __name__ == "__main__":
    unittest.main()
