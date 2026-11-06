"""Automated QEMU integration test verifying UEFI boot, GRUB execution, and screen rendering."""

from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.test_qemu_harness import QemuBootHarness, find_firmware, find_qemu, get_scenario_configs

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestQemuBootHarness(unittest.TestCase):
    """Integration test suite executing real UEFI boot in QEMU and verifying GRUB output."""

    @classmethod
    def setUpClass(cls):
        cls.qemu_exe = find_qemu()
        if not cls.qemu_exe:
            raise unittest.SkipTest("QEMU executable (qemu-system-x86_64) not found on system")

        cls.code_fd, cls.vars_template_fd = find_firmware(cls.qemu_exe)
        if not cls.code_fd or not cls.vars_template_fd:
            raise unittest.SkipTest("OVMF firmware files not found")

        # Verify base EFI binary exists
        cls.base_efi = REPO_ROOT / "build" / "esp" / "EFI" / "BOOT" / "BOOTX64.EFI"
        if not cls.base_efi.exists():
            cls.base_efi = REPO_ROOT / "boot" / "efi" / "BOOTX64.EFI"
        if not cls.base_efi.exists():
            raise unittest.SkipTest("BOOTX64.EFI bootloader binary not found")

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_qemu_test_"))
        self.output_dir = self.temp_dir / "screenshots"
        self.output_dir.mkdir(parents=True)

        self.harness = QemuBootHarness(
            qemu_exe=self.qemu_exe,
            code_fd=self.code_fd,
            vars_template_fd=self.vars_template_fd,
            output_dir=self.output_dir,
        )
        self.scenarios = get_scenario_configs()

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_qemu_boots_empty_fallback_menu(self):
        """Verify QEMU boots unconfigured virtual disk and renders fallback menu."""
        cfg = self.scenarios["scenario_a_empty"]
        disk_path = self.harness.stage_virtual_disk(self.temp_dir, cfg, "empty.img")
        self.assertTrue(disk_path.exists())

        res = self.harness.boot_and_verify(disk_path, "scenario_a_empty", wait_seconds=12.0)
        self.assertTrue(res["grub_menu_detected"])
        self.assertGreater(res["highlight_cyan_pixels"], 0)
        self.assertGreater(res["border_text_pixels"], 0)
        self.assertEqual(res["resolution"], "1280x800")
        self.assertTrue(Path(res["png_path"]).exists())

    def test_qemu_boots_registered_environment_menu(self):
        """Verify QEMU boots virtual disk with registered environments and renders active menu."""
        cfg = self.scenarios["scenario_c_multi"]
        disk_path = self.harness.stage_virtual_disk(self.temp_dir, cfg, "multi.img")
        self.assertTrue(disk_path.exists())

        res = self.harness.boot_and_verify(disk_path, "scenario_c_multi", wait_seconds=12.0)
        self.assertTrue(res["grub_menu_detected"])
        self.assertGreater(res["highlight_cyan_pixels"], 0)
        self.assertGreater(res["border_text_pixels"], 0)
        self.assertEqual(res["resolution"], "1280x800")
        self.assertTrue(Path(res["png_path"]).exists())


if __name__ == "__main__":
    unittest.main()

