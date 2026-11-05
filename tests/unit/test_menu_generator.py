"""Unit tests for MVentX GRUB menu configuration generator."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.boot_config.generator import (
    ConfigSecurityError,
    ConfigValidationError,
    GrubMenuGenerator,
    sanitize_custom_args,
    sanitize_display_name,
    sanitize_id,
    sanitize_path,
)


class TestGrubMenuGenerator(unittest.TestCase):
    """Test suite for GrubMenuGenerator."""

    def setUp(self):
        self.generator = GrubMenuGenerator()
        self.test_dir = Path(tempfile.mkdtemp(prefix="mventx_test_"))

        self.sample_config = {
            "version": "1.0.0",
            "meta": {
                "title": "MVentX Test Menu",
                "theme": "mventx",
                "timeout": 15,
                "default_entry_id": "env-linux",
            },
            "environments": [
                {
                    "id": "env-linux",
                    "display_name": "Custom Linux 2026",
                    "type": "iso",
                    "iso_path": "isos/linux.iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 1,
                    "custom_args": "quiet splash",
                },
                {
                    "id": "env-rescue",
                    "display_name": "System Rescue Toolkit",
                    "type": "iso",
                    "iso_path": "isos/rescue.iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 2,
                },
                {
                    "id": "env-chainload",
                    "display_name": "Host Windows OS",
                    "type": "chainload",
                    "target_path": "/EFI/Microsoft/Boot/bootmgfw.efi",
                    "boot_method": "chainload_efi",
                    "enabled": False,
                    "order": 3,
                },
            ],
        }

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    def test_valid_configuration_multiple_entries(self):
        """Test generating menu script from valid configuration with multiple entries."""
        script = self.generator.generate_menu_script(self.sample_config)

        self.assertIn('set mventx_menu_title="MVentX Test Menu"', script)
        self.assertIn("set timeout=15", script)
        self.assertIn('set default="env-linux"', script)

        # Both enabled entries must appear
        self.assertIn('menuentry "Custom Linux 2026" --id "env-linux"', script)
        self.assertIn('menuentry "System Rescue Toolkit" --id "env-rescue"', script)

        # Check detail echo lines
        self.assertIn("ISO Path:    isos/linux.iso", script)
        self.assertIn("Arguments:   quiet splash", script)
        self.assertIn("Boot Method: linux_generic_loopback", script)

    def test_disabled_entries_excluded(self):
        """Test that disabled entries are not rendered as boot menu entries."""
        script = self.generator.generate_menu_script(self.sample_config)

        # env-chainload is enabled: False
        self.assertNotIn("Host Windows OS", script)
        self.assertNotIn('menuentry "Host Windows OS"', script)
        self.assertNotIn('id "env-chainload"', script)

    def test_empty_configuration(self):
        """Test that empty environments or all-disabled environments output fallback menu."""
        # Case 1: Empty list
        empty_config = {
            "version": "1.0.0",
            "meta": {"title": "Empty MVentX", "timeout": 10},
            "environments": [],
        }
        script_empty = self.generator.generate_menu_script(empty_config)
        self.assertIn('menuentry "No environments registered or enabled" --id "mventx-empty"', script_empty)
        self.assertIn("No active boot environments found", script_empty)

        # Case 2: All environments disabled
        all_disabled_config = {
            "version": "1.0.0",
            "meta": {"title": "Disabled MVentX", "timeout": 10},
            "environments": [
                {
                    "id": "disabled-1",
                    "display_name": "Disabled One",
                    "type": "iso",
                    "iso_path": "isos/one.iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": False,
                    "order": 1,
                }
            ],
        }
        script_disabled = self.generator.generate_menu_script(all_disabled_config)
        self.assertIn('menuentry "No environments registered or enabled" --id "mventx-empty"', script_disabled)
        self.assertNotIn("Disabled One", script_disabled)

    def test_custom_names(self):
        """Test custom user-defined display names are rendered cleanly."""
        custom_name = "My Super Custom Diagnostic Lab (v2.5) [Offline]"
        config = {
            "version": "1.0.0",
            "meta": {"title": "Custom Lab", "timeout": 5},
            "environments": [
                {
                    "id": "custom-lab",
                    "display_name": custom_name,
                    "type": "iso",
                    "iso_path": "isos/lab.iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }
        script = self.generator.generate_menu_script(config)
        self.assertIn(f'menuentry "{custom_name}" --id "custom-lab"', script)

    def test_invalid_or_missing_fields(self):
        """Test that invalid, missing, or malformed fields raise ConfigValidationError."""
        # Missing version
        bad_config = dict(self.sample_config)
        del bad_config["version"]
        with self.assertRaises(ConfigValidationError):
            self.generator.generate_menu_script(bad_config)

        # Missing meta
        bad_config2 = dict(self.sample_config)
        del bad_config2["meta"]
        with self.assertRaises(ConfigValidationError):
            self.generator.generate_menu_script(bad_config2)

        # Missing required field in environment
        bad_env = {
            "version": "1.0.0",
            "meta": {"title": "Title", "timeout": 5},
            "environments": [
                {
                    "id": "bad-env",
                    # missing display_name
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }
        with self.assertRaises(ConfigValidationError):
            self.generator.generate_menu_script(bad_env)

        # Duplicate ID
        dup_env = {
            "version": "1.0.0",
            "meta": {"title": "Title", "timeout": 5},
            "environments": [
                {
                    "id": "same-id",
                    "display_name": "First",
                    "type": "iso",
                    "iso_path": "isos/1.iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 1,
                },
                {
                    "id": "same-id",
                    "display_name": "Second",
                    "type": "iso",
                    "iso_path": "isos/2.iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 2,
                },
            ],
        }
        with self.assertRaises(ConfigValidationError):
            self.generator.generate_menu_script(dup_env)

    def test_unsafe_grub_values(self):
        """Test rejection and escaping of unsafe values to prevent GRUB script injection."""
        # Newline injection attempt
        with self.assertRaises(ConfigSecurityError):
            sanitize_display_name("Ubuntu\nmenuentry 'Injected' { reboot }")

        # Semicolon injection attempt
        with self.assertRaises(ConfigSecurityError):
            sanitize_display_name("Ubuntu; reboot;")

        # Brace injection attempt
        with self.assertRaises(ConfigSecurityError):
            sanitize_display_name("Ubuntu { reboot }")

        # Shell command substitution attempt
        with self.assertRaises(ConfigSecurityError):
            sanitize_display_name("Ubuntu $(reboot)")

        # Directory traversal in ISO path
        with self.assertRaises(ConfigSecurityError):
            sanitize_path("../../boot/grub/grub.cfg")

        # Dangerous custom arguments
        with self.assertRaises(ConfigSecurityError):
            sanitize_custom_args("quiet; reboot;")

        with self.assertRaises(ConfigSecurityError):
            sanitize_custom_args("quiet $(whoami)")

    def test_deterministic_output(self):
        """Test that generator produces identical output across calls and respects order."""
        # Run generator multiple times on the same configuration
        output1 = self.generator.generate_menu_script(self.sample_config)
        output2 = self.generator.generate_menu_script(self.sample_config)
        self.assertEqual(output1, output2, "Output must be byte-identical for identical input")

        # Reorder input list with different sequence in python list, but same order property
        config_scrambled = dict(self.sample_config)
        config_scrambled["environments"] = [
            self.sample_config["environments"][1],  # order 2
            self.sample_config["environments"][0],  # order 1
            self.sample_config["environments"][2],  # order 3 (disabled)
        ]

        output_scrambled = self.generator.generate_menu_script(config_scrambled)
        self.assertEqual(output1, output_scrambled, "Output must be deterministic based on order field")

        # Check that order 1 appears before order 2 in the output text
        pos1 = output1.index("Custom Linux 2026")
        pos2 = output1.index("System Rescue Toolkit")
        self.assertLess(pos1, pos2)

    def test_atomic_configuration_writing(self):
        """Test writing menu configuration atomically to disk."""
        target_file = self.test_dir / "boot" / "grub" / "mventx_menu.cfg"

        # Initial write
        self.generator.write_menu_file(self.sample_config, target_file)
        self.assertTrue(target_file.exists())

        content1 = target_file.read_text(encoding="utf-8")
        self.assertIn("Custom Linux 2026", content1)

        # Check no temporary files were left over
        parent_files = list(target_file.parent.glob("*.tmp"))
        self.assertEqual(len(parent_files), 0, "No temporary files should linger")

        # Overwrite atomically with updated config
        updated_config = dict(self.sample_config)
        updated_config["meta"] = {"title": "Updated Title", "timeout": 3}
        self.generator.write_menu_file(updated_config, target_file)

        content2 = target_file.read_text(encoding="utf-8")
        self.assertIn('set mventx_menu_title="Updated Title"', content2)
        self.assertIn("set timeout=3", content2)

    def test_read_config_file(self):
        """Test reading and parsing config JSON from disk."""
        json_file = self.test_dir / "mventx.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(self.sample_config, f)

        data = GrubMenuGenerator.read_config_file(json_file)
        self.assertEqual(data["version"], "1.0.0")
        self.assertEqual(len(data["environments"]), 3)


if __name__ == "__main__":
    unittest.main()

