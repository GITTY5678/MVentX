"""Integration test verifying end-to-end Linux ISO loopback boot in QEMU (Milestone 7).

Tests full execution hierarchy:
- Level 1: Dynamic GRUB menu script generation with loopback commands.
- Level 2: Kernel loading into memory and early execution in QEMU.
- Level 3: Initramfs loading and early system daemon startup.
- Level 4: User space interactive environment reached.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from core.boot_config.generator import GrubMenuGenerator
from core.iso_manager.registration import ISORegistrationManager
from core.storage.storage_manager import StorageManager
from scripts.test_qemu_harness import find_firmware, find_qemu
from scripts.windows.make_dual_partition_disk import build_dual_partition_gpt_disk

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestLinuxLoopbackBootIntegration(unittest.TestCase):
    """End-to-end integration test booting a real Linux ISO in QEMU via GRUB loopback."""

    @classmethod
    def setUpClass(cls):
        cls.qemu_exe = find_qemu()
        if not cls.qemu_exe:
            raise unittest.SkipTest("QEMU executable (qemu-system-x86_64) not found")

        cls.code_fd, cls.vars_template_fd = find_firmware(cls.qemu_exe)
        if not cls.code_fd or not cls.vars_template_fd:
            raise unittest.SkipTest("OVMF firmware files not found")

        # Test ISO path
        cls.test_iso = REPO_ROOT / "build" / "scratch" / "ubuntu_focal_mini.iso"
        if not cls.test_iso.is_file():
            raise unittest.SkipTest(f"Test ISO not found at {cls.test_iso}")

        cls.base_efi = REPO_ROOT / "boot" / "efi" / "BOOTX64.EFI"
        cls.base_grub_cfg = REPO_ROOT / "boot" / "grub" / "grub.cfg"
        cls.grub_modules_dir = REPO_ROOT / "boot" / "grub" / "x86_64-efi"

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_linux_boot_test_"))
        self.boot_mount = self.temp_dir / "boot"
        self.data_mount = self.temp_dir / "data"
        self.boot_mount.mkdir(parents=True)
        self.data_mount.mkdir(parents=True)

        # Stage directory structure
        (self.boot_mount / "EFI" / "BOOT").mkdir(parents=True)
        (self.boot_mount / "boot" / "grub" / "fonts").mkdir(parents=True)
        (self.boot_mount / "boot" / "grub" / "x86_64-efi").mkdir(parents=True)

        (self.data_mount / "ISO").mkdir(parents=True)
        (self.data_mount / "config").mkdir(parents=True)
        (self.data_mount / "metadata").mkdir(parents=True)
        (self.data_mount / "backups").mkdir(parents=True)

        # Copy bootloader and modules
        shutil.copy2(self.base_efi, self.boot_mount / "EFI" / "BOOT" / "BOOTX64.EFI")
        shutil.copy2(self.base_grub_cfg, self.boot_mount / "boot" / "grub" / "grub.cfg")

        font_src = REPO_ROOT / "build" / "esp" / "boot" / "grub" / "fonts" / "ascii.pf2"
        if font_src.exists():
            shutil.copy2(font_src, self.boot_mount / "boot" / "grub" / "fonts" / "ascii.pf2")

        if self.grub_modules_dir.is_dir():
            for f in self.grub_modules_dir.glob("*"):
                shutil.copy2(f, self.boot_mount / "boot" / "grub" / "x86_64-efi" / f.name)

        # Copy real ISO into data partition
        self.staged_iso = self.data_mount / "ISO" / "ubuntu_focal_mini.iso"
        shutil.copy2(self.test_iso, self.staged_iso)

        # Initialize storage and registration managers
        self.storage_manager = StorageManager(
            data_mount=self.data_mount,
            boot_mount=self.boot_mount,
        )
        self.storage_manager.initialize_data_partition_structure()
        self.reg_manager = ISORegistrationManager(self.storage_manager)

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_level1_through_level4_linux_boot_execution(self):
        """Execute complete pipeline from registration through Level 4 live OS environment."""
        # 1. Register Linux ISO
        record = self.reg_manager.register_iso(
            iso_path=self.staged_iso,
            display_name="Ubuntu Focal Mini Installer",
            boot_method="linux_generic_loopback",
            custom_args="console=tty0 console=ttyS0,115200n8 nokaslr earlyprintk=serial,ttyS0,115200 debug panic=10 --- quiet",
            recipe="generic_kernel",
            kernel_path="/linux",
            initrd_path="/initrd.gz",
        )
        self.assertEqual(record.display_name, "Ubuntu Focal Mini Installer")

        # Level 1 Verification: Menu Generation
        menu_path = self.boot_mount / "boot" / "grub" / "mventx_menu.cfg"
        self.assertTrue(menu_path.is_file())
        menu_content = menu_path.read_text(encoding="utf-8")
        self.assertIn("Ubuntu Focal Mini Installer", menu_content)
        self.assertIn("loopback loop0", menu_content)
        self.assertIn("linux (loop0)/linux", menu_content)
        self.assertIn("initrd (loop0)/initrd.gz", menu_content)
        self.assertIn("boot", menu_content)

        # 2. Build 256MB GPT Dual-Partition Virtual Disk
        disk_img = self.temp_dir / "test_linux_loopback.img"
        build_dual_partition_gpt_disk(
            esp_source_dir=self.boot_mount,
            output_path=disk_img,
            disk_size_mb=256,
            esp_size_mb=64,
            data_source_dir=self.data_mount,
        )
        self.assertTrue(disk_img.exists())

        # 3. Launch QEMU with serial output redirection
        serial_log = self.temp_dir / "serial.log"
        vars_fd = self.temp_dir / "mventx_vars.fd"
        shutil.copy2(self.vars_template_fd, vars_fd)

        cmd = [
            str(self.qemu_exe),
            "-m", "1024M",
            "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={self.code_fd}",
            "-drive", f"if=pflash,format=raw,unit=1,file={vars_fd}",
            "-drive", f"file={disk_img},format=raw,index=0,media=disk",
            "-serial", f"file:{serial_log}",
            "-display", "none",
        ]

        proc = subprocess.Popen(cmd)
        try:
            # Allow kernel and initramfs to complete boot
            proc.wait(timeout=75)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        self.assertTrue(serial_log.exists(), "Serial log was not generated")
        serial_content = serial_log.read_text(encoding="utf-8", errors="ignore")

        # Level 2 Verification: Kernel Loading Tested (early kernel execution verified)
        self.assertTrue(
            "KASLR" in serial_content or "Linux version" in serial_content,
            "Early kernel execution was not detected in serial log",
        )

        # Level 3 Verification: Initramfs Loading Tested (system daemons started)
        self.assertIn("Starting system log daemon", serial_content)

        # Level 4 Verification: Live Operating System Interactive Environment Reached
        self.assertIn("Select a language", serial_content)
        self.assertIn("Language:", serial_content)


if __name__ == "__main__":
    unittest.main()
