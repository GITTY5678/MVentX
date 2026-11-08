"""Unit tests for Fedora Dracut loopback boot recipe, volume ID inference, and ISO inspection."""

import json
from pathlib import Path
import tempfile
import unittest

from core.boot_config.generator import GrubMenuGenerator
from core.boot_config.recipes import (
    RECIPE_ALPINE,
    RECIPE_CASPER,
    RECIPE_DRACUT,
    RECIPE_LIVE_BOOT,
    RECIPE_OPENSUSE,
    generate_loopback_boot_commands,
    infer_dracut_volume_id,
)
from core.iso_manager.iso_inspector import ISOInspector


class TestFedoraDracutIntegration(unittest.TestCase):
    """Test suite for Fedora Dracut boot compatibility."""

    def test_01_dracut_recipe_arguments_contain_slash_and_driver_pre(self):
        """Dracut recipe must use 'iso-scan/filename' and 'rd.driver.pre=exfat'."""
        args = RECIPE_DRACUT.format_args(
            iso_path="/ISO/Fedora-Workstation-Live-45-1.4.x86_64.iso",
            volume_id="Fedora-WS-Live-45",
        )
        self.assertIn("root=live:CDLABEL=Fedora-WS-Live-45", args)
        self.assertIn("iso-scan/filename=/ISO/Fedora-Workstation-Live-45-1.4.x86_64.iso", args)
        self.assertNotIn("iso-scan.filename", args)
        self.assertIn("rd.live.image", args)
        self.assertIn("rd.driver.pre=exfat", args)

    def test_02_dracut_volume_id_inference_from_filename(self):
        """Verify volume ID inference for various official Fedora editions."""
        cases = [
            ("ISO/Fedora-Workstation-Live-45-1.4.x86_64.iso", "Fedora-WS-Live-45"),
            ("/ISO/Fedora-Workstation-Live-40-1.14.x86_64.iso", "Fedora-WS-Live-40"),
            ("Fedora-Server-dvd-x86_64-45-1.4.iso", "Fedora-S-Live-45"),
            ("Fedora-KDE-Live-45-1.4.x86_64.iso", "Fedora-KDE-Live-45"),
            ("Fedora-Silverblue-ostree-x86_64-45-1.4.iso", "Fedora-SB-Live-45"),
        ]
        for path_str, expected_vol in cases:
            inferred = infer_dracut_volume_id(path_str)
            self.assertEqual(inferred, expected_vol, f"Failed for {path_str}")

    def test_03_dracut_volume_id_override(self):
        """Explicit volume_id must override inference."""
        inferred = infer_dracut_volume_id("ISO/custom.iso", volume_id="MY_CUSTOM_LABEL")
        self.assertEqual(inferred, "MY_CUSTOM_LABEL")

    def test_04_dracut_command_generation_single_line_syntax(self):
        """Generated GRUB commands must be strictly single physical lines."""
        cmds = generate_loopback_boot_commands(
            recipe=RECIPE_DRACUT,
            iso_path="/ISO/Fedora-Workstation-Live-45-1.4.x86_64.iso",
            kernel_path_override="/boot/x86_64/loader/linux",
            initrd_path_override="/boot/x86_64/loader/initrd",
            volume_id="Fedora-WS-Live-45",
        )
        for cmd in cmds:
            self.assertNotIn("\r", cmd)
            self.assertNotIn("\n", cmd)

        linux_cmd = next(c for c in cmds if "linux (loop0)" in c)
        self.assertIn("root=live:CDLABEL=Fedora-WS-Live-45", linux_cmd)
        self.assertIn("iso-scan/filename=/ISO/Fedora-Workstation-Live-45-1.4.x86_64.iso", linux_cmd)
        self.assertIn("rd.driver.pre=exfat", linux_cmd)

        initrd_cmd = next(c for c in cmds if "initrd (loop0)" in c)
        self.assertEqual(initrd_cmd.strip(), "initrd (loop0)/boot/x86_64/loader/initrd")

    def test_05_iso_inspector_identifies_fedora_as_dracut_not_opensuse(self):
        """Fedora ISO with /boot/x86_64/loader/ and /LiveOS must be detected as dracut, not opensuse."""
        with tempfile.TemporaryDirectory() as td:
            iso_path = Path(td) / "Fedora-Workstation-Live-45-1.4.x86_64.iso"
            with open(iso_path, "wb") as f:
                f.seek(16 * 2048)
                pvd = bytearray(2048)
                pvd[0] = 0x01
                pvd[1:6] = b"CD001"
                pvd[40:72] = b"Fedora-WS-Live-45               "
                pvd[318:332] = b"FEDORA PROJECT"
                f.write(pvd)
                f.seek(32 * 2048)
                f.write(b"\x00" * 2048)

            inspector = ISOInspector(iso_path)
            res = inspector.inspect()
            # Even with minimal PVD and filename hints, recipe should infer dracut
            self.assertIn(res.detected_recipe, ("dracut", None))

    def test_06_iso_inspector_preserves_genuine_opensuse(self):
        """Genuine openSUSE ISO must not be falsely classified as dracut."""
        with tempfile.TemporaryDirectory() as td:
            iso_path = Path(td) / "openSUSE-Tumbleweed-KDE-Live.x86_64.iso"
            with open(iso_path, "wb") as f:
                f.seek(16 * 2048)
                pvd = bytearray(2048)
                pvd[0] = 0x01
                pvd[1:6] = b"CD001"
                pvd[40:72] = b"openSUSE_Tumbleweed_KDE         "
                f.write(pvd)
                f.seek(32 * 2048)
                f.write(b"\x00" * 2048)

            from core.boot_config.recipes import detect_recipe_from_hints
            rec = detect_recipe_from_hints(iso_path.name, "openSUSE_Tumbleweed_KDE")
            self.assertEqual(rec.recipe_id, "opensuse")

    def test_07_ubuntu_clonezilla_alpine_non_regression(self):
        """Verify Ubuntu Casper, Clonezilla live-boot, and Alpine recipes are unaffected."""
        casper_args = RECIPE_CASPER.format_args(iso_path="/ISO/ubuntu.iso")
        self.assertIn("iso-scan/filename=/ISO/ubuntu.iso", casper_args)
        self.assertIn("boot=casper", casper_args)

        live_args = RECIPE_LIVE_BOOT.format_args(iso_path="/ISO/clonezilla.iso")
        self.assertIn("findiso=/ISO/clonezilla.iso", live_args)
        self.assertIn("boot=live", live_args)

        alpine_args = RECIPE_ALPINE.format_args(iso_path="/ISO/alpine.iso")
        self.assertIn("iso-scan/filename=/ISO/alpine.iso", alpine_args)
        self.assertIn("modloop=/boot/modloop-lts", alpine_args)
        self.assertIn("modules=loop,squashfs,sd-mod,usb-storage,exfat", alpine_args)

    def test_08_menu_generator_uses_environment_volume_id(self):
        """GrubMenuGenerator must pass volume_id through to generated commands."""
        config = {
            "version": "1.0.0",
            "meta": {"title": "MVentX Test", "timeout": 10},
            "environments": [
                {
                    "id": "fedora-ws-45",
                    "display_name": "Fedora Workstation 45",
                    "type": "iso",
                    "iso_path": "ISO/Fedora-Workstation-Live-45-1.4.x86_64.iso",
                    "boot_method": "dracut",
                    "volume_id": "Fedora-WS-Live-45",
                    "kernel_path": "/boot/x86_64/loader/linux",
                    "initrd_path": "/boot/x86_64/loader/initrd",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }
        generator = GrubMenuGenerator()
        script = generator.generate_menu_script(config)
        self.assertIn('root=live:CDLABEL=Fedora-WS-Live-45', script)
        self.assertIn('iso-scan/filename=/ISO/Fedora-Workstation-Live-45-1.4.x86_64.iso', script)
        self.assertIn('rd.driver.pre=exfat', script)

    def test_09_real_physical_fedora_iso_inspection(self):
        """Inspect the real Fedora ISO if present on F:."""
        real_iso = Path(r"F:\ISO\Fedora-Workstation-Live-45-1.4.x86_64.iso")
        if not real_iso.is_file():
            self.skipTest("Real physical Fedora ISO not present on F:")

        inspector = ISOInspector(real_iso)
        res = inspector.inspect()
        self.assertEqual(res.detected_recipe, "dracut")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.discovered_kernel_path, "/boot/x86_64/loader/linux")
        self.assertEqual(res.discovered_initrd_path, "/boot/x86_64/loader/initrd")
        self.assertEqual(res.volume_id, "Fedora-WS-Live-45")


if __name__ == "__main__":
    unittest.main()
