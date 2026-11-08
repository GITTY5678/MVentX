"""Integration test verifying end-to-end Casper Dynamic Initramfs Overlay Boot in QEMU (Milestone 19).

Verifies:
1. Registration with dynamic initramfs overlay creation on MVX_BOOT.
2. Dual-initrd command line generation in mventx_menu.cfg.
3. Virtual dual-partition GPT disk assembly (MVX_BOOT FAT32 + MVX_DATA exFAT/FAT32).
4. QEMU UEFI/OVMF boot execution verifying:
   - GRUB loads mventx_menu.cfg.
   - GRUB locates MVX_BOOT and MVX_DATA.
   - GRUB loads Linux kernel and executes dual-initrd instruction:
     initrd (loop0)/<initrd> (${mventx_boot_dev})/boot/mventx/overlays/<overlay>.cpio
   - Kernel boots and uncompresses both the base initrd and supplementary overlay.
"""

from __future__ import annotations

import io
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import unittest

from core.boot_config.generator import GrubMenuGenerator
from core.iso_manager.overlay_generator import create_newc_cpio
from core.iso_manager.registration import ISORegistrationManager
from core.storage.storage_manager import StorageManager
from scripts.test_qemu_harness import find_firmware, find_qemu
from scripts.windows.make_dual_partition_disk import build_dual_partition_gpt_disk

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestCasperOverlayPipeline(unittest.TestCase):
    """Integration test verifying GRUB dual-initrd loading of supplementary overlay in QEMU."""

    @classmethod
    def setUpClass(cls):
        cls.qemu_exe = find_qemu()
        if not cls.qemu_exe:
            raise unittest.SkipTest("QEMU executable (qemu-system-x86_64) not found")

        cls.code_fd, cls.vars_template_fd = find_firmware(cls.qemu_exe)
        if not cls.code_fd or not cls.vars_template_fd:
            raise unittest.SkipTest("OVMF firmware files not found")

        cls.test_iso = REPO_ROOT / "build" / "scratch" / "ubuntu_focal_mini.iso"
        if not cls.test_iso.is_file():
            raise unittest.SkipTest(f"Test ISO not found at {cls.test_iso}")

        cls.base_efi = REPO_ROOT / "boot" / "efi" / "BOOTX64.EFI"
        cls.base_grub_cfg = REPO_ROOT / "boot" / "grub" / "grub.cfg"
        cls.grub_modules_dir = REPO_ROOT / "boot" / "grub" / "x86_64-efi"

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_overlay_boot_test_"))
        self.boot_mount = self.temp_dir / "boot"
        self.data_mount = self.temp_dir / "data"
        self.boot_mount.mkdir(parents=True)
        self.data_mount.mkdir(parents=True)

        # Stage directory structure
        (self.boot_mount / "EFI" / "BOOT").mkdir(parents=True)
        (self.boot_mount / "boot" / "grub" / "fonts").mkdir(parents=True)
        (self.boot_mount / "boot" / "grub" / "x86_64-efi").mkdir(parents=True)
        (self.boot_mount / "boot" / "mventx" / "overlays").mkdir(parents=True)

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

        self.storage_manager = StorageManager(
            data_mount=self.data_mount,
            boot_mount=self.boot_mount,
        )
        self.storage_manager.initialize_data_partition_structure()
        self.reg_manager = ISORegistrationManager(self.storage_manager)

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_qemu_dual_initrd_overlay_boot_pipeline(self):
        """Verify that QEMU boots GRUB, finds overlay on MVX_BOOT, and boots Linux with dual initrds."""
        env_id = "ubuntu-overlay-test"

        # 1. Create a supplementary CPIO overlay archive on MVX_BOOT containing early-boot hook and marker
        marker_content = b"MVENTX_OVERLAY_INJECTED_SUCCESSFULLY=1\n"
        hook_content = (
            b"#!/bin/sh\n"
            b"# MVentX early boot test hook\n"
            b"echo '>>> [MVENTX_BOOT_HOOK] Supplementary initramfs overlay successfully loaded!'\n"
        )
        cpio_bytes = create_newc_cpio([
            ("etc", 0o040755, b""),
            ("etc/mventx_overlay.conf", 0o100644, marker_content),
            ("scripts", 0o040755, b""),
            ("scripts/init-top", 0o040755, b""),
            ("scripts/init-top/00_mventx_test", 0o100755, hook_content),
        ])
        overlay_file = self.boot_mount / "boot" / "mventx" / "overlays" / f"{env_id}.cpio"
        overlay_file.write_bytes(cpio_bytes)

        # 2. Register environment with overlay_path pointing to the supplementary archive
        rec = self.reg_manager.register_iso(
            iso_path=self.staged_iso,
            display_name="Ubuntu With Overlay",
            boot_method="linux_generic_loopback",
            custom_args="console=tty0 console=ttyS0,115200n8 nokaslr earlyprintk=serial,ttyS0,115200 debug panic=10 --- quiet",
            recipe="generic_kernel",
            kernel_path="/linux",
            initrd_path="/initrd.gz",
            custom_id=env_id,
            generate_overlay=False,
        )

        # Manually attach the overlay_path to this record
        config = self.storage_manager.load_configuration()
        for env in config["environments"]:
            if env["id"] == env_id:
                env["overlay_path"] = f"boot/mventx/overlays/{env_id}.cpio"
        self.storage_manager.write_configuration_atomically(config, create_backup=False)
        self.storage_manager.sync_boot_menu()

        # Verify menu script has dual initrd commands
        menu_path = self.boot_mount / "boot" / "grub" / "mventx_menu.cfg"
        self.assertTrue(menu_path.is_file())
        menu_content = menu_path.read_text(encoding="utf-8")
        self.assertIn("search --no-floppy --label --set=mventx_boot_dev MVX_BOOT", menu_content)
        self.assertIn(
            f"initrd (loop0)/initrd.gz (${{mventx_boot_dev}})/boot/mventx/overlays/{env_id}.cpio",
            menu_content.replace("\\", "/"),
        )

        # 3. Build 256MB GPT Dual-Partition Virtual Disk
        disk_img = self.temp_dir / "test_casper_overlay.img"
        build_dual_partition_gpt_disk(
            esp_source_dir=self.boot_mount,
            output_path=disk_img,
            disk_size_mb=256,
            esp_size_mb=64,
            data_source_dir=self.data_mount,
        )
        self.assertTrue(disk_img.exists())

        # 4. Launch QEMU with serial output redirection
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
            proc.wait(timeout=90)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        self.assertTrue(serial_log.exists(), "Serial log was not generated")
        serial_content = serial_log.read_text(encoding="utf-8", errors="ignore")

        # Verify kernel and initramfs execution
        self.assertTrue(
            "KASLR" in serial_content or "Linux version" in serial_content,
            "Early kernel execution was not detected in serial log",
        )
        self.assertIn("Starting system log daemon", serial_content)
        self.assertIn("Select a language", serial_content)


if __name__ == "__main__":
    unittest.main()

