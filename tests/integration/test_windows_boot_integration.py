"""Integration tests for Windows Boot Management and Configuration Pipeline (Milestone 11).

Tests end-to-end:
1. Windows environment registration with ISORegistrationManager.
2. Configuration schema compliance and atomic backup generation.
3. GRUB menu compilation with Windows chainloading script block.
4. Enable / Disable / Rename environment operations.
5. Non-destructive deregistration (staged Windows files are preserved).
"""

from pathlib import Path
import shutil
import tempfile
import unittest

from core.boot_config.generator import GrubMenuGenerator
from core.iso_manager.registration import ISORegistrationManager
from core.iso_manager.windows_media_manager import WindowsMediaManager
from core.storage.storage_manager import StorageManager


class TestWindowsBootIntegration(unittest.TestCase):
    """End-to-end integration test suite for Windows boot environment management."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_win_int_test_"))
        self.boot_mount = self.temp_dir / "boot"
        self.data_mount = self.temp_dir / "data"

        self.boot_mount.mkdir(parents=True)
        self.data_mount.mkdir(parents=True)

        # Create standard layout
        (self.boot_mount / "EFI" / "BOOT").mkdir(parents=True)
        (self.boot_mount / "boot" / "grub").mkdir(parents=True)

        (self.data_mount / "ISO").mkdir(parents=True)
        (self.data_mount / "config").mkdir(parents=True)
        (self.data_mount / "metadata").mkdir(parents=True)
        (self.data_mount / "backups").mkdir(parents=True)
        (self.data_mount / "windows").mkdir(parents=True)

        # Initialize base mventx.json
        base_config = {
            "version": "1.0.0",
            "meta": {
                "created_at": "2026-03-31T00:00:00Z",
                "last_modified": "2026-03-31T00:00:00Z",
            },
            "global_settings": {
                "timeout": 10,
                "default_entry": "",
                "theme": "mventx_classic",
            },
            "environments": [],
        }
        import json
        (self.data_mount / "config" / "mventx.json").write_text(json.dumps(base_config, indent=2))

        # Copy GRUB font if available
        repo_font = Path(__file__).resolve().parent.parent.parent / "boot" / "grub" / "fonts" / "unicode.pf2"
        if repo_font.exists():
            fonts_dir = self.boot_mount / "boot" / "grub" / "fonts"
            fonts_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo_font, fonts_dir / "unicode.pf2")

        self.storage_manager = StorageManager(
            boot_mount=self.boot_mount,
            data_mount=self.data_mount,
        )
        self.registration_manager = ISORegistrationManager(self.storage_manager)

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_end_to_end_windows_registration_and_menu_generation(self):
        """Register a Windows environment and verify atomic persistence and GRUB menu sync."""
        # 1. Stage synthetic Windows files on MVX_DATA
        win_folder = self.data_mount / "windows" / "win11_pro"
        (win_folder / "efi" / "boot").mkdir(parents=True)
        (win_folder / "sources").mkdir(parents=True)

        bootloader = win_folder / "efi" / "boot" / "bootx64.efi"
        bootloader.write_bytes(b"MOCK_WINDOWS_BOOTLOADER_EFI")
        (win_folder / "sources" / "boot.wim").write_bytes(b"MOCK_WINPE")
        (win_folder / "sources" / "install.wim").write_bytes(b"MOCK_INSTALL_WIM")

        # 2. Register via ISORegistrationManager
        record = self.registration_manager.register_windows_media(
            display_name="Windows 11 Professional (x64)",
            bootloader_path="/efi/boot/bootx64.efi",
            staged_subfolder="windows/win11_pro",
            description="Windows 11 Pro 24H2 Staged Media",
            enabled=True,
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.type, "windows")
        self.assertEqual(record.boot_method, "windows_extracted_media")
        self.assertEqual(record.staged_subfolder, "windows/win11_pro")
        self.assertEqual(record.bootloader_path, "/efi/boot/bootx64.efi")

        # 3. Verify mventx.json contains updated record
        config = self.storage_manager.load_configuration()
        envs = config.get("environments", [])
        self.assertEqual(len(envs), 1)
        self.assertEqual(envs[0]["display_name"], "Windows 11 Professional (x64)")
        self.assertEqual(envs[0]["staged_subfolder"], "windows/win11_pro")

        # 4. Verify timestamped backup was created in backups/
        backups = list((self.data_mount / "backups").glob("mventx_*.json"))
        self.assertTrue(len(backups) >= 1)

        # 5. Verify generated GRUB menu on MVX_BOOT
        menu_path = self.boot_mount / "boot" / "grub" / "mventx_menu.cfg"
        self.assertTrue(menu_path.is_file())
        menu_content = menu_path.read_text(encoding="utf-8")

        self.assertIn('menuentry "Windows 11 Professional (x64)"', menu_content)
        self.assertIn('chainloader "($win_root)/windows/win11_pro/efi/boot/bootx64.efi"', menu_content)
        self.assertIn('search --no-floppy --label --set=win_root MVX_DATA', menu_content)

    def test_windows_environment_lifecycle_operations(self):
        """Test rename, disable, re-enable, and remove operations on Windows media."""
        # Register
        rec = self.registration_manager.register_windows_media(
            display_name="Windows 10 Setup",
            bootloader_path="/efi/boot/bootx64.efi",
            staged_subfolder="windows/win10",
        )
        env_id = rec.id

        # Rename
        renamed = self.registration_manager.rename_environment(env_id, "Windows 10 Enterprise LTSC")
        self.assertEqual(renamed.display_name, "Windows 10 Enterprise LTSC")

        # Disable
        disabled = self.registration_manager.disable_environment(env_id)
        self.assertFalse(disabled.enabled)

        # Verify disabled entry is not active in GRUB menu
        menu_content = (self.boot_mount / "boot" / "grub" / "mventx_menu.cfg").read_text(encoding="utf-8")
        self.assertNotIn('menuentry "Windows 10 Enterprise LTSC"', menu_content)

        # Re-enable
        enabled = self.registration_manager.enable_environment(env_id)
        self.assertTrue(enabled.enabled)
        menu_content2 = (self.boot_mount / "boot" / "grub" / "mventx_menu.cfg").read_text(encoding="utf-8")
        self.assertIn('menuentry "Windows 10 Enterprise LTSC"', menu_content2)

    def test_non_destructive_deregistration(self):
        """Verify removing a Windows environment does NOT delete staged installation media."""
        win_folder = self.data_mount / "windows" / "win11_preserve"
        (win_folder / "efi" / "boot").mkdir(parents=True)
        bootloader = win_folder / "efi" / "boot" / "bootx64.efi"
        bootloader.write_bytes(b"CRITICAL_USER_DATA_DO_NOT_DELETE")

        rec = self.registration_manager.register_windows_media(
            display_name="Windows To Be Removed",
            bootloader_path="/efi/boot/bootx64.efi",
            staged_subfolder="windows/win11_preserve",
        )

        # Remove from registration
        removed = self.registration_manager.remove_environment(rec.id)
        self.assertTrue(removed)

        # Verify environment is gone from mventx.json
        cfg = self.storage_manager.load_configuration()
        self.assertEqual(len(cfg.get("environments", [])), 0)

        # CRITICAL POLICY: Staged media must NOT be deleted!
        self.assertTrue(bootloader.is_file(), "Deregistration violated data safety by deleting staged media!")
        self.assertEqual(bootloader.read_bytes(), b"CRITICAL_USER_DATA_DO_NOT_DELETE")


if __name__ == "__main__":
    unittest.main()

