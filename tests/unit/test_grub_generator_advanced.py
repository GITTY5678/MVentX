"""Unit tests for advanced GRUB generator functionality, escaping, security, and determinism."""

import json
from pathlib import Path
import tempfile
import unittest

from core.boot_config.generator import (
    ConfigSecurityError,
    ConfigValidationError,
    GrubMenuGenerator,
    sanitize_custom_args,
    sanitize_display_name,
    sanitize_id,
    sanitize_path,
)


class TestGrubGeneratorAdvanced(unittest.TestCase):
    """Test suite verifying generator determinism, security escaping, and diagnostic warnings."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.data_dir = self.root_path / "MVX_DATA"
        self.data_dir.mkdir()
        (self.data_dir / "ISO").mkdir()

        self.generator = GrubMenuGenerator(data_dir=self.data_dir)

        self.valid_config = {
            "version": "1.0.0",
            "meta": {
                "title": "MVentX Multiboot System",
                "theme": "mventx",
                "timeout": 10,
                "default_entry_id": None,
            },
            "environments": [
                {
                    "id": "arch-linux",
                    "display_name": "Arch Linux 2026",
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 1,
                    "iso_path": "ISO/arch.iso",
                },
                {
                    "id": "ubuntu-lts",
                    "display_name": "Ubuntu 24.04 LTS",
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 2,
                    "iso_path": "ISO/ubuntu.iso",
                },
            ],
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generator_determinism(self):
        """Verify that identical input always produces byte-for-byte identical output."""
        baseline = self.generator.generate_menu_script(self.valid_config)
        for _ in range(50):
            result = self.generator.generate_menu_script(self.valid_config)
            self.assertEqual(result, baseline)

    def test_escaping_quotes_dollar_and_slashes(self):
        """Verify proper escaping of quotes, dollar signs, and backslashes in display names."""
        name_with_quotes = sanitize_display_name('Linux "Gamer" Edition')
        self.assertEqual(name_with_quotes, 'Linux \\"Gamer\\" Edition')

        name_with_dollar = sanitize_display_name("Distro $100 Pro")
        self.assertEqual(name_with_dollar, "Distro \\$100 Pro")

        name_with_slashes = sanitize_display_name(r"Distro\Testing\Edition")
        self.assertEqual(name_with_slashes, "Distro\\\\Testing\\\\Edition")

    def test_command_injection_prevention(self):
        """Verify that command substitution and arbitrary command injection are strictly rejected."""
        dangerous_inputs = [
            'Test"; reboot; echo "',
            'Test; halt',
            "Test $(reboot)",
            "Test `reboot`",
            "Test ${PATH}",
            "Test\nmenuentry 'Injected' { reboot; }",
            "Test\r\nreboot",
            "Test { nested block }",
        ]
        for bad_input in dangerous_inputs:
            with self.assertRaises(ConfigSecurityError, msg=f"Failed to reject: {bad_input}"):
                sanitize_display_name(bad_input)

    def test_id_sanitization(self):
        """Verify strict environment ID rules."""
        self.assertEqual(sanitize_id("ubuntu_24-04"), "ubuntu_24-04")
        invalid_ids = ["env with spaces", "env;cmd", "env$id", "env/slash"]
        for bad_id in invalid_ids:
            with self.assertRaises(ConfigSecurityError):
                sanitize_id(bad_id)

    def test_path_sanitization_traversal_rejection(self):
        """Verify path traversal prevention in sanitize_path."""
        self.assertEqual(sanitize_path("ISO/distro.iso"), "ISO/distro.iso")
        self.assertEqual(sanitize_path(r"ISO\sub\distro.iso"), "ISO/sub/distro.iso")

        with self.assertRaises(ConfigSecurityError):
            sanitize_path("../../Windows/System32/cmd.exe")
        with self.assertRaises(ConfigSecurityError):
            sanitize_path("ISO/../secret.txt")
        with self.assertRaises(ConfigSecurityError):
            sanitize_path("ISO/test;reboot")

    def test_missing_iso_file_diagnostic(self):
        """Verify that missing ISO files trigger a clear diagnostic warning in the generated menu."""
        # ISO files don't exist on disk yet
        menu = self.generator.generate_menu_script(self.valid_config)
        self.assertIn("Registered ISO file missing from storage: ISO/arch.iso", menu)
        self.assertIn("Registered ISO file missing from storage: ISO/ubuntu.iso", menu)

        # Create one of the ISO files
        (self.data_dir / "ISO" / "arch.iso").write_bytes(b"DUMMY_ISO_DATA")
        menu_after = self.generator.generate_menu_script(self.valid_config)
        self.assertNotIn("Registered ISO file missing from storage: ISO/arch.iso", menu_after)
        self.assertIn("Registered ISO file missing from storage: ISO/ubuntu.iso", menu_after)

    def test_unrecognized_boot_method_diagnostic(self):
        """Verify that unrecognized boot methods are handled safely with a diagnostic."""
        config = {
            "version": "1.0.0",
            "meta": {"title": "Test", "timeout": 5},
            "environments": [
                {
                    "id": "custom-env",
                    "display_name": "Experimental OS",
                    "type": "iso",
                    "boot_method": "experimental_recipe_v2",
                    "enabled": True,
                    "order": 1,
                    "iso_path": "ISO/test.iso",
                }
            ],
        }
        menu = self.generator.generate_menu_script(config)
        self.assertIn("[WARNING] Unrecognized boot method: experimental_recipe_v2", menu)
        # Verify valid GRUB syntax structure remains intact
        self.assertIn('menuentry "Experimental OS" --id "custom-env"', menu)

    def test_non_claim_of_bootability_disclaimer(self):
        """Verify that generated menu explicitly states registration does not imply verified bootability."""
        menu = self.generator.generate_menu_script(self.valid_config)
        self.assertIn("Note: Registration does not imply verified kernel bootability", menu)
        self.assertNotIn("Boot entry verified", menu)

    def test_empty_config_fallback_menu(self):
        """Verify that empty configuration produces fallback entry."""
        empty_config = {
            "version": "1.0.0",
            "meta": {"title": "Empty MVentX", "timeout": 10},
            "environments": [],
        }
        menu = self.generator.generate_menu_script(empty_config)
        self.assertIn('menuentry "No environments registered or enabled" --id "mventx-empty"', menu)
        self.assertIn("No environments are currently enabled in mventx.json", menu)

    def test_read_config_file_errors(self):
        """Verify handling of missing or corrupt JSON files."""
        nonexistent = self.root_path / "does_not_exist.json"
        with self.assertRaises(FileNotFoundError):
            GrubMenuGenerator.read_config_file(nonexistent)

        corrupt = self.root_path / "corrupt.json"
        corrupt.write_text("{ incomplete json: ", encoding="utf-8")
        with self.assertRaises(ConfigValidationError):
            GrubMenuGenerator.read_config_file(corrupt)


if __name__ == "__main__":
    unittest.main()

