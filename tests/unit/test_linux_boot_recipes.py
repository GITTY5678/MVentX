"""Unit tests for MVentX Linux Boot Recipes and Loopback Command Generator."""

import unittest

from core.boot_config.generator import GrubMenuGenerator
from core.boot_config.recipes import (
    RECIPE_ALPINE,
    RECIPE_ARCHISO,
    RECIPE_CASPER,
    RECIPE_DRACUT,
    RECIPE_GENERIC_KERNEL,
    RECIPE_LIVE_BOOT,
    RECIPE_MX_LINUX,
    RECIPE_OPENSUSE,
    RECIPE_SYSTEMRESCUE,
    detect_recipe_from_hints,
    generate_loopback_boot_commands,
    get_recipe,
)


class TestLinuxBootRecipes(unittest.TestCase):
    """Test suite for Linux boot recipe retrieval, argument formatting, and GRUB commands."""

    def test_recipe_lookup(self):
        """Verify recipe retrieval across aliases and fallback."""
        self.assertEqual(get_recipe("casper"), RECIPE_CASPER)
        self.assertEqual(get_recipe("ubuntu"), RECIPE_CASPER)
        self.assertEqual(get_recipe("linux_casper"), RECIPE_CASPER)

        self.assertEqual(get_recipe("archiso"), RECIPE_ARCHISO)
        self.assertEqual(get_recipe("arch"), RECIPE_ARCHISO)
        self.assertEqual(get_recipe("linux_archiso"), RECIPE_ARCHISO)

        self.assertEqual(get_recipe("dracut"), RECIPE_DRACUT)
        self.assertEqual(get_recipe("fedora"), RECIPE_DRACUT)
        self.assertEqual(get_recipe("linux_dracut"), RECIPE_DRACUT)

        self.assertEqual(get_recipe("live_boot"), RECIPE_LIVE_BOOT)
        self.assertEqual(get_recipe("debian"), RECIPE_LIVE_BOOT)
        self.assertEqual(get_recipe("clonezilla"), RECIPE_LIVE_BOOT)
        self.assertEqual(get_recipe("rescuezilla"), RECIPE_LIVE_BOOT)
        self.assertEqual(get_recipe("linux_live_boot"), RECIPE_LIVE_BOOT)

        self.assertEqual(get_recipe("opensuse"), RECIPE_OPENSUSE)
        self.assertEqual(get_recipe("suse"), RECIPE_OPENSUSE)
        self.assertEqual(get_recipe("tumbleweed"), RECIPE_OPENSUSE)
        self.assertEqual(get_recipe("leap"), RECIPE_OPENSUSE)

        self.assertEqual(get_recipe("mx_linux"), RECIPE_MX_LINUX)
        self.assertEqual(get_recipe("mx"), RECIPE_MX_LINUX)
        self.assertEqual(get_recipe("antix"), RECIPE_MX_LINUX)

        self.assertEqual(get_recipe("alpine"), RECIPE_ALPINE)
        self.assertEqual(get_recipe("alpine_linux"), RECIPE_ALPINE)

        self.assertEqual(get_recipe("systemrescue"), RECIPE_SYSTEMRESCUE)
        self.assertEqual(get_recipe("sysrescue"), RECIPE_SYSTEMRESCUE)

        self.assertEqual(get_recipe("zorin"), RECIPE_CASPER)
        self.assertEqual(get_recipe("elementary"), RECIPE_CASPER)

        self.assertEqual(get_recipe("unknown_custom"), RECIPE_GENERIC_KERNEL)

    def test_recipe_detection_from_hints(self):
        """Verify automatic detection of recipe from ISO filenames and volume IDs."""
        self.assertEqual(
            detect_recipe_from_hints("ubuntu-24.04-desktop-amd64.iso", "Ubuntu 24.04 LTS"),
            RECIPE_CASPER,
        )
        self.assertEqual(
            detect_recipe_from_hints("linuxmint-21.3-cinnamon-64bit.iso", None),
            RECIPE_CASPER,
        )
        self.assertEqual(
            detect_recipe_from_hints("Zorin-OS-17.1-Core-64-bit.iso", "Zorin OS"),
            RECIPE_CASPER,
        )
        self.assertEqual(
            detect_recipe_from_hints("archlinux-2026.03.01-x86_64.iso", "ARCH_202603"),
            RECIPE_ARCHISO,
        )
        self.assertEqual(
            detect_recipe_from_hints("systemrescue-11.00-amd64.iso", "RESCUE1100"),
            RECIPE_SYSTEMRESCUE,
        )
        self.assertEqual(
            detect_recipe_from_hints("openSUSE-Tumbleweed-KDE-Live-x86_64.iso", "openSUSE_Tumbleweed"),
            RECIPE_OPENSUSE,
        )
        self.assertEqual(
            detect_recipe_from_hints("MX-23.2_x64.iso", "MX-Live"),
            RECIPE_MX_LINUX,
        )
        self.assertEqual(
            detect_recipe_from_hints("alpine-standard-3.20.0-x86_64.iso", "alpine-std"),
            RECIPE_ALPINE,
        )
        self.assertEqual(
            detect_recipe_from_hints("rescuezilla-2.4.2-64bit.noble.iso", "Rescuezilla"),
            RECIPE_LIVE_BOOT,
        )
        self.assertEqual(
            detect_recipe_from_hints("Fedora-Workstation-Live-x86_64-40-1.14.iso", "Fedora-WS-Live-40"),
            RECIPE_DRACUT,
        )
        self.assertEqual(
            detect_recipe_from_hints("clonezilla-live-3.3.3-amd64.iso", "Clonezilla-live"),
            RECIPE_LIVE_BOOT,
        )
        self.assertEqual(
            detect_recipe_from_hints("debian-live-12.5.0-amd64-standard.iso", None),
            RECIPE_LIVE_BOOT,
        )
        self.assertEqual(
            detect_recipe_from_hints("custom-kernel.iso", "MY_VOLUME"),
            RECIPE_GENERIC_KERNEL,
        )

    def test_casper_argument_formatting(self):
        """Verify Casper recipe argument replacement."""
        args = RECIPE_CASPER.format_args(
            iso_path="/ISO/ubuntu-24.04.iso",
            custom_args="console=ttyS0",
            volume_id="MVX_DATA",
        )
        self.assertIn("iso-scan/filename=/ISO/ubuntu-24.04.iso", args)
        self.assertIn("boot=casper", args)
        self.assertIn("console=ttyS0", args)

    def test_archiso_argument_formatting(self):
        """Verify ArchISO recipe argument replacement."""
        args = RECIPE_ARCHISO.format_args(
            iso_path="/ISO/archlinux.iso",
            custom_args="nomodeset",
        )
        self.assertIn("img_dev=/dev/disk/by-label/MVX_DATA", args)
        self.assertIn("img_loop=/ISO/archlinux.iso", args)
        self.assertIn("nomodeset", args)

    def test_dracut_argument_formatting(self):
        """Verify Dracut recipe argument replacement."""
        args = RECIPE_DRACUT.format_args(
            iso_path="/ISO/fedora.iso",
            volume_id="Fedora-WS-40",
        )
        self.assertIn("root=live:CDLABEL=Fedora-WS-40", args)
        self.assertIn("iso-scan.filename=/ISO/fedora.iso", args)
        self.assertIn("rd.live.image", args)

    def test_opensuse_argument_formatting(self):
        """Verify openSUSE recipe argument replacement."""
        args = RECIPE_OPENSUSE.format_args(
            iso_path="/ISO/openSUSE.iso",
            volume_id="openSUSE_Live",
        )
        self.assertIn("root=live:CDLABEL=openSUSE_Live", args)
        self.assertIn("iso-scan.filename=/ISO/openSUSE.iso", args)
        self.assertIn("rd.live.image", args)

    def test_mx_linux_argument_formatting(self):
        """Verify MX Linux recipe argument replacement."""
        args = RECIPE_MX_LINUX.format_args(
            iso_path="/ISO/MX-23.iso",
        )
        self.assertIn("fromiso=/ISO/MX-23.iso", args)
        self.assertIn("bootdev=MVX_DATA", args)

    def test_alpine_argument_formatting(self):
        """Verify Alpine Linux recipe argument replacement."""
        args = RECIPE_ALPINE.format_args(
            iso_path="/ISO/alpine.iso",
        )
        self.assertIn("modloop=/boot/modloop-virt", args)
        self.assertIn("modules=loop,squashfs,sd-mod,usb-storage", args)

    def test_systemrescue_argument_formatting(self):
        """Verify SystemRescue recipe argument replacement."""
        args = RECIPE_SYSTEMRESCUE.format_args(
            iso_path="/ISO/systemrescue.iso",
        )
        self.assertIn("img_dev=/dev/disk/by-label/MVX_DATA", args)
        self.assertIn("img_loop=/ISO/systemrescue.iso", args)
        self.assertIn("archisobasedir=sysrescue", args)

    def test_generate_loopback_boot_commands(self):
        """Verify generated GRUB script lines contain all essential safety checks."""
        cmds = generate_loopback_boot_commands(
            recipe=RECIPE_CASPER,
            iso_path="ISO/ubuntu.iso",
            custom_args="quiet",
        )
        joined = "\n".join(cmds)
        self.assertIn("search --no-floppy --label --set=mventx_data_dev MVX_DATA", joined)
        self.assertIn('set iso_file="(${mventx_data_dev})/ISO/ubuntu.iso"', joined)
        self.assertIn("if [ ! -f ", joined)
        self.assertIn("loopback loop0 ", joined)
        self.assertIn("linux (loop0)/casper/vmlinuz iso-scan/filename=/ISO/ubuntu.iso", joined)
        self.assertIn("initrd (loop0)/casper/initrd", joined)
        self.assertIn("boot", joined)

    def test_overrides_in_loopback_commands(self):
        """Verify custom kernel and initrd path overrides take precedence."""
        cmds = generate_loopback_boot_commands(
            recipe=RECIPE_CASPER,
            iso_path="/ISO/custom.iso",
            kernel_path_override="/custom/vmlinuz-signed",
            initrd_path_override="/custom/initrd-signed.img",
        )
        joined = "\n".join(cmds)
        self.assertIn("linux (loop0)/custom/vmlinuz-signed", joined)
        self.assertIn("initrd (loop0)/custom/initrd-signed.img", joined)

    def test_menu_generator_casper_environment(self):
        """Verify GrubMenuGenerator renders Casper boot commands for an Ubuntu environment."""
        config = {
            "version": "1.0.0",
            "meta": {"title": "MVentX Test", "timeout": 10},
            "environments": [
                {
                    "id": "ubuntu-lts",
                    "display_name": "Ubuntu 24.04 LTS",
                    "type": "iso",
                    "iso_path": "ISO/ubuntu-24.04.iso",
                    "boot_method": "linux_casper",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }
        generator = GrubMenuGenerator()
        script = generator.generate_menu_script(config)
        self.assertIn('menuentry "Ubuntu 24.04 LTS" --id "ubuntu-lts"', script)
        self.assertIn("loopback loop0", script)
        self.assertIn("linux (loop0)/casper/vmlinuz", script)
        self.assertIn("initrd (loop0)/casper/initrd", script)
        self.assertIn("iso-scan/filename=/ISO/ubuntu-24.04.iso", script)

    def test_menu_generator_archiso_environment(self):
        """Verify GrubMenuGenerator renders ArchISO boot commands for an Arch environment."""
        config = {
            "version": "1.0.0",
            "meta": {"title": "MVentX Test", "timeout": 10},
            "environments": [
                {
                    "id": "arch-linux",
                    "display_name": "Arch Linux 2026",
                    "type": "iso",
                    "iso_path": "ISO/archlinux.iso",
                    "boot_method": "linux_archiso",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }
        generator = GrubMenuGenerator()
        script = generator.generate_menu_script(config)
        self.assertIn('menuentry "Arch Linux 2026" --id "arch-linux"', script)
        self.assertIn("linux (loop0)/arch/boot/x86_64/vmlinuz-linux", script)
        self.assertIn("initrd (loop0)/arch/boot/x86_64/initramfs-linux.img", script)
        self.assertIn("img_dev=/dev/disk/by-label/MVX_DATA", script)

    def test_menu_generator_dracut_environment(self):
        """Verify GrubMenuGenerator renders Dracut boot commands for a Fedora environment."""
        config = {
            "version": "1.0.0",
            "meta": {"title": "MVentX Test", "timeout": 10},
            "environments": [
                {
                    "id": "fedora-ws",
                    "display_name": "Fedora 40 Workstation",
                    "type": "iso",
                    "iso_path": "ISO/fedora-40.iso",
                    "boot_method": "linux_dracut",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }
        generator = GrubMenuGenerator()
        script = generator.generate_menu_script(config)
        self.assertIn('menuentry "Fedora 40 Workstation" --id "fedora-ws"', script)
        self.assertIn("linux (loop0)/images/pxeboot/vmlinuz", script)
        self.assertIn("initrd (loop0)/images/pxeboot/initrd.img", script)
        self.assertIn("rd.live.image", script)

    def test_menu_generator_live_boot_environment(self):
        """Verify GrubMenuGenerator renders Debian live-boot commands for a Clonezilla environment."""
        config = {
            "version": "1.0.0",
            "meta": {"title": "MVentX Test", "timeout": 10},
            "environments": [
                {
                    "id": "clonezilla",
                    "display_name": "Clonezilla Live",
                    "type": "iso",
                    "iso_path": "ISO/clonezilla.iso",
                    "boot_method": "linux_live_boot",
                    "enabled": True,
                    "order": 1,
                }
            ],
        }
        generator = GrubMenuGenerator()
        script = generator.generate_menu_script(config)
        self.assertIn('menuentry "Clonezilla Live" --id "clonezilla"', script)
        self.assertIn("linux (loop0)/live/vmlinuz", script)
        self.assertIn("initrd (loop0)/live/initrd.img", script)
        self.assertIn("boot=live findiso=/ISO/clonezilla.iso", script)


if __name__ == "__main__":
    unittest.main()

