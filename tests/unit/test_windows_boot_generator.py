"""Unit tests for Windows Boot Generator and GRUB Menu Generation (Milestone 11).

Tests GRUB 2 command generation for Windows chainloading, space escaping,
subfolder path resolution, and error screen fallbacks.
"""

import unittest

from core.boot_config.generator import GrubMenuGenerator
from core.boot_config.windows_boot_generator import (
    generate_windows_boot_commands,
    sanitize_windows_path,
)
from core.boot_config.windows_boot_strategy import WindowsBootStrategy


class TestWindowsBootGenerator(unittest.TestCase):
    """Test suite for Windows GRUB command generation."""

    def test_sanitize_windows_path(self):
        """Verify normalization of Windows relative and absolute paths."""
        self.assertEqual(sanitize_windows_path("efi/boot/bootx64.efi"), "/efi/boot/bootx64.efi")
        self.assertEqual(sanitize_windows_path("\\EFI\\Boot\\bootx64.efi"), "/EFI/Boot/bootx64.efi")
        self.assertEqual(sanitize_windows_path("/sources/boot.wim"), "/sources/boot.wim")
        self.assertEqual(sanitize_windows_path(""), "/")

    def test_standard_extracted_media_commands(self):
        """Verify command generation for standard extracted media on MVX_DATA."""
        lines = generate_windows_boot_commands(
            strategy=WindowsBootStrategy.EXTRACTED_MEDIA,
            bootloader_path="/efi/boot/bootx64.efi",
            staged_subfolder=None,
            data_partition_label="MVX_DATA",
        )
        script = "\n".join(lines)

        self.assertIn("search --no-floppy --label --set=win_root MVX_DATA", script)
        self.assertIn('if [ ! -f "($win_root)/efi/boot/bootx64.efi" ]; then', script)
        self.assertIn('chainloader "($win_root)/efi/boot/bootx64.efi"', script)
        self.assertIn("Expected bootloader not found at: ($win_root)/efi/boot/bootx64.efi", script)

    def test_staged_subfolder_commands(self):
        """Verify command generation when files reside in MVX_DATA/windows/<env_id>/."""
        lines = generate_windows_boot_commands(
            strategy=WindowsBootStrategy.EXTRACTED_MEDIA,
            bootloader_path="/efi/boot/bootx64.efi",
            staged_subfolder="windows/win11_pro",
            data_partition_label="MVX_DATA",
        )
        script = "\n".join(lines)

        self.assertIn('chainloader "($win_root)/windows/win11_pro/efi/boot/bootx64.efi"', script)

    def test_space_escaping_in_paths(self):
        """Verify paths containing spaces are safely quoted to prevent GRUB syntax errors."""
        lines = generate_windows_boot_commands(
            strategy=WindowsBootStrategy.EXTRACTED_MEDIA,
            bootloader_path="/efi/boot/bootx64.efi",
            staged_subfolder="windows/Windows 11 Setup Pro",
            data_partition_label="MY USB DATA",
        )
        script = "\n".join(lines)

        self.assertIn('search --no-floppy --label --set=win_root MY USB DATA', script)
        self.assertIn('chainloader "($win_root)/windows/Windows 11 Setup Pro/efi/boot/bootx64.efi"', script)

    def test_host_chainload_commands(self):
        """Verify host chainload commands search internal ESP for bootmgfw.efi."""
        lines = generate_windows_boot_commands(
            strategy=WindowsBootStrategy.HOST_CHAINLOAD,
            bootloader_path="/EFI/Microsoft/Boot/bootmgfw.efi",
        )
        script = "\n".join(lines)

        self.assertIn("search --no-floppy --file --set=host_esp /EFI/Microsoft/Boot/bootmgfw.efi", script)
        self.assertIn('chainloader "($host_esp)/EFI/Microsoft/Boot/bootmgfw.efi"', script)

    def test_grub_menu_generator_integration(self):
        """Verify GrubMenuGenerator compiles full menu with Windows environment entries."""
        generator = GrubMenuGenerator()
        config = {
            "version": "1.0.0",
            "meta": {
                "created_at": "2026-03-31T00:00:00Z",
                "last_modified": "2026-03-31T00:00:00Z",
            },
            "global_settings": {
                "timeout": 10,
                "default_entry": "win11",
                "theme": "mventx_classic",
            },
            "environments": [
                {
                    "id": "win11",
                    "display_name": "Windows 11 Installation Setup",
                    "type": "windows",
                    "boot_method": "windows_extracted_media",
                    "bootloader_path": "/efi/boot/bootx64.efi",
                    "enabled": True,
                    "order": 1,
                },
                {
                    "id": "ubuntu",
                    "display_name": "Ubuntu 24.04 LTS",
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "iso_path": "/ISO/ubuntu.iso",
                    "enabled": True,
                    "order": 2,
                },
            ],
        }

        script = generator.generate_menu_script(config)
        self.assertIn('menuentry "Windows 11 Installation Setup"', script)
        self.assertIn('chainloader "($win_root)/efi/boot/bootx64.efi"', script)
        self.assertIn('menuentry "Ubuntu 24.04 LTS"', script)
        self.assertIn('loopback loop0 "${iso_file}"', script)


if __name__ == "__main__":
    unittest.main()
