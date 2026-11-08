"""Unit and integration tests for Alpine Linux 3.24.4 Boot Compatibility Repair.

Tests cover the 11 required engineering test cases:
1. Alpine kernel flavour detection (-lts vs -virt).
2. Matching vmlinuz-lts, initramfs-lts and modloop-lts.
3. Rejecting / preventing mismatched kernel/initramfs/modloop flavours.
4. Extracting exfat.ko from Alpine modloop (modloop-lts).
5. Validating vermagic against the Alpine kernel (6.18.60-0-lts) and Alpine Linux kernel key signature.
6. Extracting required dependency modules (nls_utf8.ko, nls_cp437.ko, nls_iso8859-1.ko, nls_ascii.ko, modules.dep, modules.alias).
7. Generating a valid Alpine overlay CPIO archive (synthetic and real Alpine 3.24.4 ISO).
8. Generating valid single-line GRUB commands (no split initrd or test lines, pure LF line endings).
9. Including exfat in Alpine's modules= boot parameter and iso-scan/filename + flavour-matched modloop=.
10. Regenerating and rolling back Alpine overlays safely via ISORegistrationManager.
11. Preserving existing Ubuntu (casper) and Clonezilla (live_boot) entries completely untouched.
"""

from __future__ import annotations

import gzip
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from core.boot_config.generator import GrubMenuGenerator
from core.boot_config.recipes import (
    generate_loopback_boot_commands,
    get_recipe,
    infer_alpine_modloop_path,
)
from core.iso_manager.iso_inspector import ISOInspector
from core.iso_manager.kernel_module_inspector import KernelModuleInspector
from core.iso_manager.overlay_generator import (
    InitramfsOverlayGenerator,
    OverlayGenerationError,
    OverlayGenerationResult,
    create_newc_cpio,
    find_seven_zip,
    parse_newc_cpio_entries,
)
from core.iso_manager.registration import (
    ISORegistrationManager,
    MenuSyncRollbackError,
    RegistrationError,
)
from core.storage.storage_manager import StorageManager
from tests.unit.test_dynamic_overlay import build_synthetic_elf_ko, build_synthetic_vmlinuz
from tests.unit.test_iso_validation import make_mock_iso


def make_alpine_iso(
    output_path: Path,
    boot_files: list[str],
    volume_id: str = "ALPINE-STD 3.24.4 X86_64",
    system_id: str = "LINUX",
    publisher_id: str = "ALPINE",
) -> None:
    """Create a valid ISO 9660 image with a /boot directory containing the specified filenames."""
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
        # 16 sectors system area
        f.write(b"\x00" * (16 * SECTOR))

        # Sector 16: Primary Volume Descriptor (PVD)
        pvd = bytearray(SECTOR)
        pvd[0] = 0x01
        pvd[1:6] = b"CD001"
        pvd[6] = 0x01
        pvd[8:40] = system_id.encode("latin-1")[:32].ljust(32)
        pvd[40:72] = volume_id.encode("latin-1")[:32].ljust(32)
        pvd[318:446] = publisher_id.encode("latin-1")[:128].ljust(128)

        # Root directory record at offset 156 -> LBA 18
        pvd[156] = 34
        struct.pack_into("<I", pvd, 158, 18)
        struct.pack_into("<I", pvd, 166, SECTOR)
        pvd[181] = 0x02
        pvd[188] = 0x01
        pvd[189] = 0x00
        f.write(pvd)

        # Sector 17: Terminator
        term = bytearray(SECTOR)
        term[0] = 0xFF
        term[1:6] = b"CD001"
        term[6] = 0x01
        f.write(term)

        # Sector 18: Root Directory containing /BOOT (LBA 19)
        root = bytearray(SECTOR)
        off = 0
        off = add_rec(root, off, 18, SECTOR, 0x02, "\x00")
        off = add_rec(root, off, 18, SECTOR, 0x02, "\x01")
        off = add_rec(root, off, 19, SECTOR, 0x02, "BOOT")
        f.write(root)

        # Sector 19: /boot directory
        boot_dir = bytearray(SECTOR)
        off = 0
        off = add_rec(boot_dir, off, 19, SECTOR, 0x02, "\x00")
        off = add_rec(boot_dir, off, 18, SECTOR, 0x02, "\x01")
        next_lba = 20
        for fname in boot_files:
            off = add_rec(boot_dir, off, next_lba, 1024, 0x00, fname.upper())
            next_lba += 1
        f.write(boot_dir)

        for _ in boot_files:
            f.write(b"MOCK_DATA" + b"\x00" * (SECTOR - 9))


def KernelModuleInfo_from_vermagic(vermagic: str):
    elf_bytes = build_synthetic_elf_ko(
        modinfo_dict={
            "name": "exfat",
            "vermagic": vermagic,
            "license": "GPL",
            "alias": "fs-exfat",
        },
        signature_signer="Alpine Linux kernel key",
    )
    return KernelModuleInspector.inspect_elf_module(elf_bytes)


class TestAlpineFlavourDetectionAndMatching(unittest.TestCase):
    """Test cases 1, 2, 3: Alpine kernel/initramfs/modloop flavour detection and matching."""

    def test_1_alpine_kernel_flavour_detection_lts_and_virt(self):
        """1. Detect Alpine kernel flavour (-lts vs -virt) from kernel/initrd paths."""
        self.assertEqual(
            infer_alpine_modloop_path("/boot/vmlinuz-lts", "/boot/initramfs-lts"),
            "/boot/modloop-lts",
        )
        self.assertEqual(
            infer_alpine_modloop_path("/boot/vmlinuz-virt", "/boot/initramfs-virt"),
            "/boot/modloop-virt",
        )
        self.assertEqual(
            infer_alpine_modloop_path("/boot/vmlinuz-edge", "/boot/initramfs-edge"),
            "/boot/modloop-edge",
        )

    def test_2_matching_vmlinuz_lts_initramfs_lts_and_modloop_lts(self):
        """2. ISOInspector matches vmlinuz-lts, initramfs-lts, and modloop-lts on Alpine Standard ISOs."""
        with tempfile.TemporaryDirectory() as td:
            iso_p = Path(td) / "alpine-standard-3.24.4-x86_64.iso"
            make_alpine_iso(
                iso_p,
                boot_files=["vmlinuz-lts", "initramfs-lts", "modloop-lts", "System.map-lts", "config-lts"],
            )
            res = ISOInspector(iso_p).inspect()
            self.assertEqual(res.detected_recipe, "alpine")
            self.assertEqual(res.confidence, "high")
            self.assertEqual(res.kernel_path, "/boot/vmlinuz-lts")
            self.assertEqual(res.initrd_path, "/boot/initramfs-lts")
            self.assertEqual(infer_alpine_modloop_path(res.kernel_path, res.initrd_path), "/boot/modloop-lts")

    def test_3_rejecting_mismatched_kernel_initramfs_modloop_flavours(self):
        """3. ISOInspector refuses to pair mismatched kernel/initramfs/modloop flavours when a complete triplet exists."""
        with tempfile.TemporaryDirectory() as td:
            iso_p = Path(td) / "alpine-virt-3.24.4-x86_64.iso"
            # Contains an incomplete vmlinuz-lts (no initramfs-lts or modloop-lts) alongside complete -virt triplet
            make_alpine_iso(
                iso_p,
                boot_files=["vmlinuz-lts", "vmlinuz-virt", "initramfs-virt", "modloop-virt"],
                volume_id="ALPINE-VIRT 3.24.4 X86_64",
            )
            res = ISOInspector(iso_p).inspect()
            self.assertEqual(res.detected_recipe, "alpine")
            # Must select the complete -virt triplet and NOT pair vmlinuz-lts with initramfs-virt
            self.assertEqual(res.kernel_path, "/boot/vmlinuz-virt")
            self.assertEqual(res.initrd_path, "/boot/initramfs-virt")
            self.assertEqual(infer_alpine_modloop_path(res.kernel_path, res.initrd_path), "/boot/modloop-virt")

        # Also verify KernelModuleInspector rejects a module compiled for -virt against an -lts kernel
        virt_mod = KernelModuleInfo_from_vermagic("6.18.60-0-virt SMP preempt mod_unload modversions")
        ok, reasons = KernelModuleInspector.verify_module_compatibility(virt_mod, "6.18.60-0-lts")
        self.assertFalse(ok)
        self.assertTrue(any("mismatch" in r.lower() for r in reasons))


class TestAlpineModuleExtractionAndOverlayGeneration(unittest.TestCase):
    """Test cases 4, 5, 6, 7: Alpine modloop extraction, vermagic validation, dependencies, and CPIO overlay."""

    def test_4_5_6_7_synthetic_alpine_modloop_extraction_and_cpio_generation(self):
        """4–7. Extract exfat.ko and dependencies from modloop-lts, validate vermagic, and build Alpine CPIO overlay."""
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            iso_dummy = td_p / "alpine-standard-3.24.4-x86_64.iso"
            iso_dummy.write_bytes(b"DUMMY_ALPINE_ISO")
            output_cpio = td_p / "alpine-standard-3_24_4-x86_64.cpio"

            # Build synthetic 6.18.60-0-lts exfat.ko signed with Alpine Linux kernel key
            exfat_ko = build_synthetic_elf_ko(
                modinfo_dict={
                    "name": "exfat",
                    "vermagic": "6.18.60-0-lts SMP preempt mod_unload modversions",
                    "license": "GPL",
                    "alias": "fs-exfat",
                },
                signature_signer="Alpine Linux kernel key",
            )
            nls_utf8 = build_synthetic_elf_ko(
                modinfo_dict={
                    "name": "nls_utf8",
                    "vermagic": "6.18.60-0-lts SMP preempt mod_unload modversions",
                    "license": "Dual BSD/GPL",
                },
                signature_signer="Alpine Linux kernel key",
            )
            nls_cp437 = build_synthetic_elf_ko(
                modinfo_dict={
                    "name": "nls_cp437",
                    "vermagic": "6.18.60-0-lts SMP preempt mod_unload modversions",
                    "license": "Dual BSD/GPL",
                },
                signature_signer="Alpine Linux kernel key",
            )

            # Build a synthetic gzip-compressed initramfs-lts containing usr/sbin/nlplug-findfs
            initramfs_cpio = create_newc_cpio([
                ("usr", 0o040755, b""),
                ("usr/sbin", 0o040755, b""),
                ("usr/sbin/nlplug-findfs", 0o100755, b"\x7fELF_STOCK_NLPLUG_FINDFS_BINARY"),
            ])
            initramfs_gz = gzip.compress(initramfs_cpio)

            def fake_subprocess_run(cmd, *args, **kwargs):
                cmd_str = " ".join(str(c) for c in cmd)
                out_args = [arg for arg in cmd if str(arg).startswith("-o")]
                out_dir = Path(str(out_args[0])[2:]) if out_args else td_p

                if "vmlinuz" in cmd_str:
                    (out_dir / "vmlinuz-lts").write_bytes(build_synthetic_vmlinuz("6.18.60-0-lts"))
                elif "initramfs" in cmd_str:
                    (out_dir / "initramfs-lts").write_bytes(initramfs_gz)
                elif "*exfat.ko*" in cmd_str:
                    (out_dir / "exfat.ko").write_bytes(exfat_ko)
                elif "*nls_utf8.ko*" in cmd_str:
                    (out_dir / "nls_utf8.ko").write_bytes(nls_utf8)
                    (out_dir / "nls_cp437.ko").write_bytes(nls_cp437)
                    (out_dir / "modules.dep").write_bytes(b"kernel/fs/exfat/exfat.ko:\n")
                    (out_dir / "modules.dep.bin").write_bytes(b"DEPBIN")
                    (out_dir / "modules.alias").write_bytes(b"alias fs-exfat exfat\n")
                    (out_dir / "modules.alias.bin").write_bytes(b"ALIASBIN")
                elif "modloop" in cmd_str:
                    (out_dir / "modloop-lts").write_bytes(b"MOCK_MODLOOP_SQUASHFS")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            dummy_7z = td_p / "fake_7z.exe"
            dummy_7z.write_bytes(b"FAKE_EXE")

            with patch("core.iso_manager.overlay_generator.find_seven_zip", return_value=dummy_7z), \
                 patch("subprocess.run", side_effect=fake_subprocess_run):
                result = InitramfsOverlayGenerator.generate_exfat_overlay(
                    iso_path=iso_dummy,
                    output_cpio_path=output_cpio,
                    recipe="alpine",
                    kernel_path="/boot/vmlinuz-lts",
                    initrd_path="/boot/initramfs-lts",
                )

            # Verify result metadata (Test cases 4, 5, 6)
            self.assertTrue(result.success)
            self.assertEqual(result.kernel_version, "6.18.60-0-lts")
            self.assertIsNotNone(result.module_info)
            self.assertEqual(result.module_info.name, "exfat")
            self.assertEqual(result.module_info.kernel_version, "6.18.60-0-lts")
            self.assertTrue(result.module_info.has_signature)
            self.assertEqual(result.module_info.signer, "Alpine Linux kernel key")
            self.assertEqual(result.warnings, [])
            self.assertIn("exfat.ko", result.extracted_modules)
            self.assertIn("nls_utf8.ko", result.extracted_modules)
            self.assertIn("nls_cp437.ko", result.extracted_modules)

            # Verify CPIO archive structure (Test case 7)
            cpio_bytes = output_cpio.read_bytes()
            self.assertEqual(len(cpio_bytes) % 512, 0)
            self.assertEqual(cpio_bytes[:6], b"070701")

            entries = parse_newc_cpio_entries(cpio_bytes)
            self.assertIn("usr/lib/modules/6.18.60-0-lts/kernel/fs/exfat/exfat.ko", entries)
            self.assertIn("usr/lib/modules/6.18.60-0-lts/kernel/fs/nls/nls_utf8.ko", entries)
            self.assertIn("usr/lib/modules/6.18.60-0-lts/kernel/fs/nls/nls_cp437.ko", entries)
            self.assertIn("usr/lib/modules/6.18.60-0-lts/modules.dep", entries)
            self.assertIn("usr/lib/modules/6.18.60-0-lts/modules.dep.bin", entries)
            self.assertIn("usr/lib/modules/6.18.60-0-lts/modules.alias", entries)
            self.assertIn("usr/lib/modules/6.18.60-0-lts/modules.alias.bin", entries)
            self.assertIn("usr/sbin/mdev", entries)
            self.assertIn("usr/sbin/nlplug-findfs", entries)
            self.assertIn("usr/sbin/nlplug-findfs.real", entries)
            self.assertEqual(entries["usr/sbin/nlplug-findfs.real"], b"\x7fELF_STOCK_NLPLUG_FINDFS_BINARY")

            # Must NOT contaminate Alpine initramfs with Ubuntu Casper scripts
            self.assertNotIn("scripts/casper-premount/00_mventx_exfat", entries)
            self.assertNotIn("conf/conf.d/zz_mventx_exfat.conf", entries)

    def test_7_real_alpine_iso_overlay_generation_if_present(self):
        """7. Verify end-to-end overlay generation against the real F:\\ISO\\alpine-standard-3.24.4-x86_64.iso."""
        real_iso = Path(r"F:\ISO\alpine-standard-3.24.4-x86_64.iso")
        tool_7z = find_seven_zip()
        if not real_iso.is_file() or not tool_7z:
            self.skipTest("Physical Alpine 3.24.4 ISO or 7-Zip not available.")

        with tempfile.TemporaryDirectory() as td:
            out_cpio = Path(td) / "alpine-standard-3_24_4-x86_64.cpio"
            result = InitramfsOverlayGenerator.generate_exfat_overlay(
                iso_path=real_iso,
                output_cpio_path=out_cpio,
                recipe="alpine",
                kernel_path="/boot/vmlinuz-lts",
                initrd_path="/boot/initramfs-lts",
            )
            self.assertTrue(result.success)
            self.assertEqual(result.kernel_version, "6.18.60-0-lts")
            self.assertEqual(result.module_info.vermagic, "6.18.60-0-lts SMP preempt mod_unload modversions")
            self.assertTrue(result.module_info.has_signature)
            self.assertEqual(result.module_info.signer, "Alpine Linux kernel key")
            self.assertEqual(result.warnings, [])
            self.assertIn("exfat.ko", result.extracted_modules)
            self.assertIn("nls_utf8.ko", result.extracted_modules)
            self.assertIn("nls_cp437.ko", result.extracted_modules)
            self.assertIn("nls_iso8859-1.ko", result.extracted_modules)
            self.assertIn("nls_ascii.ko", result.extracted_modules)

            entries = parse_newc_cpio_entries(out_cpio.read_bytes())
            self.assertIn("usr/lib/modules/6.18.60-0-lts/kernel/fs/exfat/exfat.ko", entries)
            self.assertIn("usr/sbin/nlplug-findfs.real", entries)
            self.assertEqual(entries["usr/sbin/nlplug-findfs.real"][:4], b"\x7fELF")


class TestAlpineGrubAndBootParameters(unittest.TestCase):
    """Test cases 8, 9: Single-line GRUB commands and Alpine boot arguments."""

    def test_8_9_alpine_grub_commands_and_boot_parameters(self):
        """8 & 9. Verify single-line GRUB syntax, exfat in modules=, iso-scan/filename, and modloop-lts."""
        recipe = get_recipe("alpine")
        commands = generate_loopback_boot_commands(
            recipe=recipe,
            iso_path="/ISO/alpine-standard-3.24.4-x86_64.iso",
            kernel_path_override="/boot/vmlinuz-lts",
            initrd_path_override="/boot/initramfs-lts",
            overlay_path="boot/mventx/overlays/alpine-standard-3_24_4-x86_64.cpio",
        )

        # Every command must be a single line (no embedded CR or LF)
        for cmd in commands:
            self.assertNotIn("\r", cmd)
            self.assertNotIn("\n", cmd)

        stripped = [c.strip() for c in commands]
        linux_lines = [c for c in stripped if c.startswith("linux (loop0)/boot/vmlinuz-lts ")]
        self.assertEqual(len(linux_lines), 1)
        linux_cmd = linux_lines[0]

        # Test case 9: modules= includes exfat, modloop=/boot/modloop-lts, iso-scan/filename=...
        self.assertIn("modules=loop,squashfs,sd-mod,usb-storage,exfat", linux_cmd)
        self.assertIn("modloop=/boot/modloop-lts", linux_cmd)
        self.assertNotIn("modloop-virt", linux_cmd)
        self.assertIn("iso-scan/filename=/ISO/alpine-standard-3.24.4-x86_64.iso", linux_cmd)

        # Test case 8: Single-line initrd command and overlay existence check
        self.assertIn(
            'if [ -f "(${mventx_boot_dev})/boot/mventx/overlays/alpine-standard-3_24_4-x86_64.cpio" ]; then',
            stripped,
        )
        self.assertIn(
            "initrd (loop0)/boot/initramfs-lts (${mventx_boot_dev})/boot/mventx/overlays/alpine-standard-3_24_4-x86_64.cpio",
            stripped,
        )


class TestAlpineRegistrationRollbackAndPreservation(unittest.TestCase):
    """Test cases 10, 11: Regenerating/rolling back Alpine overlays safely and preserving Ubuntu & Clonezilla."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)
        self.data_mount = self.root_path / "MVX_DATA"
        self.boot_mount = self.root_path / "MVX_BOOT"
        self.data_mount.mkdir()
        self.boot_mount.mkdir()
        (self.boot_mount / "boot" / "grub").mkdir(parents=True)
        (self.boot_mount / "boot" / "mventx" / "overlays").mkdir(parents=True)

        self.storage_manager = StorageManager(
            data_mount=self.data_mount,
            boot_mount=self.boot_mount,
        )
        self.reg_mgr = ISORegistrationManager(self.storage_manager)
        self.iso_dir = self.reg_mgr.iso_directory

        # Create mock ISOs for Ubuntu, Clonezilla, and Alpine
        self.ubuntu_iso = self.iso_dir / "ubuntu-26.04.1-desktop-amd64.iso"
        self.clonezilla_iso = self.iso_dir / "clonezilla-live-3.3.3-37-amd64.iso"
        self.alpine_iso = self.iso_dir / "alpine-standard-3.24.4-x86_64.iso"
        for p, vol in [
            (self.ubuntu_iso, "UBUNTU_26_04"),
            (self.clonezilla_iso, "CLONEZILLA"),
            (self.alpine_iso, "ALPINE_3_24_4"),
        ]:
            make_mock_iso(p, volume_id=vol, system_id="LINUX", publisher_id="TEST")

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.generate_exfat_overlay")
    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.can_generate_overlay", return_value=True)
    def test_10_11_regenerate_and_rollback_alpine_while_preserving_ubuntu_and_clonezilla(self, mock_can, mock_gen):
        """10 & 11. Verify Alpine overlay regeneration, rollback on failure, and 100% preservation of Ubuntu & Clonezilla."""
        valid_cpio = create_newc_cpio([("etc/modules", 0o100644, b"exfat\n")])

        def fake_generate(iso_path, output_cpio_path, **kwargs):
            out_p = Path(output_cpio_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_bytes(valid_cpio)
            return OverlayGenerationResult(
                success=True,
                cpio_path=out_p,
                kernel_version="6.18.60-0-lts",
                file_size_bytes=len(valid_cpio),
            )

        mock_gen.side_effect = fake_generate

        # Register Ubuntu (casper) and Clonezilla (live_boot)
        ubuntu_rec = self.reg_mgr.register_iso(
            iso_path=self.ubuntu_iso,
            display_name="Ubuntu 26.04.1 Lts Amd64",
            boot_method="casper",
            kernel_path="/casper/vmlinuz",
            initrd_path="/casper/initrd",
        )
        clonezilla_rec = self.reg_mgr.register_iso(
            iso_path=self.clonezilla_iso,
            display_name="Clonezilla Live 3.3.3-37 Amd64",
            boot_method="live_boot",
            kernel_path="/live/vmlinuz",
            initrd_path="/live/initrd.img",
        )

        ubuntu_snapshot = ubuntu_rec.to_dict().copy()
        clonezilla_snapshot = clonezilla_rec.to_dict().copy()

        # Register Alpine
        alpine_rec = self.reg_mgr.register_iso(
            iso_path=self.alpine_iso,
            display_name="Alpine Standard 3.24.4 x86_64",
            boot_method="alpine",
            kernel_path="/boot/vmlinuz-lts",
            initrd_path="/boot/initramfs-lts",
        )

        self.assertEqual(alpine_rec.recipe, "alpine")
        self.assertEqual(alpine_rec.overlay_path, "boot/mventx/overlays/alpine-standard-3_24_4-x86_64.cpio")
        alpine_cpio_file = self.boot_mount / alpine_rec.overlay_path
        self.assertTrue(alpine_cpio_file.is_file())

        # Check overlay status is ready
        status = self.reg_mgr.get_overlay_status(alpine_rec.id)
        self.assertTrue(status.requires_overlay)
        self.assertEqual(status.state, "ready")

        # Simulate failed regeneration: must preserve existing CPIO and config
        original_cpio_bytes = alpine_cpio_file.read_bytes()
        mock_gen.side_effect = OverlayGenerationError("Simulated modloop read error")
        with self.assertRaises(RegistrationError):
            self.reg_mgr.regenerate_boot_support(alpine_rec.id)

        # Previous overlay file must have been restored untouched
        self.assertTrue(alpine_cpio_file.is_file())
        self.assertEqual(alpine_cpio_file.read_bytes(), original_cpio_bytes)

        # Restore working generator and regenerate successfully
        mock_gen.side_effect = fake_generate
        updated_alpine = self.reg_mgr.regenerate_boot_support(alpine_rec.id)
        self.assertEqual(updated_alpine.overlay_path, "boot/mventx/overlays/alpine-standard-3_24_4-x86_64.cpio")

        # Verify Ubuntu and Clonezilla entries were preserved 100% untouched
        envs_by_id = {e.id: e.to_dict() for e in self.reg_mgr.list_environments()}
        self.assertEqual(envs_by_id[ubuntu_rec.id], ubuntu_snapshot)
        self.assertEqual(envs_by_id[clonezilla_rec.id], clonezilla_snapshot)

        # Verify generated mventx_menu.cfg passes syntax validation and has all 3 entries
        menu_bytes = (self.boot_mount / "boot" / "grub" / "mventx_menu.cfg").read_bytes()
        self.assertNotIn(b"\r", menu_bytes)
        menu_text = menu_bytes.decode("utf-8")
        self.assertEqual(GrubMenuGenerator.validate_grub_script_syntax(menu_text), [])
        self.assertIn("ubuntu-26_04_1-desktop-amd64", menu_text)
        self.assertIn("clonezilla-live-3_3_3-37-amd64", menu_text)
        self.assertIn("alpine-standard-3_24_4-x86_64", menu_text)
        self.assertIn("modloop=/boot/modloop-lts", menu_text)
        self.assertNotIn("modloop-virt", menu_text)


if __name__ == "__main__":
    unittest.main()
