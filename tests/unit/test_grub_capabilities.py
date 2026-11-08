"""Unit tests for GNU GRUB 2.12 Capability Registry and Runtime Verification (Milestone 18)."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from core.boot_config.grub_capabilities import (
    CapabilityReport,
    CommandCapability,
    GRUBCommandType,
    GRUBCapabilityRegistry,
    STANDARD_COMMAND_CATALOG,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestGRUBCapabilities(unittest.TestCase):
    """Test suite for GRUBCapabilityRegistry, manifests parsing, and validation."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_cap_test_"))
        self.registry = GRUBCapabilityRegistry(
            x86_64_efi_dir=REPO_ROOT / "boot" / "grub" / "x86_64-efi"
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_standard_catalog_completeness(self) -> None:
        """Verify that all essential bootloader commands exist in the standard catalog."""
        catalog = STANDARD_COMMAND_CATALOG
        essential_cmds = [
            "echo", "sleep", "test", "search", "configfile", "source", "normal",
            "menuentry", "submenu", "insmod", "reboot", "halt", "exit", "fwsetup",
            "fat", "exfat", "ext2", "ntfs", "iso9660", "part_gpt", "loopback", "linux"
        ]
        for cmd in essential_cmds:
            self.assertIn(cmd, catalog, f"Expected '{cmd}' in standard catalog")
            cap = catalog[cmd]
            self.assertIsInstance(cap, CommandCapability)
            self.assertEqual(cap.name, cmd)

        self.assertEqual(catalog["reboot"].command_type, GRUBCommandType.MODULE)
        self.assertEqual(catalog["reboot"].module_name, "reboot")
        self.assertEqual(catalog["halt"].command_type, GRUBCommandType.MODULE)
        self.assertEqual(catalog["halt"].module_name, "halt")
        self.assertEqual(catalog["echo"].command_type, GRUBCommandType.BUILTIN)

    def test_parse_command_lst(self) -> None:
        """Verify parsing of official command.lst manifest."""
        mock_file = self.temp_dir / "command.lst"
        mock_file.write_text(
            "# GRUB command list\n"
            "reboot: reboot\n"
            "halt: halt\n"
            "fwsetup: efifwsetup\n"
            "exit: minicmd\n",
            encoding="utf-8"
        )
        mapping = GRUBCapabilityRegistry.parse_command_lst(mock_file)
        self.assertEqual(mapping.get("reboot"), "reboot")
        self.assertEqual(mapping.get("halt"), "halt")
        self.assertEqual(mapping.get("fwsetup"), "efifwsetup")
        self.assertEqual(mapping.get("exit"), "minicmd")

    def test_parse_moddep_lst(self) -> None:
        """Verify parsing of official moddep.lst dependency manifest."""
        mock_file = self.temp_dir / "moddep.lst"
        mock_file.write_text(
            "halt: acpi\n"
            "acpi: extcmd mmap\n"
            "reboot:\n"
            "exfat: fshelp\n",
            encoding="utf-8"
        )
        deps = GRUBCapabilityRegistry.parse_moddep_lst(mock_file)
        self.assertEqual(deps.get("halt"), ["acpi"])
        self.assertEqual(deps.get("acpi"), ["extcmd", "mmap"])
        self.assertEqual(deps.get("reboot"), [])
        self.assertEqual(deps.get("exfat"), ["fshelp"])

    def test_resolve_dependencies_recursive(self) -> None:
        """Verify recursive multi-hop dependency resolution in topological order."""
        mock_dep_file = self.temp_dir / "moddep.lst"
        mock_dep_file.write_text(
            "halt: acpi\n"
            "acpi: extcmd mmap\n"
            "extcmd:\n"
            "mmap:\n",
            encoding="utf-8"
        )
        reg = GRUBCapabilityRegistry(x86_64_efi_dir=self.temp_dir)
        halt_deps = reg.resolve_dependencies("halt")
        # Dependencies must include acpi, extcmd, and mmap
        self.assertIn("acpi", halt_deps)
        self.assertIn("extcmd", halt_deps)
        self.assertIn("mmap", halt_deps)

    def test_inspect_real_efi_binary(self) -> None:
        """Verify inspection of real repository BOOTX64.EFI binary."""
        efi_path = REPO_ROOT / "boot" / "efi" / "BOOTX64.EFI"
        self.assertTrue(efi_path.is_file())
        info = GRUBCapabilityRegistry.inspect_efi_binary(efi_path)
        self.assertEqual(info["architecture"], "x86_64 (PE32+)")
        self.assertTrue(info["is_pe32_plus"])
        self.assertFalse(info["is_signed"])  # Real binary is unsigned, triggering SB violation when active
        self.assertEqual(info["cert_table_size"], 0)
        self.assertIn("echo", info["builtin_detected"])
        self.assertIn("sleep", info["builtin_detected"])
        self.assertIn("test", info["builtin_detected"])
        self.assertIn("search", info["builtin_detected"])
        self.assertIn("configfile", info["builtin_detected"])

    def test_inspect_corrupt_efi_binary(self) -> None:
        """Verify graceful error handling for corrupted EFI binaries."""
        bad_file = self.temp_dir / "bad.efi"
        bad_file.write_bytes(b"NOT_A_VALID_PE_IMAGE")
        with self.assertRaises(ValueError):
            GRUBCapabilityRegistry.inspect_efi_binary(bad_file)

    def test_validate_staged_assets_real_repo(self) -> None:
        """Verify asset validation on the repository boot files."""
        # Create mock target ESP mount layout mirroring repository assets
        esp_dir = self.temp_dir / "esp"
        (esp_dir / "EFI" / "BOOT").mkdir(parents=True)
        (esp_dir / "boot" / "grub" / "x86_64-efi").mkdir(parents=True)

        shutil.copy2(REPO_ROOT / "boot" / "efi" / "BOOTX64.EFI", esp_dir / "EFI" / "BOOT" / "BOOTX64.EFI")
        shutil.copy2(REPO_ROOT / "boot" / "grub" / "grub.cfg", esp_dir / "boot" / "grub" / "grub.cfg")

        # Copy modules and manifests
        mod_src = REPO_ROOT / "boot" / "grub" / "x86_64-efi"
        for f in mod_src.glob("*"):
            if f.is_file():
                shutil.copy2(f, esp_dir / "boot" / "grub" / "x86_64-efi" / f.name)

        report = self.registry.validate_staged_assets(esp_dir)
        self.assertTrue(report.is_valid, f"Expected valid assets report, got errors: {report.errors}")
        self.assertEqual(report.architecture, "x86_64 (PE32+)")
        self.assertFalse(report.is_signed)
        self.assertGreater(len(report.available_modules), 250)
        self.assertEqual(len(report.missing_required_modules), 0)
        self.assertIn("reboot", report.supported_utilities)
        self.assertIn("halt", report.supported_utilities)
        self.assertIn("exit", report.supported_utilities)
        self.assertIn("fwsetup", report.supported_utilities)
        self.assertEqual(len(report.unsupported_utilities), 0)

    def test_validate_staged_assets_detects_missing_module(self) -> None:
        """Verify that missing required modules are detected and reported."""
        esp_dir = self.temp_dir / "esp_incomplete"
        (esp_dir / "EFI" / "BOOT").mkdir(parents=True)
        (esp_dir / "boot" / "grub" / "x86_64-efi").mkdir(parents=True)

        shutil.copy2(REPO_ROOT / "boot" / "efi" / "BOOTX64.EFI", esp_dir / "EFI" / "BOOT" / "BOOTX64.EFI")
        shutil.copy2(REPO_ROOT / "boot" / "grub" / "grub.cfg", esp_dir / "boot" / "grub" / "grub.cfg")

        # Copy only reboot.mod, but NOT halt.mod or exfat.mod
        shutil.copy2(REPO_ROOT / "boot" / "grub" / "x86_64-efi" / "reboot.mod", esp_dir / "boot" / "grub" / "x86_64-efi" / "reboot.mod")

        report = self.registry.validate_staged_assets(esp_dir)
        self.assertFalse(report.is_valid)
        self.assertIn("halt", report.missing_required_modules)
        self.assertIn("exfat", report.missing_required_modules)
        self.assertIn("halt", report.unsupported_utilities)
        self.assertIn("reboot", report.supported_utilities)

    def test_generate_system_utilities_script(self) -> None:
        """Verify generation of clean, supported system utilities submenu."""
        script_lines = self.registry.generate_system_utilities_script(
            supported_utilities=["reboot", "halt", "exit", "fwsetup"],
            include_heading=True,
        )
        script = "\n".join(script_lines)
        self.assertIn('submenu "MVentX System Utilities" --id "mventx-system-utils"', script)
        self.assertIn('menuentry "Reboot System" --id "system-reboot"', script)
        self.assertIn('menuentry "Halt / Power Off System" --id "system-halt"', script)
        self.assertIn('menuentry "Exit GRUB (Return to UEFI Boot Manager)" --id "system-exit"', script)
        self.assertIn('menuentry "UEFI Firmware Settings" --id "uefi-firmware"', script)
        self.assertIn("reboot", script)
        self.assertIn("halt", script)
        self.assertIn("exit", script)
        self.assertIn("fwsetup", script)

    def test_generate_system_utilities_script_omits_unsupported(self) -> None:
        """Verify that unsupported utilities are never emitted in the menu."""
        script_lines = self.registry.generate_system_utilities_script(
            supported_utilities=["reboot"],  # Only reboot supported
            include_heading=True,
        )
        script = "\n".join(script_lines)
        self.assertIn('menuentry "Reboot System"', script)
        self.assertNotIn('menuentry "Halt / Power Off System"', script)
        self.assertNotIn('menuentry "UEFI Firmware Settings"', script)
        self.assertNotIn('menuentry "Exit GRUB', script)


if __name__ == "__main__":
    unittest.main()

