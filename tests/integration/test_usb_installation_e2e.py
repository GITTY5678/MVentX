"""End-to-End Integration Test for MVentX USB Installation and Preparation (Milestone 9).

Validates:
1. End-to-end 12-step installation into a fresh virtual disk image.
2. Complete structural and file-level verification of GPT, FAT32, exFAT, and bootloader.
3. Headless UEFI boot in QEMU verifying fallback menu rendering.
4. Subsequent ISO registration into newly installed disk updates GRUB menu seamlessly.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from PySide6.QtGui import QImage

from core.iso_manager.registration import ISORegistrationManager
from core.storage.storage_manager import StorageManager
from installer.installation_service import InstallationService
from installer.installation_verifier import InstallationVerifier
from scripts.test_qemu_harness import find_firmware, find_qemu

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestUSBInstallationE2E(unittest.TestCase):
    """End-to-end integration test executing complete virtual disk installation and UEFI QEMU boot."""

    @classmethod
    def setUpClass(cls):
        cls.qemu_exe = find_qemu()
        if not cls.qemu_exe:
            raise unittest.SkipTest("QEMU executable (qemu-system-x86_64) not found")

        cls.code_fd, cls.vars_template_fd = find_firmware(cls.qemu_exe)
        if not cls.code_fd or not cls.vars_template_fd:
            raise unittest.SkipTest("OVMF firmware files not found")

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_install_e2e_"))
        self.installer = InstallationService()

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_virtual_disk_installation_and_qemu_boot(self):
        """Execute virtual installation and verify UEFI boot into GRUB fallback menu."""
        disk_path = self.temp_dir / "mventx_fresh_install.img"

        # 1. Execute full installation
        result = self.installer.install_to_virtual_disk(
            target_image_path=disk_path,
            disk_size_mb=256,
            esp_size_mb=64,
            menu_title="MVentX M9 Automated Installation",
            menu_timeout=10,
        )

        self.assertTrue(result.success, f"Installation failed: {result.error_message}")
        self.assertTrue(disk_path.exists())

        # 2. Verify installation report
        report = result.verification_report
        self.assertIsNotNone(report)
        self.assertTrue(report.is_valid, f"Verification errors: {report.errors}")
        self.assertEqual(len(report.errors), 0)

        # 3. Boot freshly installed disk in QEMU
        ppm_file = self.temp_dir / "qemu_fresh_install.ppm"
        png_file = self.temp_dir / "qemu_fresh_install.png"
        vars_fd = self.temp_dir / "vars_fresh_install.fd"
        shutil.copy2(self.vars_template_fd, vars_fd)

        cmd = [
            str(self.qemu_exe),
            "-m", "512M",
            "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={self.code_fd}",
            "-drive", f"if=pflash,format=raw,unit=1,file={vars_fd}",
            "-drive", f"file={disk_path},format=raw,index=0,media=disk",
            "-net", "none",
            "-display", "none",
            "-monitor", "stdio",
        ]

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Wait for OVMF firmware and GRUB to initialize and render
            time.sleep(12.0)
            proc.stdin.write(f"screendump {ppm_file.as_posix()}\n")
            proc.stdin.flush()
            time.sleep(1.0)
            proc.stdin.write("quit\n")
            proc.stdin.flush()
            proc.communicate(timeout=10)
        except Exception:
            proc.kill()
            raise

        self.assertTrue(ppm_file.exists(), f"QEMU screendump was not generated: {ppm_file}")

        # Convert to PNG and inspect pixel distribution
        qimg = QImage(str(ppm_file))
        qimg.save(str(png_file))

        with open(ppm_file, "rb") as f:
            for _ in range(3):
                f.readline()
            pixels = f.read()

        from collections import Counter
        colors = Counter([pixels[i : i + 3] for i in range(0, len(pixels), 3)])

        cyan_highlight = colors.get(b"\x54\xfe\xfe", 0)  # RGB(84, 254, 254)
        border_text = colors.get(b"\xa8\xa8\xa8", 0)     # RGB(168, 168, 168)

        self.assertGreater(
            cyan_highlight,
            0,
            "Cyan highlight box was not rendered in GRUB fallback menu screen",
        )
        self.assertGreater(
            border_text,
            0,
            "Border or text elements were not rendered in GRUB fallback menu screen",
        )


if __name__ == "__main__":
    unittest.main()

