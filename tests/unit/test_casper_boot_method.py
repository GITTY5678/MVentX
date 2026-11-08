"""Unit tests for Ubuntu Casper Boot Method Integration.

Validates:
1. Authoritative recipe registry and boot method descriptors (casper, archiso, live_boot, etc.).
2. Casper ISO structure inspection, kernel/initrd variants, and volume label fallback.
3. ISORegistrationManager automatic assignment of Casper boot method and manual overrides.
4. GRUB menu script generation and loopback boot command verification for Casper.
5. PySide6 AddEnvironmentScreen dropdown synchronization and auto-selection.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

# Offscreen platform for headless Qt tests
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QMessageBox

from core.boot_config.generator import GrubMenuGenerator
from core.boot_config.recipes import (
    RECIPE_CASPER,
    REGISTERED_RECIPES,
    SUPPORTED_BOOT_METHODS,
    generate_loopback_boot_commands,
    get_boot_method_descriptor,
    get_recipe,
    get_supported_boot_methods,
)
from core.iso_manager.iso_inspector import ISOInspector, InspectionResult
from core.iso_manager.metadata import ISOMetadataExtractor
from core.iso_manager.registration import ISORegistrationManager
from core.storage.storage_manager import StorageManager
from manager.main import create_app
from manager.services.device_service import DeviceService
from manager.services.iso_service import ISOService
from manager.ui.add_environment import AddEnvironmentScreen


def create_mock_casper_iso(
    output_path: Path,
    volume_id: str = "UBUNTU_26_04_1_LTS",
    system_id: str = "LINUX",
    publisher_id: str = "CANONICAL",
    kernel_name: str = "vmlinuz",
    initrd_name: str = "initrd",
    include_casper_dir: bool = True,
) -> None:
    """Create a minimal valid ISO 9660 image with optional /casper directory entries."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    SECTOR = 2048

    def add_rec(buf: bytearray, off: int, lba: int, length: int, flags: int, name: str) -> int:
        n_bytes = name.encode("latin-1")
        rec_len = 33 + len(n_bytes)
        if rec_len % 2 == 1:
            rec_len += 1
        buf[off] = rec_len
        struct.pack_into("<I", buf, off + 2, lba)
        struct.pack_into("<I", buf, off + 10, length)
        buf[off + 25] = flags
        buf[off + 32] = len(n_bytes)
        buf[off + 33 : off + 33 + len(n_bytes)] = n_bytes
        return off + rec_len

    with open(output_path, "wb") as f:
        # 16 sectors system area (32 KiB)
        f.write(b"\x00" * (16 * SECTOR))

        # Sector 16: Primary Volume Descriptor (PVD)
        pvd = bytearray(SECTOR)
        pvd[0] = 0x01
        pvd[1:6] = b"CD001"
        pvd[6] = 0x01
        pvd[8:40] = system_id.encode("latin-1")[:32].ljust(32)
        pvd[40:72] = volume_id.encode("latin-1")[:32].ljust(32)
        pvd[318:446] = publisher_id.encode("latin-1")[:128].ljust(128)

        # Root directory record at offset 156
        pvd[156] = 34
        struct.pack_into("<I", pvd, 158, 18)  # root at LBA 18
        struct.pack_into("<I", pvd, 166, SECTOR)
        pvd[181] = 0x02  # is_dir
        pvd[188] = 0x01
        pvd[189] = 0x00
        f.write(pvd)

        # Sector 17: Terminator
        term = bytearray(SECTOR)
        term[0] = 0xFF
        term[1:6] = b"CD001"
        term[6] = 0x01
        f.write(term)

        # Sector 18: Root Directory
        root = bytearray(SECTOR)
        off = 0
        off = add_rec(root, off, 18, SECTOR, 0x02, "\x00")
        off = add_rec(root, off, 18, SECTOR, 0x02, "\x01")
        if include_casper_dir:
            off = add_rec(root, off, 19, SECTOR, 0x02, "CASPER")
        f.write(root)

        if include_casper_dir:
            # Sector 19: /casper/ directory
            casper_dir = bytearray(SECTOR)
            off = 0
            off = add_rec(casper_dir, off, 19, SECTOR, 0x02, "\x00")
            off = add_rec(casper_dir, off, 18, SECTOR, 0x02, "\x01")
            off = add_rec(casper_dir, off, 20, 1024, 0x00, kernel_name)
            off = add_rec(casper_dir, off, 21, 1024, 0x00, initrd_name)
            f.write(casper_dir)

            # Sectors 20 and 21: Kernel and Initrd payload dummies
            f.write(b"MOCK_KERNEL" + b"\x00" * (SECTOR - 11))
            f.write(b"MOCK_INITRD" + b"\x00" * (SECTOR - 11))


class TestCasperRecipeRegistry(unittest.TestCase):
    """Test suite for the authoritative recipe and boot method registry."""

    def test_casper_present_in_supported_methods(self):
        """Verify casper is exposed via get_supported_boot_methods."""
        methods = get_supported_boot_methods()
        method_ids = [m.id for m in methods]
        self.assertIn("casper", method_ids)
        self.assertIn("linux_generic_loopback", method_ids)
        self.assertIn("archiso", method_ids)
        self.assertIn("live_boot", method_ids)

    def test_casper_descriptor_properties(self):
        """Verify the Casper boot method descriptor contains valid attributes."""
        desc = get_boot_method_descriptor("casper")
        self.assertIsNotNone(desc)
        self.assertEqual(desc.id, "casper")
        self.assertEqual(desc.recipe_id, "casper")
        self.assertEqual(desc.env_type, "iso")
        self.assertIn("Casper", desc.display_name)

    def test_casper_alias_resolution(self):
        """Verify distribution aliases resolve to the casper descriptor."""
        for alias in ("ubuntu", "mint", "linux_casper"):
            desc = get_boot_method_descriptor(alias)
            self.assertIsNotNone(desc, f"Failed resolving alias '{alias}'")
            self.assertEqual(desc.id, "casper")

    def test_registered_recipes_casper(self):
        """Verify REGISTERED_RECIPES contains casper mapping to RECIPE_CASPER."""
        recipe = get_recipe("casper")
        self.assertEqual(recipe.recipe_id, "casper")
        self.assertEqual(recipe.kernel_path, "/casper/vmlinuz")
        self.assertEqual(recipe.initrd_path, "/casper/initrd")
        self.assertIn("boot=casper", recipe.default_args)
        self.assertIn("iso-scan/filename=${iso_path}", recipe.default_args)


class TestCasperISODetection(unittest.TestCase):
    """Test suite for deep Casper ISO inspection and path discovery."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_standard_casper_structure(self):
        """Verify high confidence detection for standard /casper/vmlinuz and /casper/initrd."""
        iso_file = self.dir_path / "ubuntu-26.04.1-desktop-amd64.iso"
        create_mock_casper_iso(iso_file, kernel_name="vmlinuz", initrd_name="initrd")

        inspector = ISOInspector(iso_file)
        res = inspector.inspect()

        self.assertEqual(res.detected_recipe, "casper")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.discovered_kernel_path, "/casper/vmlinuz")
        self.assertEqual(res.discovered_initrd_path, "/casper/initrd")

    def test_detect_signed_kernel_and_compressed_initrd(self):
        """Verify detection handles vmlinuz.signed and initrd.lz/zst variations."""
        iso_file = self.dir_path / "ubuntu-custom.iso"
        create_mock_casper_iso(iso_file, kernel_name="vmlinuz.signed", initrd_name="initrd.lz")

        res = ISOInspector(iso_file).inspect()
        self.assertEqual(res.detected_recipe, "casper")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.discovered_kernel_path, "/casper/vmlinuz.signed")
        self.assertEqual(res.discovered_initrd_path, "/casper/initrd.lz")

    def test_detect_hwe_kernel_and_generic_initrd(self):
        """Verify detection handles hwe-vmlinuz and initrd.gz."""
        iso_file = self.dir_path / "ubuntu-hwe.iso"
        create_mock_casper_iso(iso_file, kernel_name="hwe-vmlinuz", initrd_name="initrd.gz")

        res = ISOInspector(iso_file).inspect()
        self.assertEqual(res.detected_recipe, "casper")
        self.assertEqual(res.discovered_kernel_path, "/casper/hwe-vmlinuz")
        self.assertEqual(res.discovered_initrd_path, "/casper/initrd.gz")

    def test_detect_casper_from_volume_hints_when_empty_dirs(self):
        """Verify volume label/publisher hints infer casper when directory tree has no files."""
        iso_file = self.dir_path / "ubuntu-hint-only.iso"
        create_mock_casper_iso(iso_file, volume_id="UBUNTU_26_04", include_casper_dir=False)

        res = ISOInspector(iso_file).inspect()
        self.assertEqual(res.detected_recipe, "casper")
        self.assertEqual(res.confidence, "medium")
        self.assertEqual(res.discovered_kernel_path, "/casper/vmlinuz")
        self.assertEqual(res.discovered_initrd_path, "/casper/initrd")

    def test_metadata_extractor_suggests_casper(self):
        """Verify ISOMetadataExtractor sets suggested_boot_method to 'casper'."""
        iso_file = self.dir_path / "ubuntu-desktop.iso"
        create_mock_casper_iso(iso_file)

        meta = ISOMetadataExtractor.extract_metadata(iso_file)
        self.assertEqual(meta.detected_recipe, "casper")
        self.assertEqual(meta.suggested_boot_method, "casper")
        self.assertEqual(meta.discovered_kernel_path, "/casper/vmlinuz")
        self.assertEqual(meta.discovered_initrd_path, "/casper/initrd")


class TestCasperRegistration(unittest.TestCase):
    """Test suite for registering Ubuntu environments via ISORegistrationManager."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.data_dir = self.root_path / "MVX_DATA"
        self.boot_dir = self.root_path / "MVX_BOOT"
        self.data_dir.mkdir()
        self.boot_dir.mkdir()
        (self.boot_dir / "boot" / "grub").mkdir(parents=True)

        self.storage = StorageManager(data_mount=self.data_dir, boot_mount=self.boot_dir)
        self.storage.initialize_data_partition_structure()
        self.reg_mgr = ISORegistrationManager(self.storage)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_automatic_casper_method_assignment(self):
        """Verify Ubuntu ISO is automatically assigned boot_method='casper' when unspecified."""
        iso_file = self.data_dir / "ISO" / "ubuntu-26.04.1-desktop-amd64.iso"
        create_mock_casper_iso(iso_file)

        rec = self.reg_mgr.register_iso(
            iso_path=iso_file,
            display_name="Ubuntu 26.04.1 LTS",
        )

        self.assertEqual(rec.boot_method, "casper")
        self.assertEqual(rec.recipe, "casper")
        self.assertEqual(rec.kernel_path, "/casper/vmlinuz")
        self.assertEqual(rec.initrd_path, "/casper/initrd")

        # Verify persisted configuration reflects casper
        config = self.storage.load_configuration()
        env_entry = next(e for e in config["environments"] if e["id"] == rec.id)
        self.assertEqual(env_entry["boot_method"], "casper")
        self.assertEqual(env_entry["recipe"], "casper")

    def test_manual_boot_method_override_preserved(self):
        """Verify user override to linux_generic_loopback is strictly preserved."""
        iso_file = self.data_dir / "ISO" / "ubuntu-custom.iso"
        create_mock_casper_iso(iso_file)

        rec = self.reg_mgr.register_iso(
            iso_path=iso_file,
            display_name="Ubuntu (Generic Loopback)",
            boot_method="linux_generic_loopback",
        )

        self.assertEqual(rec.boot_method, "linux_generic_loopback")
        self.assertEqual(rec.recipe, "generic_kernel")

        # Verify persisted configuration preserves the manual selection
        config = self.storage.load_configuration()
        env_entry = next(e for e in config["environments"] if e["id"] == rec.id)
        self.assertEqual(env_entry["boot_method"], "linux_generic_loopback")
        self.assertEqual(env_entry["recipe"], "generic_kernel")

    def test_explicit_kernel_and_initrd_override(self):
        """Verify user-specified kernel and initrd paths take precedence over auto-discovered paths."""
        iso_file = self.data_dir / "ISO" / "ubuntu-override.iso"
        create_mock_casper_iso(iso_file)

        rec = self.reg_mgr.register_iso(
            iso_path=iso_file,
            display_name="Ubuntu Override",
            boot_method="casper",
            kernel_path="/casper/vmlinuz.signed",
            initrd_path="/casper/initrd.lz",
        )

        self.assertEqual(rec.kernel_path, "/casper/vmlinuz.signed")
        self.assertEqual(rec.initrd_path, "/casper/initrd.lz")


class TestCasperGrubGeneration(unittest.TestCase):
    """Test suite for GRUB configuration script generation with Casper boot entries."""

    def test_loopback_boot_commands_casper(self):
        """Verify generated commands contain required Casper loopback parameters."""
        cmds = generate_loopback_boot_commands(
            recipe=RECIPE_CASPER,
            iso_path="ISO/ubuntu-26.04.1.iso",
            custom_args="splash",
        )
        joined = "\n".join(cmds)

        self.assertIn('set iso_file="(${mventx_data_dev})/ISO/ubuntu-26.04.1.iso"', joined)
        self.assertIn('loopback loop0 "${iso_file}"', joined)
        self.assertIn("linux (loop0)/casper/vmlinuz", joined)
        self.assertIn("initrd (loop0)/casper/initrd", joined)
        self.assertIn("iso-scan/filename=/ISO/ubuntu-26.04.1.iso", joined)
        self.assertIn("boot=casper", joined)
        self.assertIn("quiet splash", joined)
        self.assertIn("boot", joined)

    def test_menu_generator_recognizes_casper(self):
        """Verify GrubMenuGenerator generates clean menuentry for casper without warnings."""
        config = {
            "version": "1.0.0",
            "meta": {"title": "MVentX Test Menu", "timeout": 10},
            "environments": [
                {
                    "id": "ubuntu_26_04",
                    "display_name": "Ubuntu 26.04.1 LTS",
                    "type": "iso",
                    "boot_method": "casper",
                    "recipe": "casper",
                    "iso_path": "ISO/ubuntu.iso",
                    "kernel_path": "/casper/vmlinuz",
                    "initrd_path": "/casper/initrd",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }

        generator = GrubMenuGenerator()
        script = generator.generate_menu_script(config)

        self.assertIn('menuentry "Ubuntu 26.04.1 LTS" --id "ubuntu_26_04"', script)
        self.assertIn('echo "  Boot Method: casper"', script)
        self.assertNotIn("Unrecognized boot method: casper", script)
        self.assertIn("boot=casper", script)
        self.assertIn("linux (loop0)/casper/vmlinuz", script)


class TestCasperGUIIntegration(unittest.TestCase):
    """Test suite for PySide6 AddEnvironmentScreen with Casper boot method."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])

    def setUp(self):
        self.info_patcher = patch.object(QMessageBox, "information", return_value=QMessageBox.Ok)
        self.warn_patcher = patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok)
        self.info_patcher.start()
        self.warn_patcher.start()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.data_dir = self.root_path / "MVX_DATA"
        self.boot_dir = self.root_path / "MVX_BOOT"
        self.data_dir.mkdir()
        self.boot_dir.mkdir()
        (self.boot_dir / "boot" / "grub").mkdir(parents=True)
        (self.data_dir / "ISO").mkdir(parents=True)

        _, self.window = create_app(data_path=self.data_dir, boot_path=self.boot_dir)
        self.iso_service = self.window.iso_service
        self.screen = self.window.add_screen

    def tearDown(self):
        self.info_patcher.stop()
        self.warn_patcher.stop()
        self.temp_dir.cleanup()

    def test_method_combo_contains_casper(self):
        """Verify casper is exposed in the GUI Boot Method dropdown."""
        found = False
        for i in range(self.screen.method_combo.count()):
            data = self.screen.method_combo.itemData(i)
            if data == "casper":
                found = True
                break
        self.assertTrue(found, "casper not found in AddEnvironmentScreen method_combo")

    def test_validate_selection_auto_selects_casper(self):
        """Verify selecting an Ubuntu ISO automatically switches method_combo to casper."""
        iso_file = self.data_dir / "ISO" / "ubuntu-26.04.1-desktop-amd64.iso"
        create_mock_casper_iso(iso_file)

        self.screen.refresh_unregistered_list()

        # Check that method_combo is currently casper
        current_data = self.screen.method_combo.currentData()
        self.assertEqual(current_data, "casper")
        self.assertIn("casper", self.screen.val_details_lbl.text())
        self.assertIn("/casper/vmlinuz", self.screen.val_details_lbl.text())


if __name__ == "__main__":
    unittest.main()
