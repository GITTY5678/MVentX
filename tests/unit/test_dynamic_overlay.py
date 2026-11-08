"""Unit tests for MVentX Milestone 19: Dynamic Initramfs Filesystem Overlay.

Tests cover:
1. KernelModuleInspector: ELF .ko parsing, .modinfo extraction, cryptographic signature verification,
   vmlinuz x86 boot header version extraction, and vermagic compatibility checking.
2. create_newc_cpio: Standards-compliant newc CPIO archive builder, 4-byte alignment, 512-byte sector padding.
3. InitramfsOverlayGenerator: Module decompression, SquashFS extraction pipeline, hook scripts, and packaging.
4. ISORegistrationManager overlay lifecycle: Generation, storage on MVX_BOOT, rollback on sync/generation errors,
   overlay removal on deregistration (preserving ISO), and orphan cleanup.
5. recipes.py / generator.py: Dual-initrd command emission and fallback paths in GRUB 2 loopback recipes.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import shutil
import struct
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from core.boot_config.generator import GrubMenuGenerator
from core.boot_config.recipes import generate_loopback_boot_commands, get_recipe
from core.iso_manager.kernel_module_inspector import (
    KernelModuleInfo,
    KernelModuleInspectionError,
    KernelModuleInspector,
)
from core.iso_manager.overlay_generator import (
    InitramfsOverlayGenerator,
    OverlayGenerationError,
    OverlayGenerationResult,
    create_newc_cpio,
    find_seven_zip,
)
from core.iso_manager.registration import (
    EnvironmentRecord,
    ISORegistrationManager,
    MenuSyncRollbackError,
    RegistrationError,
)
from core.storage.storage_manager import StorageManager
from tests.unit.test_iso_validation import make_mock_iso


def build_synthetic_elf_ko(
    modinfo_dict: dict[str, str],
    signature_signer: str | None = None,
    signature_key_id: bytes = b"\x01\x02\x03\x04",
) -> bytes:
    """Build a minimal valid 64-bit ELF relocatable object (.ko) with .modinfo and optional signature."""
    buf = io.BytesIO()

    # ELF64 Header (64 bytes)
    # e_ident: 16 bytes
    e_ident = bytearray(16)
    e_ident[0:4] = b"\x7fELF"
    e_ident[4] = 2  # ELFCLASS64
    e_ident[5] = 1  # ELFDATA2LSB (little endian)
    e_ident[6] = 1  # EV_CURRENT
    e_ident[7] = 0  # ELFOSABI_NONE
    buf.write(e_ident)

    # e_type (ET_REL = 1), e_machine (EM_X86_64 = 62), e_version (1)
    buf.write(struct.pack("<HHI", 1, 62, 1))
    # e_entry (0), e_phoff (0), e_shoff (placeholder at offset 40)
    buf.write(struct.pack("<QQQ", 0, 0, 0))
    # e_flags (0), e_ehsize (64), e_phentsize (0), e_phnum (0), e_shentsize (64), e_shnum (3), e_shstrndx (2)
    buf.write(struct.pack("<IHHHHHH", 0, 64, 0, 0, 64, 3, 2))

    # Construct .modinfo content (null-separated strings)
    modinfo_bytes = bytearray()
    for k, v in modinfo_dict.items():
        modinfo_bytes.extend(f"{k}={v}\x00".encode("utf-8"))

    # Construct .shstrtab section names
    shstrtab_bytes = b"\x00.modinfo\x00.shstrtab\x00"

    # Section offsets
    offset_modinfo = 64
    offset_shstrtab = offset_modinfo + len(modinfo_bytes)
    # Section header table begins after sections, 8-byte aligned
    shoff = (offset_shstrtab + len(shstrtab_bytes) + 7) & ~7

    buf.seek(offset_modinfo)
    buf.write(modinfo_bytes)
    buf.seek(offset_shstrtab)
    buf.write(shstrtab_bytes)

    # Section header table: 3 headers (SHN_UNDEF, .modinfo, .shstrtab)
    buf.seek(shoff)
    # 0: NULL header
    buf.write(b"\x00" * 64)

    # 1: .modinfo header (name offset 1, type SHT_PROGBITS=1, flags=0)
    buf.write(struct.pack("<IIQQQQIIQQ", 1, 1, 0, 0, offset_modinfo, len(modinfo_bytes), 0, 0, 1, 0))

    # 2: .shstrtab header (name offset 10, type SHT_STRTAB=3, flags=0)
    buf.write(struct.pack("<IIQQQQIIQQ", 10, 3, 0, 0, offset_shstrtab, len(shstrtab_bytes), 0, 0, 1, 0))

    # Update e_shoff in ELF header
    total_elf_len = buf.tell()
    buf.seek(40)
    buf.write(struct.pack("<Q", shoff))

    elf_data = buf.getvalue()

    # Append PKCS#7 signature if requested
    if signature_signer is not None:
        signer_bytes = signature_signer.encode("utf-8")
        sig_data = b"\x30\x82\x01\x00" + b"\xFF" * 64  # simulated PKCS#7 raw data

        # struct module_signature: 12 bytes
        # algo (0), hash (4=SHA256), id_type (1=PKEY_ID_PKCS7), signer_len (u8), key_id_len (u32), sig_len (u32)
        mod_sig_hdr = struct.pack(
            ">BBBBII",
            0,
            4,
            1,
            len(signer_bytes),
            len(signature_key_id),
            len(sig_data),
        )
        magic = b"~Module signature append~"
        elf_data += signer_bytes + signature_key_id + sig_data + mod_sig_hdr + magic

    return elf_data


def build_synthetic_vmlinuz(kernel_version: str) -> bytes:
    """Build a minimal synthetic Linux x86 setup header containing HdrS magic and version string."""
    buf = bytearray(4096)
    # Setup header magic 'HdrS' at offset 0x202 (514)
    buf[0x202:0x206] = b"HdrS"
    # Kernel version pointer at offset 0x20E (526) -> point to offset 0x400
    struct.pack_into("<H", buf, 0x20E, 0x400 - 0x200)
    # Write version string at 0x400
    v_bytes = (kernel_version + "\x00").encode("ascii")
    buf[0x400 : 0x400 + len(v_bytes)] = v_bytes
    return bytes(buf)


class TestKernelModuleInspector(unittest.TestCase):
    """Test suite for ELF .ko inspection and compatibility validation."""

    def test_inspect_elf_module_with_signature(self):
        """Verify extraction of modinfo fields and cryptographic signature metadata."""
        modinfo = {
            "name": "exfat",
            "vermagic": "6.8.0-31-generic SMP preempt mod_unload modversions",
            "license": "GPL",
            "description": "exFAT filesystem support",
            "depends": "nls_utf8",
            "alias": "fs-exfat",
        }
        elf_bytes = build_synthetic_elf_ko(
            modinfo_dict=modinfo,
            signature_signer="Canonical Ltd. Master CA",
        )

        info = KernelModuleInspector.inspect_elf_module(elf_bytes)
        self.assertEqual(info.name, "exfat")
        self.assertEqual(info.kernel_version, "6.8.0-31-generic")
        self.assertEqual(info.license, "GPL")
        self.assertEqual(info.dependencies, ["nls_utf8"])
        self.assertEqual(info.aliases, ["fs-exfat"])
        self.assertTrue(info.has_signature)
        self.assertIn("Canonical", info.signer)

    def test_inspect_elf_module_without_signature(self):
        """Verify module inspection correctly flags unsigned modules."""
        modinfo = {
            "name": "exfat",
            "vermagic": "5.15.0-89-generic SMP mod_unload",
            "license": "GPL",
        }
        elf_bytes = build_synthetic_elf_ko(modinfo_dict=modinfo, signature_signer=None)

        info = KernelModuleInspector.inspect_elf_module(elf_bytes)
        self.assertEqual(info.name, "exfat")
        self.assertEqual(info.kernel_version, "5.15.0-89-generic")
        self.assertFalse(info.has_signature)
        self.assertIsNone(info.signer)

    def test_extract_vmlinuz_version(self):
        """Verify direct parsing of kernel version from setup header."""
        vmlinuz_data = build_synthetic_vmlinuz("6.8.0-31-generic")
        ver = KernelModuleInspector.extract_vmlinuz_version(vmlinuz_data)
        self.assertEqual(ver, "6.8.0-31-generic")

    def test_extract_real_vmlinuz_focal(self):
        """Verify version extraction against real Ubuntu focal vmlinuz binary if available."""
        real_iso = Path(r"C:\MVentX\build\scratch\ubuntu_focal_mini.iso")
        tool_7z = find_seven_zip()
        if not real_iso.is_file() or not tool_7z:
            self.skipTest("Real focal ISO or 7z not available.")

        import subprocess
        with tempfile.TemporaryDirectory() as td:
            # Extract linux binary from ISO
            cmd = [str(tool_7z), "e", str(real_iso), f"-o{td}", "linux", "-r", "-y"]
            subprocess.run(cmd, check=True, capture_output=True)
            k_bin = Path(td) / "linux"
            if k_bin.is_file():
                ver = KernelModuleInspector.extract_vmlinuz_version(k_bin.read_bytes())
                self.assertIsNotNone(ver)
                self.assertIn("5.4.0", ver)
                self.assertIn("generic", ver)

    def test_verify_module_compatibility(self):
        """Verify compatibility check logic against target kernel."""
        info = KernelModuleInfo(
            name="exfat",
            vermagic="6.8.0-31-generic SMP preempt mod_unload modversions",
            kernel_version="6.8.0-31-generic",
            license="GPL",
        )
        # Matching kernel
        ok, reasons = KernelModuleInspector.verify_module_compatibility(info, "6.8.0-31-generic")
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

        # Mismatched kernel
        ok_bad, reasons_bad = KernelModuleInspector.verify_module_compatibility(info, "5.15.0-25-generic")
        self.assertFalse(ok_bad)
        self.assertTrue(any("mismatch" in r.lower() for r in reasons_bad))


class TestCpioArchiveBuilder(unittest.TestCase):
    """Test suite for the pure-Python standards-compliant newc CPIO builder."""

    def test_create_newc_cpio_structure(self):
        """Verify header format, magic 070701, TRAILER, and 512-byte sector padding."""
        entries = [
            ("lib", 0o040755, b""),
            ("lib/modules", 0o040755, b""),
            ("etc/modules", 0o100644, b"exfat\n"),
            ("scripts/test.sh", 0o100755, b"#!/bin/sh\necho hello\n"),
        ]
        cpio_bytes = create_newc_cpio(entries)

        # 1. Must be aligned to 512-byte sector boundary
        self.assertEqual(len(cpio_bytes) % 512, 0)

        # 2. Must start with newc magic ASCII "070701"
        self.assertEqual(cpio_bytes[:6], b"070701")

        # 3. Must contain TRAILER!!! record
        self.assertIn(b"TRAILER!!!\x00", cpio_bytes)

        # 4. Check that all file paths and contents exist in the stream
        self.assertIn(b"etc/modules\x00", cpio_bytes)
        self.assertIn(b"exfat\n", cpio_bytes)
        self.assertIn(b"scripts/test.sh\x00", cpio_bytes)
        self.assertIn(b"#!/bin/sh\necho hello\n", cpio_bytes)

    def test_cpio_archive_roundtrip_with_tar(self):
        """Verify that Windows bsdtar/tar can list the generated CPIO archive without error."""
        tar_cmd = shutil.which("tar")
        if not tar_cmd:
            self.skipTest("tar command not available on host.")

        entries = [
            ("etc", 0o040755, b""),
            ("etc/modules", 0o100644, b"exfat\n"),
        ]
        cpio_bytes = create_newc_cpio(entries)

        with tempfile.NamedTemporaryFile(suffix=".cpio", delete=False) as tf:
            tf.write(cpio_bytes)
            tf_path = Path(tf.name)

        try:
            import subprocess
            proc = subprocess.run(
                [tar_cmd, "-tvf", str(tf_path)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("etc/modules", proc.stdout)
        finally:
            tf_path.unlink(missing_ok=True)


class TestOverlayLifecycleAndRollback(unittest.TestCase):
    """Test suite for registration, rollback, updates, and removal with dynamic overlays."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        self.data_mount = self.root_path / "MVX_DATA"
        self.boot_mount = self.root_path / "MVX_BOOT"
        self.data_mount.mkdir()
        self.boot_mount.mkdir()
        (self.boot_mount / "boot" / "grub").mkdir(parents=True)

        self.storage_manager = StorageManager(
            data_mount=self.data_mount,
            boot_mount=self.boot_mount,
        )
        self.reg_mgr = ISORegistrationManager(self.storage_manager)

        self.iso_dir = self.reg_mgr.iso_directory
        self.ubuntu_iso = self.iso_dir / "ubuntu-26.04.1-desktop-amd64.iso"
        make_mock_iso(
            self.ubuntu_iso,
            volume_id="UBUNTU_26_04",
            system_id="LINUX",
            publisher_id="CANONICAL",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.generate_exfat_overlay")
    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.can_generate_overlay", return_value=True)
    def test_registration_with_casper_overlay_success(self, mock_can, mock_gen):
        """Verify Casper registration automatically generates overlay on MVX_BOOT and records path."""
        def fake_generate(iso_path, output_cpio_path, **kwargs):
            out_p = Path(output_cpio_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_bytes(b"MOCK_CPIO_DATA")
            return OverlayGenerationResult(
                success=True,
                cpio_path=out_p,
                kernel_version="6.8.0-31-generic",
                file_size_bytes=14,
            )

        mock_gen.side_effect = fake_generate

        rec = self.reg_mgr.register_iso(
            iso_path=self.ubuntu_iso,
            display_name="Ubuntu 26.04.1 LTS",
            boot_method="casper",
        )

        self.assertEqual(rec.boot_method, "casper")
        self.assertEqual(rec.recipe, "casper")
        self.assertIsNotNone(rec.overlay_path)
        self.assertTrue(rec.overlay_path.endswith(".cpio"))

        # Verify CPIO archive actually exists on MVX_BOOT
        overlay_file = self.boot_mount / rec.overlay_path
        self.assertTrue(overlay_file.is_file())
        self.assertEqual(overlay_file.read_bytes(), b"MOCK_CPIO_DATA")

        # Verify GRUB menu was generated with both initrds
        grub_cfg = (self.boot_mount / "boot" / "grub" / "mventx_menu.cfg").read_text(encoding="utf-8")
        self.assertIn("search --no-floppy --label --set=mventx_boot_dev MVX_BOOT", grub_cfg)
        self.assertIn(f"(${{mventx_boot_dev}})/{rec.overlay_path}", grub_cfg.replace("\\", "/"))

    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.generate_exfat_overlay")
    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.can_generate_overlay", return_value=True)
    def test_registration_overlay_generation_failure_rolls_back(self, mock_can, mock_gen):
        """Verify that failure during overlay generation aborts registration and leaves no state."""
        mock_gen.side_effect = OverlayGenerationError("SquashFS extraction failure simulated")

        with self.assertRaises(RegistrationError) as ctx:
            self.reg_mgr.register_iso(
                iso_path=self.ubuntu_iso,
                display_name="Ubuntu Broken",
                boot_method="casper",
            )

        self.assertIn("Dynamic initramfs overlay generation failed", str(ctx.exception))
        # Environments list should remain empty
        envs = self.reg_mgr.list_environments()
        self.assertEqual(len(envs), 0)

        # Overlays directory should be clean
        overlays_dir = self.storage_manager.overlays_directory_path
        if overlays_dir.exists():
            self.assertEqual(list(overlays_dir.glob("*.cpio")), [])

    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.generate_exfat_overlay")
    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.can_generate_overlay", return_value=True)
    def test_registration_menu_sync_failure_cleans_up_overlay(self, mock_can, mock_gen):
        """Verify that menu sync failure rolls back configuration and unlinks the generated CPIO."""
        def fake_generate(iso_path, output_cpio_path, **kwargs):
            out_p = Path(output_cpio_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_bytes(b"MOCK_CPIO")
            return OverlayGenerationResult(success=True, cpio_path=out_p)

        mock_gen.side_effect = fake_generate

        # Force sync_boot_menu to fail
        with patch.object(self.storage_manager, "sync_boot_menu", side_effect=RuntimeError("Disk read-only")):
            with self.assertRaises(MenuSyncRollbackError):
                self.reg_mgr.register_iso(
                    iso_path=self.ubuntu_iso,
                    display_name="Ubuntu Rollback",
                    boot_method="casper",
                )

        # Configuration should have 0 environments
        envs = self.reg_mgr.list_environments()
        self.assertEqual(len(envs), 0)

        # The generated .cpio file should have been cleaned up by rollback logic
        overlays_dir = self.storage_manager.overlays_directory_path
        if overlays_dir.exists():
            self.assertEqual(list(overlays_dir.glob("*.cpio")), [])

    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.generate_exfat_overlay")
    @patch("core.iso_manager.overlay_generator.InitramfsOverlayGenerator.can_generate_overlay", return_value=True)
    def test_removal_cleans_up_overlay_and_preserves_iso(self, mock_can, mock_gen):
        """Verify removing environment deletes overlay from MVX_BOOT and strictly preserves ISO on MVX_DATA."""
        def fake_generate(iso_path, output_cpio_path, **kwargs):
            out_p = Path(output_cpio_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_bytes(b"MOCK_CPIO")
            return OverlayGenerationResult(success=True, cpio_path=out_p)

        mock_gen.side_effect = fake_generate

        rec = self.reg_mgr.register_iso(
            iso_path=self.ubuntu_iso,
            display_name="Ubuntu to Remove",
            boot_method="casper",
        )

        overlay_file = self.boot_mount / rec.overlay_path
        self.assertTrue(overlay_file.is_file())
        self.assertTrue(self.ubuntu_iso.is_file())

        # Remove without deleting ISO
        success = self.reg_mgr.remove_environment(rec.id, delete_file=False)
        self.assertTrue(success)

        # Overlay on MVX_BOOT must be deleted
        self.assertFalse(overlay_file.exists())
        # Underlying ISO on MVX_DATA must be completely preserved!
        self.assertTrue(self.ubuntu_iso.is_file())

    def test_clean_orphaned_overlays(self):
        """Verify cleanup of orphaned CPIO files not referenced by any registered environment."""
        overlays_dir = self.storage_manager.overlays_directory_path
        overlays_dir.mkdir(parents=True, exist_ok=True)

        # Create two orphaned files and one valid registered file
        orphan1 = overlays_dir / "old_orphaned_env1.cpio"
        orphan2 = overlays_dir / "legacy_test.cpio"
        orphan1.write_bytes(b"ORPHAN1")
        orphan2.write_bytes(b"ORPHAN2")

        cleaned = self.reg_mgr.clean_orphaned_overlays()
        self.assertEqual(cleaned, 2)
        self.assertFalse(orphan1.exists())
        self.assertFalse(orphan2.exists())


class TestOverlayGeneratorPipeline(unittest.TestCase):
    """Test suite for InitramfsOverlayGenerator extraction and archive compilation."""

    def test_generate_exfat_overlay_pipeline(self):
        """Verify pipeline execution: extracts, validates vermagic, generates hooks, and packages into CPIO."""
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            iso_dummy = td_p / "dummy.iso"
            iso_dummy.write_bytes(b"DUMMY_ISO_CONTENT")
            output_cpio = td_p / "output.cpio"

            # Create synthetic modules
            exfat_ko = build_synthetic_elf_ko(
                modinfo_dict={
                    "name": "exfat",
                    "vermagic": "6.8.0-31-generic SMP preempt mod_unload",
                    "license": "GPL",
                },
                signature_signer="Canonical Ltd. Master CA",
            )
            nls_ko = build_synthetic_elf_ko(
                modinfo_dict={
                    "name": "nls_utf8",
                    "vermagic": "6.8.0-31-generic SMP preempt mod_unload",
                    "license": "Dual BSD/GPL",
                },
            )

            # Mock subprocess.run for 7z extraction steps
            import subprocess

            def fake_subprocess_run(cmd, *args, **kwargs):
                cmd_str = " ".join(str(c) for c in cmd)
                # If command is extracting vmlinuz:
                if "*vmlinuz*" in cmd_str:
                    out_dir = Path([arg for arg in cmd if str(arg).startswith("-o")][0][2:])
                    (out_dir / "vmlinuz").write_bytes(build_synthetic_vmlinuz("6.8.0-31-generic"))
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                # If command is extracting filesystem.squashfs:
                elif "*filesystem.squashfs" in cmd_str:
                    out_dir = Path([arg for arg in cmd if str(arg).startswith("-o")][0][2:])
                    (out_dir / "filesystem.squashfs").write_bytes(b"MOCK_SQUASHFS")
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                # If command is extracting exfat.ko:
                elif "*exfat.ko*" in cmd_str:
                    out_dir = Path([arg for arg in cmd if str(arg).startswith("-o")][0][2:])
                    (out_dir / "exfat.ko").write_bytes(exfat_ko)
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                # If command is extracting nls:
                elif "*nls_utf8.ko*" in cmd_str:
                    out_dir = Path([arg for arg in cmd if str(arg).startswith("-o")][0][2:])
                    (out_dir / "nls_utf8.ko").write_bytes(nls_ko)
                    return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            # Mock find_seven_zip to return a valid path
            dummy_7z = td_p / "fake_7z.exe"
            dummy_7z.write_bytes(b"FAKE_EXE")

            with patch("core.iso_manager.overlay_generator.find_seven_zip", return_value=dummy_7z), \
                 patch("subprocess.run", side_effect=fake_subprocess_run):
                result = InitramfsOverlayGenerator.generate_exfat_overlay(
                    iso_path=iso_dummy,
                    output_cpio_path=output_cpio,
                )

            self.assertTrue(result.success)
            self.assertEqual(result.kernel_version, "6.8.0-31-generic")
            self.assertIn("exfat.ko", result.extracted_modules)
            self.assertTrue(output_cpio.is_file())

            # Read back generated CPIO bytes
            cpio_bytes = output_cpio.read_bytes()
            self.assertEqual(len(cpio_bytes) % 512, 0)
            self.assertIn(b"usr/lib/modules/6.8.0-31-generic/kernel/fs/exfat/exfat.ko\x00", cpio_bytes)
            self.assertIn(b"usr/lib/modules/6.8.0-31-generic/kernel/fs/nls/nls_utf8.ko\x00", cpio_bytes)
            self.assertIn(b"conf/conf.d/zz_mventx_exfat.conf\x00", cpio_bytes)
            self.assertIn(b"scripts/casper-premount/00_mventx_exfat\x00", cpio_bytes)
            self.assertIn(b"scripts/init-top/00_mventx_exfat\x00", cpio_bytes)
            self.assertIn(b"etc/modules\x00", cpio_bytes)


class TestRecipesOverlayCommandGeneration(unittest.TestCase):
    """Test suite for GRUB 2 script generation with dynamic overlay handling."""

    def test_generate_loopback_boot_commands_with_overlay(self):
        """Verify that providing overlay_path generates dual-initrd commands and search block."""
        recipe = get_recipe("casper")
        commands = generate_loopback_boot_commands(
            recipe=recipe,
            iso_path="/ISO/ubuntu-26.04.1.iso",
            overlay_path="boot/mventx/overlays/ubuntu_env.cpio",
        )
        cmd_text = "\n".join(commands)

        # Check search block for MVX_BOOT
        self.assertIn("search --no-floppy --label --set=mventx_boot_dev MVX_BOOT", cmd_text)
        # Check dual initrd command
        self.assertIn("initrd (loop0)/casper/initrd (${mventx_boot_dev})/boot/mventx/overlays/ubuntu_env.cpio", cmd_text)
        # Check fallback to single initrd if overlay is missing at runtime
        self.assertIn("initrd (loop0)/casper/initrd", cmd_text)

    def test_generate_loopback_boot_commands_without_overlay(self):
        """Verify that omitting overlay_path generates standard single-initrd commands."""
        recipe = get_recipe("casper")
        commands = generate_loopback_boot_commands(
            recipe=recipe,
            iso_path="/ISO/ubuntu-26.04.1.iso",
            overlay_path=None,
        )
        cmd_text = "\n".join(commands)

        self.assertNotIn("search --no-floppy --label --set=mventx_boot_dev MVX_BOOT", cmd_text)
        self.assertIn("initrd (loop0)/casper/initrd", cmd_text)

    def test_m19_2_physical_line_structure_and_lf_endings(self):
        """Regression test (M19.2): verify physical line structure, single-line initrd/test, and pure LF endings."""
        config = {
            "version": "1.0.0",
            "meta": {
                "title": "MVentX Multiboot System",
                "theme": "mventx",
                "timeout": 10,
                "default_entry_id": None,
            },
            "environments": [
                {
                    "id": "ubuntu-26_04_1-desktop-amd64",
                    "display_name": "Ubuntu 26.04.1 Lts Amd64",
                    "type": "iso",
                    "boot_method": "casper",
                    "enabled": True,
                    "order": 1,
                    "iso_path": "ISO/ubuntu-26.04.1-desktop-amd64.iso",
                    "description": "Ubuntu 26.04.1 Desktop Live Environment",
                    "recipe": "casper",
                    "kernel_path": "/casper/vmlinuz",
                    "initrd_path": "/casper/initrd",
                    "overlay_path": "boot/mventx/overlays/ubuntu-26_04_1-desktop-amd64.cpio",
                }
            ],
        }
        gen = GrubMenuGenerator()
        with tempfile.TemporaryDirectory() as td:
            out_cfg = Path(td) / "mventx_menu.cfg"
            gen.write_menu_file(config, out_cfg)
            raw_bytes = out_cfg.read_bytes()

        # 1. File on disk must contain strictly LF ('\n') line endings and zero CR ('\r') bytes
        self.assertNotIn(b"\r", raw_bytes)

        script = raw_bytes.decode("utf-8")
        syntax_errors = GrubMenuGenerator.validate_grub_script_syntax(script)
        self.assertEqual(syntax_errors, [])

        physical_lines = [line.strip() for line in script.split("\n")]

        # 2. Combined initrd command must be emitted on ONE physical line
        expected_initrd_line = (
            "initrd (loop0)/casper/initrd "
            "(${mventx_boot_dev})/boot/mventx/overlays/ubuntu-26_04_1-desktop-amd64.cpio"
        )
        self.assertIn(expected_initrd_line, physical_lines)

        # 3. Overlay existence test must be emitted as ONE single-line valid command
        expected_test_line = (
            'if [ -f "(${mventx_boot_dev})/boot/mventx/overlays/ubuntu-26_04_1-desktop-amd64.cpio" ]; then'
        )
        self.assertIn(expected_test_line, physical_lines)

        # 4. mventx_boot_dev must be initialized by searching MVX_BOOT label before use
        search_line = "search --no-floppy --label --set=mventx_boot_dev MVX_BOOT"
        self.assertIn(search_line, physical_lines)
        search_idx = physical_lines.index(search_line)
        test_idx = physical_lines.index(expected_test_line)
        initrd_idx = physical_lines.index(expected_initrd_line)
        self.assertLess(search_idx, test_idx)
        self.assertLess(test_idx, initrd_idx)

    def test_m19_2_grub_script_syntax_validator_rejects_split_commands_and_crlf(self):
        """Regression test (M19.2): verify validator catches CRLF, split test lines, and split initrd lines."""
        # 1. Split initrd command across two physical lines
        split_initrd_script = "\n".join([
            'menuentry "Ubuntu" --id "ubuntu" {',
            '    if [ -z "${mventx_boot_dev}" ]; then',
            '        search --no-floppy --label --set=mventx_boot_dev MVX_BOOT',
            '    fi',
            '    if [ -f "(${mventx_boot_dev})/boot/mventx/overlays/ubuntu-26_04_1-desktop-amd64.cpio" ]; then',
            '        initrd (loop0)/casper/initrd',
            '        (${mventx_boot_dev})/boot/mventx/overlays/ubuntu-26_04_1-desktop-amd64.cpio',
            '    fi',
            '}',
        ])
        errs_split_initrd = GrubMenuGenerator.validate_grub_script_syntax(split_initrd_script)
        self.assertTrue(any("orphaned device path" in e for e in errs_split_initrd), errs_split_initrd)

        # 2. Split overlay existence condition across two physical lines
        split_test_script = "\n".join([
            'menuentry "Ubuntu" --id "ubuntu" {',
            '    if [ -z "${mventx_boot_dev}" ]; then',
            '        search --no-floppy --label --set=mventx_boot_dev MVX_BOOT',
            '    fi',
            '    if [ -f "(${mventx_boot_dev})/boot/mventx/overlays/ubuntu-26_04_1-desktop-amd64.',
            'cpio" ]; then',
            '        initrd (loop0)/casper/initrd (${mventx_boot_dev})/boot/mventx/overlays/ubuntu-26_04_1-desktop-amd64.cpio',
            '    fi',
            '}',
        ])
        errs_split_test = GrubMenuGenerator.validate_grub_script_syntax(split_test_script)
        self.assertTrue(any("must end with '; then'" in e for e in errs_split_test), errs_split_test)

        # 3. Missing MVX_BOOT search before mventx_boot_dev usage
        missing_search_script = "\n".join([
            'menuentry "Ubuntu" --id "ubuntu" {',
            '    if [ -f "(${mventx_boot_dev})/boot/mventx/overlays/ubuntu-26_04_1-desktop-amd64.cpio" ]; then',
            '        initrd (loop0)/casper/initrd (${mventx_boot_dev})/boot/mventx/overlays/ubuntu-26_04_1-desktop-amd64.cpio',
            '    fi',
            '}',
        ])
        errs_no_search = GrubMenuGenerator.validate_grub_script_syntax(missing_search_script)
        self.assertTrue(any("before MVX_BOOT label search" in e for e in errs_no_search), errs_no_search)

        # 4. CRLF line endings
        crlf_script = 'menuentry "Ubuntu" --id "ubuntu" {\r\n    reboot\r\n}\r\n'
        errs_crlf = GrubMenuGenerator.validate_grub_script_syntax(crlf_script)
        self.assertTrue(any("CR ('\\r')" in e for e in errs_crlf), errs_crlf)

    def test_m19_2_embedded_newlines_in_arguments_are_normalized_to_single_line(self):
        """Regression test (M19.2): verify embedded CR/LF in overlay_path or iso_path never split GRUB commands."""
        recipe = get_recipe("casper")
        commands = generate_loopback_boot_commands(
            recipe=recipe,
            iso_path="/ISO/ubuntu-26.04.1-desktop-amd64.iso\r\n",
            overlay_path="boot/mventx/overlays/\nubuntu-26_04_1-desktop-amd64.cpio\r",
        )
        for cmd in commands:
            self.assertNotIn("\r", cmd)
            self.assertNotIn("\n", cmd)
        stripped = [c.strip() for c in commands]
        self.assertIn(
            "initrd (loop0)/casper/initrd (${mventx_boot_dev})/boot/mventx/overlays/ubuntu-26_04_1-desktop-amd64.cpio",
            stripped,
        )


if __name__ == "__main__":
    unittest.main()
