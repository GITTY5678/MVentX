"""Unit tests for USB Installer GUI Component (Milestone 9)."""

from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication

from manager.services.device_service import DeviceService
from manager.ui.usb_installer import InstallationWorker, USBInstallerScreen

# Ensure single QApplication instance
APP = QApplication.instance() or QApplication([])


class TestUSBInstallerGUI(unittest.TestCase):
    """Test suite verifying USB Installer screen behavior, controls, and worker execution."""

    def setUp(self):
        self.device_service = DeviceService()
        self.screen = USBInstallerScreen(self.device_service)
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_test_gui_install_"))

    def tearDown(self):
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_screen_initial_state(self):
        """Verify initial state enforces safety controls."""
        # Virtual disk radio must be checked by default
        self.assertTrue(self.screen.radio_virtual.isChecked())
        # Physical radio must be disabled by default
        self.assertFalse(self.screen.radio_physical.isEnabled())
        # Install button must be disabled until confirmation checkbox is checked
        self.assertFalse(self.screen.btn_install.isEnabled())
        self.assertFalse(self.screen.chk_confirm.isChecked())

    def test_confirmation_toggle_enables_install_button(self):
        """Verify checking the safety confirmation box enables the install button."""
        self.screen.chk_confirm.setChecked(True)
        self.assertTrue(self.screen.btn_install.isEnabled())

        self.screen.chk_confirm.setChecked(False)
        self.assertFalse(self.screen.btn_install.isEnabled())

    def test_layout_preview_calculation(self):
        """Verify selecting 512 MiB total and 128 MiB ESP updates preview text and bar."""
        # Select 512 MiB
        idx_512 = self.screen.disk_size_combo.findData(512)
        self.screen.disk_size_combo.setCurrentIndex(idx_512)

        # Select 128 MiB ESP
        idx_128 = self.screen.esp_size_combo.findData(128)
        self.screen.esp_size_combo.setCurrentIndex(idx_128)

        self.assertIn("128 MiB", self.screen.bar_esp.text())
        self.assertIn("383.0 MiB", self.screen.bar_data.text())
        self.assertIn("MVX_BOOT", self.screen.preview_details.text())
        self.assertIn("MVX_DATA", self.screen.preview_details.text())

    def test_worker_cancellation(self):
        """Verify worker honours cancel() signal."""
        worker = InstallationWorker(
            target_path=self.temp_dir / "cancelled.img",
            disk_size_mb=256,
            esp_size_mb=64,
            menu_title="Cancelled",
            menu_timeout=10,
        )
        worker.cancel()
        self.assertTrue(worker._cancelled)


if __name__ == "__main__":
    unittest.main()
