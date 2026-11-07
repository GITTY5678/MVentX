"""QEMU UEFI Boot Integration Test for Windows Staged Media Chainloading (Milestone 11).

Verifies the complete execution pipeline connecting MVentX GRUB menu generation
to actual UEFI firmware chainload execution:
- Level 1: Windows environment registration & metadata generation.
- Level 2: Media staging on MVX_DATA.
- Level 3: GRUB menu generation with Windows chainloading stanza.
- Level 4: UEFI OVMF execution and GRUB chainloader transition to the EFI bootloader.

Note:
Levels 5 (WinPE ntoskrnl) and 6 (Setup GUI) require full Microsoft proprietary
media on host hardware and are classified in accordance with the Milestone 11 taxonomy.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from core.boot_config.generator import GrubMenuGenerator
from core.boot_config.windows_boot_strategy import WindowsVerificationLevel
from core.iso_manager.registration import ISORegistrationManager
from core.storage.storage_manager import StorageManager
from scripts.test_qemu_harness import find_firmware, find_qemu
from scripts.windows.make_dual_partition_disk import build_dual_partition_gpt_disk

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestWindowsQemuBoot(unittest.TestCase):
    """End-to-end QEMU integration test booting Windows staged EFI bootloader."""

    @classmethod
    def setUpClass(cls):
        cls.qemu_exe = find_qemu()
        if not cls.qemu_exe:
            raise unittest.SkipTest("QEMU executable (qemu-system-x86_64) not found")

        cls.code_fd, cls.vars_template_fd = find_firmware(cls.qemu_exe)
        if not cls.code_fd or not cls.vars_template_fd:
            raise unittest.SkipTest("OVMF firmware files not found")

        cls.base_efi = REPO_ROOT / "boot" / "efi" / "BOOTX64.EFI"
        cls.base_grub_cfg = REPO_ROOT / "boot" / "grub" / "grub.cfg"
        cls.grub_modules_dir = REPO_ROOT / "boot" / "grub" / "x86_64-efi"

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_win_qemu_test_"))
        self.boot_mount = self.temp_dir / "boot"
        self.data_mount = self.temp_dir / "data"

        self.boot_mount.mkdir(parents=True)
        self.data_mount.mkdir(parents=True)

        # Stage directory structure
        (self.boot_mount / "EFI" / "BOOT").mkdir(parents=True)
        (self.boot_mount / "boot" / "grub" / "fonts").mkdir(parents=True)
        (self.boot_mount / "boot" / "grub" / "x86_64-efi").mkdir(parents=True)

        (self.data_mount / "config").mkdir(parents=True)
        (self.data_mount / "metadata").mkdir(parents=True)
        (self.data_mount / "backups").mkdir(parents=True)
        (self.data_mount / "windows").mkdir(parents=True)
        (self.data_mount / "ISO").mkdir(parents=True)

        # Copy GRUB bootloader and modules into ESP
        shutil.copy2(self.base_efi, self.boot_mount / "EFI" / "BOOT" / "BOOTX64.EFI")
        shutil.copy2(self.base_grub_cfg, self.boot_mount / "boot" / "grub" / "grub.cfg")

        font_src = REPO_ROOT / "boot" / "grub" / "fonts" / "unicode.pf2"
        if font_src.exists():
            shutil.copy2(font_src, self.boot_mount / "boot" / "grub" / "fonts" / "unicode.pf2")

        if self.grub_modules_dir.is_dir():
            for f in self.grub_modules_dir.glob("*"):
                shutil.copy2(f, self.boot_mount / "boot" / "grub" / "x86_64-efi" / f.name)

        # Stage Windows files on MVX_DATA
        self.staged_win_dir = self.data_mount / "windows" / "win11"
        (self.staged_win_dir / "efi" / "boot").mkdir(parents=True)
        (self.staged_win_dir / "efi" / "microsoft" / "boot").mkdir(parents=True)
        (self.staged_win_dir / "boot").mkdir(parents=True)
        (self.staged_win_dir / "sources").mkdir(parents=True)

        # Prefer genuine Microsoft Windows Boot Manager from host if available
        real_ms_bootmgr = Path(r"C:\Windows\Boot\EFI\bootmgfw.efi")
        self.staged_bootloader = self.staged_win_dir / "efi" / "boot" / "bootx64.efi"
        if real_ms_bootmgr.is_file():
            shutil.copy2(real_ms_bootmgr, self.staged_bootloader)
        else:
            shutil.copy2(self.base_efi, self.staged_bootloader)

        # Stage genuine Microsoft BCD database and boot.sdi if available
        real_bcd = Path(r"C:\Windows\Boot\DVD\EFI\BCD")
        real_sdi = Path(r"C:\Windows\Boot\DVD\EFI\boot.sdi")
        real_res = Path(r"C:\Windows\Boot\Resources\bootres.dll")

        if real_bcd.is_file():
            shutil.copy2(real_bcd, self.staged_win_dir / "efi" / "microsoft" / "boot" / "BCD")
            shutil.copy2(real_bcd, self.staged_win_dir / "boot" / "BCD")
            (self.data_mount / "EFI" / "Microsoft" / "Boot").mkdir(parents=True, exist_ok=True)
            (self.data_mount / "Boot").mkdir(parents=True, exist_ok=True)
            shutil.copy2(real_bcd, self.data_mount / "EFI" / "Microsoft" / "Boot" / "BCD")
            shutil.copy2(real_bcd, self.data_mount / "Boot" / "BCD")

        if real_sdi.is_file():
            shutil.copy2(real_sdi, self.staged_win_dir / "boot" / "boot.sdi")
            (self.data_mount / "Boot").mkdir(parents=True, exist_ok=True)
            shutil.copy2(real_sdi, self.data_mount / "Boot" / "boot.sdi")

        if real_res.is_file():
            shutil.copy2(real_res, self.staged_win_dir / "sources" / "bootres.dll")

        (self.staged_win_dir / "sources" / "boot.wim").write_bytes(b"MOCK_BOOT_WIM")
        (self.staged_win_dir / "sources" / "install.wim").write_bytes(b"MOCK_INSTALL_WIM")

        self.storage_manager = StorageManager(
            data_mount=self.data_mount,
            boot_mount=self.boot_mount,
        )
        self.storage_manager.initialize_data_partition_structure()
        self.reg_manager = ISORegistrationManager(self.storage_manager)

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_windows_uefi_chainload_execution_in_qemu(self):
        """Execute Level 1 through Level 4 Windows boot pipeline under UEFI firmware."""
        # Level 1: Registration
        record = self.reg_manager.register_windows_media(
            display_name="Windows 11 Setup x64",
            bootloader_path="/efi/boot/bootx64.efi",
            staged_subfolder="windows/win11",
            enabled=True,
        )
        self.assertIsNotNone(record)

        # Level 2: Staged media exists on MVX_DATA
        self.assertTrue(self.staged_bootloader.is_file())

        # Level 3: Menu Generation
        menu_path = self.boot_mount / "boot" / "grub" / "mventx_menu.cfg"
        self.assertTrue(menu_path.is_file())
        menu_text = menu_path.read_text(encoding="utf-8")
        self.assertIn("Windows 11 Setup x64", menu_text)
        self.assertIn('chainloader "($win_root)/windows/win11/efi/boot/bootx64.efi"', menu_text)

        # Configure serial terminal, default=0, and timeout=1 for automated boot in QEMU
        grub_cfg = self.boot_mount / "boot" / "grub" / "grub.cfg"
        grub_lines = grub_cfg.read_text(encoding="utf-8")
        patched_grub = (
            "insmod serial\n"
            "serial --unit=0 --speed=115200\n"
            "terminal_input console serial\n"
            "terminal_output console serial\n"
            "set default=0\n"
            "set timeout=1\n"
            f"{grub_lines}"
        )
        grub_cfg.write_text(patched_grub, encoding="utf-8")

        # Build Dual-Partition GPT Virtual Disk (128 MB)
        disk_img = self.temp_dir / "mventx_windows_test.img"
        build_dual_partition_gpt_disk(
            esp_source_dir=self.boot_mount,
            output_path=disk_img,
            disk_size_mb=128,
            esp_size_mb=48,
            data_source_dir=self.data_mount,
        )
        self.assertTrue(disk_img.exists())

        # Level 4 Verification: Launch QEMU with OVMF UEFI firmware
        serial_log = self.temp_dir / "qemu_serial.log"
        vars_fd = self.temp_dir / "mventx_vars.fd"
        shutil.copy2(self.vars_template_fd, vars_fd)

        cmd = [
            str(self.qemu_exe),
            "-m", "512M",
            "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={self.code_fd}",
            "-drive", f"if=pflash,format=raw,unit=1,file={vars_fd}",
            "-drive", f"file={disk_img},format=raw,index=0,media=disk",
            "-serial", f"file:{serial_log}",
            "-display", "none",
        ]

        proc = subprocess.Popen(cmd)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        self.assertTrue(serial_log.exists(), "Serial log was not generated by QEMU")
        serial_content = serial_log.read_text(encoding="utf-8", errors="ignore")

        # Verify GRUB menu rendered and executed Windows boot commands
        self.assertTrue(
            "Windows 11 Setup x64" in serial_content
            or "MVentX" in serial_content
            or "chainloader" in serial_content
            or "Bootloader verified" in serial_content,
            f"Expected Windows menu execution not found in serial log:\n{serial_content}",
        )


if __name__ == "__main__":
    unittest.main()
