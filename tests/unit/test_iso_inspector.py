"""Unit tests for ISO Inspector and Milestone 8 Distribution Compatibility Engine."""

from pathlib import Path
import tempfile
import unittest

from core.boot_config.generator import GrubMenuGenerator
from core.boot_config.recipes import (
    RECIPE_ARCHISO,
    RECIPE_CASPER,
    RECIPE_DRACUT,
    RECIPE_GENERIC_KERNEL,
    RECIPE_LIVE_BOOT,
    generate_loopback_boot_commands,
)
from core.iso_manager.iso_inspector import ISOInspector, InspectionResult
from core.iso_manager.metadata import ISOMetadata, ISOMetadataExtractor
from core.iso_manager.registration import ISORegistrationManager
from core.storage.storage_manager import StorageManager

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestISOInspector(unittest.TestCase):
    """Test suite for deep ISO inspection, recipe inference, and diagnostic warning generation."""

    @classmethod
    def setUpClass(cls):
        cls.scratch_dir = REPO_ROOT / "build" / "scratch"
        cls.clonezilla_iso = cls.scratch_dir / "clonezilla-live.iso"
        cls.focal_iso = cls.scratch_dir / "ubuntu_focal_mini.iso"
        cls.alpine_iso = cls.scratch_dir / "alpine_virt.iso"

    def test_nonexistent_iso_inspection(self):
        """Verify inspector gracefully handles missing files with uncertain confidence and warning."""
        inspector = ISOInspector("/nonexistent/path/to/missing.iso")
        res = inspector.inspect()
        self.assertEqual(res.confidence, "uncertain")
        self.assertTrue(len(res.warnings) > 0)
        self.assertIn("does not exist", res.warnings[0])

    def test_small_invalid_file_inspection(self):
        """Verify inspector rejects truncated or undersized files."""
        with tempfile.NamedTemporaryFile(suffix=".iso", delete=False) as f:
            f.write(b"NOT_AN_ISO_FILE_CONTENT")
            temp_path = Path(f.name)

        try:
            inspector = ISOInspector(temp_path)
            res = inspector.inspect()
            self.assertEqual(res.confidence, "uncertain")
            self.assertTrue(any("too small" in w.lower() for w in res.warnings))
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_clonezilla_live_inspection(self):
        """Verify deep inspection of Debian live-boot Clonezilla ISO."""
        if not self.clonezilla_iso.is_file():
            self.skipTest(f"Clonezilla test ISO not found at {self.clonezilla_iso}")

        inspector = ISOInspector(self.clonezilla_iso)
        res = inspector.inspect()

        self.assertEqual(res.detected_recipe, "live_boot")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.discovered_kernel_path, "/live/vmlinuz")
        self.assertIn(res.discovered_initrd_path, ("/live/initrd.img", "/live/initrd"))
        self.assertIn("/live", res.directories)

    def test_ubuntu_focal_mini_inspection(self):
        """Verify deep inspection of Ubuntu Focal Mini Netboot ISO."""
        if not self.focal_iso.is_file():
            self.skipTest(f"Ubuntu Focal Mini test ISO not found at {self.focal_iso}")

        inspector = ISOInspector(self.focal_iso)
        res = inspector.inspect()

        # Focal mini uses /linux and /initrd.gz (netboot structure)
        self.assertEqual(res.discovered_kernel_path, "/linux")
        self.assertEqual(res.discovered_initrd_path, "/initrd.gz")
        self.assertIn(res.detected_recipe, ("generic_kernel", "casper"))

    def test_alpine_virt_inspection(self):
        """Verify deep inspection of Alpine Linux Virtual ISO."""
        if not self.alpine_iso.is_file():
            self.skipTest(f"Alpine test ISO not found at {self.alpine_iso}")

        inspector = ISOInspector(self.alpine_iso)
        res = inspector.inspect()

        self.assertIn("/boot", res.directories)
        self.assertIsNotNone(res.discovered_kernel_path)
        self.assertIsNotNone(res.discovered_initrd_path)
        self.assertIn(res.detected_recipe, ("generic_kernel", "dracut", "live_boot"))

    def test_space_in_iso_path_handling(self):
        """Verify paths containing spaces are safely quoted in loopback commands."""
        cmds = generate_loopback_boot_commands(
            recipe=RECIPE_CASPER,
            iso_path="/ISO/Ubuntu 24.04 Desktop LTS.iso",
            custom_args="quiet splash",
        )
        joined = "\n".join(cmds)

        self.assertIn('set iso_file="(${mventx_data_dev})/ISO/Ubuntu 24.04 Desktop LTS.iso"', joined)
        self.assertIn('loopback loop0 "${iso_file}"', joined)
        self.assertIn('iso-scan/filename="/ISO/Ubuntu 24.04 Desktop LTS.iso"', joined)

    def test_grub_error_diagnostics_generation(self):
        """Verify loopback script contains robust diagnostics, loop cleanup, and countdown."""
        cmds = generate_loopback_boot_commands(
            recipe=RECIPE_LIVE_BOOT,
            iso_path="/ISO/debian-live.iso",
        )
        joined = "\n".join(cmds)

        self.assertIn("[BOOT ERROR] MVX_DATA Storage Partition Not Found", joined)
        self.assertIn("[BOOT ERROR] Registered ISO File Not Found", joined)
        self.assertIn("[BOOT ERROR] Kernel Binary Not Found Inside ISO", joined)
        self.assertIn("[BOOT ERROR] Initramfs Archive Not Found Inside ISO", joined)
        self.assertIn("loopback -d loop0", joined)
        self.assertIn("sleep 5", joined)

    def test_metadata_extractor_populates_inspection_fields(self):
        """Verify ISOMetadataExtractor integrates ISOInspector results."""
        if not self.clonezilla_iso.is_file():
            self.skipTest(f"Clonezilla test ISO not found at {self.clonezilla_iso}")

        meta = ISOMetadataExtractor.extract_metadata(self.clonezilla_iso)
        self.assertEqual(meta.detected_recipe, "live_boot")
        self.assertEqual(meta.recipe_confidence, "high")
        self.assertEqual(meta.discovered_kernel_path, "/live/vmlinuz")
        self.assertEqual(meta.discovered_initrd_path, "/live/initrd.img")

    def test_registration_with_custom_overrides(self):
        """Verify registration accepts explicit recipe, kernel path, and initrd path overrides."""
        temp_dir = Path(tempfile.mkdtemp(prefix="mventx_test_overrides_"))
        data_mount = temp_dir / "data"
        boot_mount = temp_dir / "boot"

        try:
            storage = StorageManager(data_mount=data_mount, boot_mount=boot_mount)
            storage.initialize_data_partition_structure()
            reg_mgr = ISORegistrationManager(storage)

            # Create mock valid ISO in data partition
            iso_file = data_mount / "ISO" / "custom_test.iso"
            with open(iso_file, "wb") as f:
                f.seek(16 * 2048)
                # Primary volume descriptor
                pvd = bytearray(2048)
                pvd[0] = 0x01
                pvd[1:6] = b"CD001"
                pvd[40:72] = b"CUSTOM_OS                       "
                f.write(pvd)
                f.seek(32 * 2048)
                f.write(b"\x00" * 2048)

            record = reg_mgr.register_iso(
                iso_path=iso_file,
                display_name="Custom Linux Image",
                recipe="dracut",
                kernel_path="/custom/vmlinuz-special",
                initrd_path="/custom/initramfs-special.img",
                custom_args="nomodeset test_arg=1",
            )

            self.assertEqual(record.recipe, "dracut")
            self.assertEqual(record.kernel_path, "/custom/vmlinuz-special")
            self.assertEqual(record.initrd_path, "/custom/initramfs-special.img")
            self.assertEqual(record.custom_args, "nomodeset test_arg=1")

            # Verify update_environment can change settings
            updated = reg_mgr.update_environment(
                env_id=record.id,
                display_name="Updated Custom Linux",
                recipe="live_boot",
                kernel_path="/live/vmlinuz",
                custom_args="quiet",
            )
            self.assertEqual(updated.display_name, "Updated Custom Linux")
            self.assertEqual(updated.recipe, "live_boot")
            self.assertEqual(updated.kernel_path, "/live/vmlinuz")
            self.assertEqual(updated.custom_args, "quiet")

        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

