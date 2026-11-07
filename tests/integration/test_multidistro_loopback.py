"""Integration test verifying multi-distribution Linux ISO loopback boot in QEMU (Milestone 8).

Validates:
1. Debian live-boot distribution (Clonezilla Live Debian):
   - Level 1: Dynamic GRUB menu generation with live_boot recipe.
   - Level 2: Kernel loading, decompression, and early boot execution in QEMU.
2. Alpine Linux / Netboot fallback:
   - Dynamic discovery of boot structures and generation of valid loopback menu.
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


class TestMultiDistroLoopbackIntegration(unittest.TestCase):
    """End-to-end integration tests verifying loopback boot across multiple Linux distributions."""

    @classmethod
    def setUpClass(cls):
        cls.qemu_exe = find_qemu()
        if not cls.qemu_exe:
            raise unittest.SkipTest("QEMU executable (qemu-system-x86_64) not found")

        cls.code_fd, cls.vars_template_fd = find_firmware(cls.qemu_exe)
        if not cls.code_fd or not cls.vars_template_fd:
            raise unittest.SkipTest("OVMF firmware files not found")

        cls.clonezilla_iso = REPO_ROOT / "build" / "scratch" / "clonezilla-live.iso"
        cls.alpine_iso = REPO_ROOT / "build" / "scratch" / "alpine_virt.iso"

        cls.base_efi = REPO_ROOT / "boot" / "efi" / "BOOTX64.EFI"
        cls.base_grub_cfg = REPO_ROOT / "boot" / "grub" / "grub.cfg"
        cls.grub_modules_dir = REPO_ROOT / "boot" / "grub" / "x86_64-efi"

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_multidistro_test_"))
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

    def test_clonezilla_live_boot_execution(self):
        """Verify Debian live-boot recipe on real Clonezilla Live ISO boots Linux kernel in QEMU."""
        if not self.clonezilla_iso.is_file():
            self.skipTest(f"Clonezilla test ISO not found at {self.clonezilla_iso}")

        # Stage Clonezilla ISO into data partition
        staged_iso = self.data_mount / "ISO" / "clonezilla-live.iso"
        shutil.copy2(self.clonezilla_iso, staged_iso)

        # 1. Register ISO with live_boot recipe and serial debug arguments
        record = self.reg_manager.register_iso(
            iso_path=staged_iso,
            display_name="Clonezilla Live Debian",
            boot_method="linux_live_boot",
            recipe="live_boot",
            custom_args="console=tty0 console=ttyS0,115200n8 earlyprintk=serial,ttyS0,115200 nokaslr debug",
        )

        self.assertEqual(record.recipe, "live_boot")
        self.assertEqual(record.kernel_path, "/live/vmlinuz")

        # Level 1 Verification: Menu Generation
        menu_path = self.boot_mount / "boot" / "grub" / "mventx_menu.cfg"
        self.assertTrue(menu_path.is_file())
        menu_content = menu_path.read_text(encoding="utf-8")
        self.assertIn("Clonezilla Live Debian", menu_content)
        self.assertIn("linux (loop0)/live/vmlinuz", menu_content)
        self.assertIn("boot=live findiso=/ISO/clonezilla-live.iso", menu_content)

        # 2. Build 600MB GPT Dual-Partition Virtual Disk
        disk_img = self.temp_dir / "test_clonezilla_boot.img"
        build_dual_partition_gpt_disk(
            esp_source_dir=self.boot_mount,
            output_path=disk_img,
            disk_size_mb=600,
            esp_size_mb=64,
            data_source_dir=self.data_mount,
        )
        self.assertTrue(disk_img.exists())

        # 3. Launch QEMU with serial output redirection
        serial_log = self.temp_dir / "clonezilla_serial.log"
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
            # Allow kernel to decompress and begin execution
            proc.wait(timeout=35)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        self.assertTrue(serial_log.exists(), "Serial log was not generated")
        serial_content = serial_log.read_text(encoding="utf-8", errors="ignore")

        # Level 2 Verification: Kernel Loading Tested (Decompression and early execution)
        kernel_boot_signatures = [
            "Decompressing Linux",
            "Linux version",
            "BIOS-e820",
            "Command line:",
            "KASLR",
            "Booting Linux on physical CPU",
            "ACPI",
        ]
        found_signatures = [sig for sig in kernel_boot_signatures if sig in serial_content]
        self.assertTrue(
            len(found_signatures) > 0,
            f"Early kernel execution was not detected in serial log. Log snippet: {serial_content[:300]!r}",
        )

    def test_alpine_linux_menu_generation(self):
        """Verify Alpine Linux ISO registration produces valid loopback commands with discovered paths."""
        if not self.alpine_iso.is_file():
            self.skipTest(f"Alpine test ISO not found at {self.alpine_iso}")

        staged_iso = self.data_mount / "ISO" / "alpine_virt.iso"
        shutil.copy2(self.alpine_iso, staged_iso)

        record = self.reg_manager.register_iso(
            iso_path=staged_iso,
            display_name="Alpine Linux Virt",
        )

        self.assertEqual(record.recipe, "alpine")
        self.assertEqual(record.kernel_path, "/boot/vmlinuz-virt")
        self.assertEqual(record.initrd_path, "/boot/initramfs-virt")

        menu_path = self.boot_mount / "boot" / "grub" / "mventx_menu.cfg"
        self.assertTrue(menu_path.is_file())
        menu_content = menu_path.read_text(encoding="utf-8")

        self.assertIn("Alpine Linux Virt", menu_content)
        self.assertIn("loopback loop0", menu_content)
        self.assertIn(record.kernel_path, menu_content)
        self.assertIn(record.initrd_path, menu_content)


if __name__ == "__main__":
    unittest.main()
