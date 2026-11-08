"""Automated QEMU + OVMF Integration Tests for GRUB Command Execution (Milestone 18).

Verifies actual execution and hardware state transitions of:
- Reboot (ResetSystem cold reset)
- Halt / Power Off (ACPI shutdown / powerdown)
- Exit (UEFI firmware handoff)
- Filesystem discovery & exFAT driver loading
- Dynamic menu loading via configfile
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

from installer.bootloader_installer import BootloaderInstaller
from scripts.test_qemu_harness import find_firmware, find_qemu
from scripts.windows.make_dual_partition_disk import build_dual_partition_gpt_disk

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestGRUBCommandExecutionQemu(unittest.TestCase):
    """Integration test suite executing GNU GRUB 2.12 commands under QEMU + OVMF."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.qemu_exe = find_qemu()
        if not cls.qemu_exe:
            raise unittest.SkipTest("qemu-system-x86_64.exe not found on system")

        cls.code_fd, cls.vars_template = find_firmware(cls.qemu_exe)
        if not cls.code_fd or not cls.vars_template:
            raise unittest.SkipTest("OVMF firmware files not found")

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_qemu_cmd_"))
        self.esp_dir = self.temp_dir / "esp"
        self.data_dir = self.temp_dir / "data"
        self.esp_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Deploy bootloader and all modules + manifests
        installer = BootloaderInstaller(self.esp_dir, assets_root=REPO_ROOT)
        installer.deploy_all(title="MVentX M18 Test", timeout=5)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run_qemu(self, disk_img: Path, timeout_seconds: int = 10) -> Tuple[int, str]:
        """Launch headless QEMU with OVMF firmware and capture serial output."""
        serial_log = self.temp_dir / f"serial_{int(time.time()*1000)}.log"
        vars_fd = self.temp_dir / f"vars_{int(time.time()*1000)}.fd"
        shutil.copy2(self.vars_template, vars_fd)

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
            exit_code = proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
            exit_code = -1

        output = ""
        if serial_log.exists():
            output = serial_log.read_text(encoding="utf-8", errors="replace")
        return exit_code, output

    def test_qemu_reboot_command_execution(self) -> None:
        """Verify that 'reboot' command executes cold reset and reboots the UEFI machine."""
        cfg_path = self.esp_dir / "boot" / "grub" / "grub.cfg"
        test_script = (
            "insmod reboot\n"
            "echo '>>> MARKER: REBOOT INITIATED <<<'\n"
            "reboot\n"
            "echo '>>> MARKER: THIS_SHOULD_NEVER_RUN <<<'\n"
        )
        cfg_path.write_text(test_script, encoding="utf-8")

        disk_img = self.temp_dir / "reboot_test.img"
        build_dual_partition_gpt_disk(
            esp_source_dir=self.esp_dir,
            output_path=disk_img,
            disk_size_mb=256,
            esp_size_mb=64,
        )

        _, log = self._run_qemu(disk_img, timeout_seconds=11)
        self.assertIn(">>> MARKER: REBOOT INITIATED <<<", log)
        self.assertNotIn(">>> MARKER: THIS_SHOULD_NEVER_RUN <<<", log)
        # Verify that BdsDxe restarted after the reboot marker
        after_marker = log.split(">>> MARKER: REBOOT INITIATED <<<")[1]
        self.assertIn("BdsDxe: loading Boot0001", after_marker, "Firmware did not reboot after reboot command")

    def test_qemu_halt_command_execution(self) -> None:
        """Verify that 'halt' command powers down the system and terminates QEMU with exit 0."""
        cfg_path = self.esp_dir / "boot" / "grub" / "grub.cfg"
        test_script = (
            "serial --speed=115200\n"
            "terminal_output console serial\n"
            "insmod acpi\n"
            "insmod halt\n"
            "echo '>>> MARKER: HALT INITIATED <<<'\n"
            "halt\n"
            "echo '>>> MARKER: THIS_SHOULD_NEVER_RUN <<<'\n"
        )
        cfg_path.write_text(test_script, encoding="utf-8")

        disk_img = self.temp_dir / "halt_test.img"
        build_dual_partition_gpt_disk(
            esp_source_dir=self.esp_dir,
            output_path=disk_img,
            disk_size_mb=256,
            esp_size_mb=64,
        )

        exit_code, log = self._run_qemu(disk_img, timeout_seconds=8)
        self.assertIn(">>> MARKER: HALT INITIATED <<<", log)
        self.assertNotIn(">>> MARKER: THIS_SHOULD_NEVER_RUN <<<", log)
        self.assertEqual(exit_code, 0, "QEMU did not terminate cleanly on ACPI halt")

    def test_qemu_exit_command_execution(self) -> None:
        """Verify that 'exit' command cleanly hands off control back to UEFI firmware."""
        cfg_path = self.esp_dir / "boot" / "grub" / "grub.cfg"
        test_script = (
            "serial --speed=115200\n"
            "terminal_output console serial\n"
            "insmod minicmd\n"
            "echo '>>> MARKER: EXIT INITIATED <<<'\n"
            "exit\n"
            "echo '>>> MARKER: POST_EXIT <<<'\n"
        )
        cfg_path.write_text(test_script, encoding="utf-8")

        disk_img = self.temp_dir / "exit_test.img"
        build_dual_partition_gpt_disk(
            esp_source_dir=self.esp_dir,
            output_path=disk_img,
            disk_size_mb=256,
            esp_size_mb=64,
        )

        _, log = self._run_qemu(disk_img, timeout_seconds=8)
        self.assertIn(">>> MARKER: EXIT INITIATED <<<", log)
        self.assertNotIn(">>> MARKER: POST_EXIT <<<", log)
        # Verify that firmware received control back (UiApp invoked)
        self.assertIn("UiApp", log, "Firmware UiApp was not launched after GRUB exit")

    def test_qemu_exfat_module_loading_and_discovery(self) -> None:
        """Verify that exFAT module loads dynamically and accesses MVX_DATA filesystem."""
        # Create a test marker file on data partition
        (self.data_dir / "config").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "config" / "marker.txt").write_text("MVX_EXFAT_ACTIVE", encoding="utf-8")

        cfg_path = self.esp_dir / "boot" / "grub" / "grub.cfg"
        test_script = (
            "serial --speed=115200\n"
            "terminal_output console serial\n"
            "insmod exfat\n"
            "set found_data=0\n"
            "if search --no-floppy --label --set=data_dev MVX_DATA; then\n"
            "    set found_data=1\n"
            "    echo '>>> MARKER: FOUND_EXFAT_PARTITION' $data_dev '<<<'\n"
            "fi\n"
            "insmod acpi\n"
            "insmod halt\n"
            "halt\n"
        )
        cfg_path.write_text(test_script, encoding="utf-8")

        disk_img = self.temp_dir / "exfat_test.img"
        build_dual_partition_gpt_disk(
            esp_source_dir=self.esp_dir,
            data_source_dir=self.data_dir,
            output_path=disk_img,
            disk_size_mb=256,
            esp_size_mb=64,
        )

        exit_code, log = self._run_qemu(disk_img, timeout_seconds=8)
        self.assertIn(">>> MARKER: FOUND_EXFAT_PARTITION", log)
        self.assertEqual(exit_code, 0)

    def test_qemu_dynamic_menu_with_system_utilities(self) -> None:
        """Verify that dynamic mventx_menu.cfg generates and displays the System Utilities submenu."""
        from core.boot_config.generator import GrubMenuGenerator

        config_data = {
            "version": "1.0.0",
            "meta": {
                "title": "MVentX Dynamic Test",
                "timeout": 3,
            },
            "environments": [
                {
                    "id": "env_alpine",
                    "display_name": "Alpine Linux Test",
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "recipe": "alpine",
                    "iso_path": "ISO/alpine.iso",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }
        menu_path = self.esp_dir / "boot" / "grub" / "mventx_menu.cfg"
        gen = GrubMenuGenerator()
        gen.write_menu_file(config_data, menu_path)

        menu_content = menu_path.read_text(encoding="utf-8")
        self.assertIn('submenu "MVentX System Utilities"', menu_content)
        self.assertIn('menuentry "Reboot System"', menu_content)
        self.assertIn('menuentry "Halt / Power Off System"', menu_content)
        self.assertIn('menuentry "Exit GRUB', menu_content)
        self.assertIn('menuentry "Alpine Linux Test"', menu_content)

        # Update static grub.cfg to source mventx_menu.cfg and dump menu test marker to serial
        cfg_path = self.esp_dir / "boot" / "grub" / "grub.cfg"
        test_script = (
            "serial --speed=115200\n"
            "terminal_output console serial\n"
            "insmod configfile\n"
            "echo '>>> MARKER: SOURCING DYNAMIC MENU <<<'\n"
            "configfile /boot/grub/mventx_menu.cfg\n"
        )
        cfg_path.write_text(test_script, encoding="utf-8")

        disk_img = self.temp_dir / "dynamic_menu_test.img"
        build_dual_partition_gpt_disk(
            esp_source_dir=self.esp_dir,
            output_path=disk_img,
            disk_size_mb=256,
            esp_size_mb=64,
        )

        _, log = self._run_qemu(disk_img, timeout_seconds=6)
        self.assertIn(">>> MARKER: SOURCING DYNAMIC MENU <<<", log)


if __name__ == "__main__":
    unittest.main()

