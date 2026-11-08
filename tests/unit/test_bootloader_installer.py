"""Unit tests for BootloaderInstaller (Milestone 18)."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from installer.bootloader_installer import BootloaderInstaller, BootloaderInstallError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestBootloaderInstaller(unittest.TestCase):
    """Test suite for bootloader deployment, module staging, and manifest copying."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_boot_inst_"))
        self.installer = BootloaderInstaller(self.temp_dir, assets_root=REPO_ROOT)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_deploy_directories(self) -> None:
        """Verify creation of EFI, GRUB modules, and fonts directories."""
        self.installer.deploy_directories()
        self.assertTrue((self.temp_dir / "EFI" / "BOOT").is_dir())
        self.assertTrue((self.temp_dir / "boot" / "grub" / "x86_64-efi").is_dir())
        self.assertTrue((self.temp_dir / "boot" / "grub" / "fonts").is_dir())

    def test_deploy_efi_binaries(self) -> None:
        """Verify copying of BOOTX64.EFI to /EFI/BOOT/."""
        self.installer.deploy_directories()
        target = self.installer.deploy_efi_binaries()
        self.assertTrue(target.is_file())
        self.assertEqual(target.name, "BOOTX64.EFI")
        self.assertGreater(target.stat().st_size, 100000)

    def test_deploy_grub_configuration(self) -> None:
        """Verify deployment of primary static grub.cfg."""
        self.installer.deploy_directories()
        target = self.installer.deploy_grub_configuration()
        self.assertTrue(target.is_file())
        content = target.read_text(encoding="utf-8")
        self.assertIn("insmod reboot", content)
        self.assertIn("insmod halt", content)
        self.assertIn("insmod minicmd", content)
        self.assertIn("submenu \"MVentX System Utilities\"", content)

    def test_deploy_modules_copies_mod_and_lst(self) -> None:
        """Verify that deploy_modules copies both .mod binaries and .lst manifests."""
        self.installer.deploy_directories()
        mod_count = self.installer.deploy_modules()
        self.assertGreater(mod_count, 250)

        target_dir = self.temp_dir / "boot" / "grub" / "x86_64-efi"
        # Verify required .mod files
        self.assertTrue((target_dir / "reboot.mod").is_file())
        self.assertTrue((target_dir / "halt.mod").is_file())
        self.assertTrue((target_dir / "acpi.mod").is_file())
        self.assertTrue((target_dir / "minicmd.mod").is_file())
        self.assertTrue((target_dir / "efifwsetup.mod").is_file())
        self.assertTrue((target_dir / "exfat.mod").is_file())

        # Verify critical .lst manifests that enable autoloading & dependency resolution
        self.assertTrue((target_dir / "command.lst").is_file(), "command.lst must be deployed for command autoloading")
        self.assertTrue((target_dir / "moddep.lst").is_file(), "moddep.lst must be deployed for dependency resolution")
        self.assertTrue((target_dir / "fs.lst").is_file())
        self.assertTrue((target_dir / "partmap.lst").is_file())

    def test_deploy_initial_menu(self) -> None:
        """Verify deployment of initial mventx_menu.cfg."""
        self.installer.deploy_directories()
        target = self.installer.deploy_initial_menu(title="MVentX Test Menu", timeout=7)
        self.assertTrue(target.is_file())
        content = target.read_text(encoding="utf-8")
        self.assertIn('set mventx_menu_title="MVentX Test Menu"', content)
        self.assertIn("set timeout=7", content)
        self.assertIn('menuentry "No environments registered or enabled"', content)
        self.assertIn('submenu "MVentX System Utilities"', content)
        self.assertIn('menuentry "Reboot System"', content)
        self.assertIn('menuentry "Halt / Power Off System"', content)

    def test_deploy_all_validates_capabilities(self) -> None:
        """Verify complete end-to-end deployment and capability report generation."""
        report = self.installer.deploy_all(title="MVentX Production System", timeout=10)
        self.assertIsNotNone(report)
        self.assertTrue(report.is_valid)
        self.assertEqual(report.architecture, "x86_64 (PE32+)")
        self.assertIn("reboot", report.supported_utilities)
        self.assertIn("halt", report.supported_utilities)
        self.assertIn("exit", report.supported_utilities)
        self.assertIn("fwsetup", report.supported_utilities)
        self.assertEqual(len(report.missing_required_modules), 0)


if __name__ == "__main__":
    unittest.main()

