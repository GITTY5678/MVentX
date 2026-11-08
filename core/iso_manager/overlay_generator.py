"""Dynamic Initramfs Filesystem Overlay Generator for MVentX.

Extracts Canonical-provided kernel drivers (such as exfat.ko) from an Ubuntu
Live ISO's filesystem.squashfs, validates module compatibility and vermagic,
and compiles a supplementary CPIO initramfs overlay archive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import gzip
import lzma
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Dict, List, Optional, Tuple, Union

from core.iso_manager.kernel_module_inspector import (
    KernelModuleInfo,
    KernelModuleInspectionError,
    KernelModuleInspector,
)


@dataclass
class OverlayGenerationResult:
    """Outcome of generating a supplementary initramfs overlay."""
    success: bool
    cpio_path: Optional[Path] = None
    kernel_version: Optional[str] = None
    module_info: Optional[KernelModuleInfo] = None
    extracted_modules: List[str] = field(default_factory=list)
    file_size_bytes: int = 0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class OverlayGenerationError(Exception):
    """Raised when overlay generation cannot be completed."""


def find_seven_zip() -> Optional[Path]:
    """Locate 7-Zip binary across standard Windows installation paths and PATH."""
    for candidate in ("7z", "7za"):
        found = shutil.which(candidate)
        if found:
            return Path(found).resolve()

    # Standard Windows install locations
    common_paths = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe",
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
        Path(r"C:\Program Files\7-Zip\7z.exe"),
    ]
    for p in common_paths:
        if p.is_file():
            return p.resolve()

    return None


def create_newc_cpio(entries: List[Tuple[str, int, bytes]]) -> bytes:
    """Generate a valid, standards-compliant newc format CPIO archive in pure Python.

    Args:
        entries: List of (archive_path, file_mode, content_bytes).
                 Directories should have content_bytes = b"".

    Returns:
        Full binary bytes of the padded newc CPIO archive.
    """
    buf = io.BytesIO()
    ino = 1
    mtime = int(time.time())

    for path_str, mode, content in entries:
        norm_path = path_str.lstrip("/\\").replace("\\", "/").encode("ascii")
        namesize = len(norm_path) + 1  # includes terminating null byte
        filesize = len(content)
        nlink = 2 if (mode & 0o040000) else 1

        # CPIO newc header: exactly 110 ASCII hex bytes
        hdr = (
            f"070701"
            f"{ino:08X}"
            f"{mode:08X}"
            f"{0:08X}"  # uid
            f"{0:08X}"  # gid
            f"{nlink:08X}"
            f"{mtime:08X}"
            f"{filesize:08X}"
            f"{0:08X}"  # devmajor
            f"{0:08X}"  # devminor
            f"{0:08X}"  # rdevmajor
            f"{0:08X}"  # rdevminor
            f"{namesize:08X}"
            f"{0:08X}"  # check (always 0)
        ).encode("ascii")

        buf.write(hdr)
        buf.write(norm_path + b"\x00")

        # Header + namesize must be padded to a 4-byte boundary
        pad1 = (4 - ((110 + namesize) % 4)) % 4
        if pad1:
            buf.write(b"\x00" * pad1)

        # File content must be padded to a 4-byte boundary
        if filesize > 0:
            buf.write(content)
            pad2 = (4 - (filesize % 4)) % 4
            if pad2:
                buf.write(b"\x00" * pad2)

        ino += 1

    # Standard CPIO trailer
    trailer_name = b"TRAILER!!!\x00"
    trailer_hdr = (
        f"070701"
        f"{0:08X}"
        f"{0:08X}"
        f"{0:08X}"
        f"{0:08X}"
        f"{1:08X}"
        f"{0:08X}"
        f"{0:08X}"
        f"{0:08X}"
        f"{0:08X}"
        f"{0:08X}"
        f"{0:08X}"
        f"{len(trailer_name):08X}"
        f"{0:08X}"
    ).encode("ascii")

    buf.write(trailer_hdr)
    buf.write(trailer_name)
    pad = (4 - ((110 + len(trailer_name)) % 4)) % 4
    if pad:
        buf.write(b"\x00" * pad)

    # Pad final archive to 512-byte sector boundary (standard for Linux initramfs)
    total_len = buf.tell()
    pad_512 = (512 - (total_len % 512)) % 512
    if pad_512:
        buf.write(b"\x00" * pad_512)

    return buf.getvalue()


class InitramfsOverlayGenerator:
    """Manages extraction of kernel modules from ISO SquashFS and building CPIO overlays."""

    CASPER_PREMOUNT_SCRIPT = (
        b"#!/bin/sh\n"
        b"# MVentX Dynamic Initramfs Early-Boot exFAT Module Injection Hook\n"
        b"PREREQ=\"\"\n"
        b"prereqs() { echo \"$PREREQ\"; }\n"
        b"case $1 in prereqs) prereqs; exit 0 ;; esac\n"
        b"\n"
        b"mkdir -p /dev/.initramfs 2>/dev/null || true\n"
        b"\n"
        b"# Patch Casper's is_supported_fs() helper so 20iso_scan scans exFAT volumes\n"
        b"for f in /scripts/casper-helpers /scripts/casper; do\n"
        b"    if [ -f \"$f\" ] && ! grep -q 'exfat' \"$f\" 2>/dev/null; then\n"
        b"        sed -i 's/vfat|ntfs/vfat|ntfs|exfat/g' \"$f\" 2>/dev/null || true\n"
        b"    fi\n"
        b"done\n"
        b"\n"
        b"KVER=$(uname -r)\n"
        b"if ! grep -q exfat /proc/filesystems 2>/dev/null; then\n"
        b"    if command -v depmod >/dev/null 2>&1; then\n"
        b"        depmod -a \"$KVER\" 2>/dev/null || true\n"
        b"    fi\n"
        b"\n"
        b"    # 1. Attempt modprobe if modules.dep is intact\n"
        b"    modprobe exfat 2>/dev/null || true\n"
        b"\n"
        b"    # 2. Direct insmod fallback if not registered\n"
        b"    if ! grep -q exfat /proc/filesystems 2>/dev/null; then\n"
        b"        for base in \"/usr/lib/modules/${KVER}\" \"/lib/modules/${KVER}\"; do\n"
        b"            for mod in \"${base}\"/kernel/fs/nls/*.ko; do\n"
        b"                [ -f \"$mod\" ] && insmod \"$mod\" 2>/dev/null || true\n"
        b"            done\n"
        b"            for mod in \"${base}\"/kernel/fs/exfat/*.ko; do\n"
        b"                [ -f \"$mod\" ] && insmod \"$mod\" 2>/dev/null || true\n"
        b"            done\n"
        b"        done\n"
        b"    fi\n"
        b"\n"
        b"    if grep -q exfat /proc/filesystems 2>/dev/null; then\n"
        b"        echo \"[MVentX] exfat kernel module loaded successfully for ${KVER}\" >/dev/kmsg 2>/dev/null || true\n"
        b"        echo \"[MVentX] exfat kernel module loaded successfully for ${KVER}\"\n"
        b"    fi\n"
        b"fi\n"
    )

    INITRAMFS_CONF_HOOK = (
        b"# MVentX Early-Boot Casper exFAT Hook Registration\n"
        b"if [ -f /scripts/casper-premount/ORDER ] && ! grep -q 00_mventx_exfat /scripts/casper-premount/ORDER 2>/dev/null; then\n"
        b"    sed -i '1i /scripts/casper-premount/00_mventx_exfat \"$@\"' /scripts/casper-premount/ORDER 2>/dev/null || true\n"
        b"    echo '[ -n \"$LIVEMEDIA\" ] && echo \"[MVentX] Casper 20iso_scan mounted exFAT and located ISO: LIVEMEDIA=$LIVEMEDIA\" >/dev/kmsg 2>/dev/null || true' >> /scripts/casper-premount/ORDER\n"
        b"fi\n"
        b"/scripts/casper-premount/00_mventx_exfat\n"
    )

    SQUASHFS_CANDIDATE_PATTERNS = (
        "*filesystem.squashfs",
        "casper/minimal.squashfs",
        "*minimal.squashfs",
        "*ubuntu-server-minimal.squashfs",
        "*minimal.standard.live.squashfs",
        "*minimal.standard.squashfs",
    )

    @classmethod
    def can_generate_overlay(
        cls,
        iso_path: Union[Path, str],
        seven_zip_path: Optional[Union[Path, str]] = None,
    ) -> bool:
        """Check if 7-Zip is available and the target ISO contains a Live SquashFS image."""
        tool_7z = Path(seven_zip_path) if seven_zip_path else find_seven_zip()
        if not tool_7z or not tool_7z.is_file():
            return False
        iso_p = Path(iso_path)
        if not iso_p.is_file():
            return False
        try:
            cmd = [str(tool_7z), "l", str(iso_p), "*.squashfs*", "-r"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            return bool(res.returncode == 0 and ".squashfs" in res.stdout.lower())
        except Exception:
            return False

    @classmethod
    def decompress_module_bytes(cls, path: Path, raw_bytes: bytes, seven_zip_path: Optional[Path] = None) -> bytes:
        """Decompress .xz, .gz, or .zst kernel modules into raw ELF bytes."""
        name = path.name.lower()
        if name.endswith(".xz"):
            return lzma.decompress(raw_bytes)
        if name.endswith(".gz"):
            return gzip.decompress(raw_bytes)
        if name.endswith(".zst"):
            # Try python zstandard if available
            try:
                import zstandard
                dctx = zstandard.ZstdDecompressor()
                return dctx.decompress(raw_bytes)
            except ImportError:
                pass

            # Fallback to 7-Zip CLI decompression
            if seven_zip_path and seven_zip_path.is_file():
                with tempfile.TemporaryDirectory(prefix="mventx_zst_") as tdir:
                    t_in = Path(tdir) / "module.ko.zst"
                    t_in.write_bytes(raw_bytes)
                    cmd = [str(seven_zip_path), "e", str(t_in), f"-o{tdir}", "-y"]
                    subprocess.run(cmd, check=True, capture_output=True)
                    out_files = [f for f in Path(tdir).glob("*") if f != t_in]
                    if out_files:
                        return out_files[0].read_bytes()

            # Fallback to Windows bsdtar (tar.exe has bundled libzstd 1.5.7)
            tar_cmd = shutil.which("tar")
            if tar_cmd:
                with tempfile.TemporaryDirectory(prefix="mventx_tar_") as tdir:
                    t_in = Path(tdir) / "module.ko.zst"
                    t_in.write_bytes(raw_bytes)
                    # Use tar / bsdtar to decompress
                    cmd = [tar_cmd, "-xf", str(t_in), "-C", tdir]
                    try:
                        subprocess.run(cmd, check=True, capture_output=True)
                        out_files = [f for f in Path(tdir).glob("*") if f != t_in]
                        if out_files:
                            return out_files[0].read_bytes()
                    except Exception:
                        pass

            raise OverlayGenerationError(
                f"Cannot decompress Zstandard (.zst) module '{path.name}'. Neither zstandard python package, "
                f"7-Zip, nor compatible tar tool was able to unpack it."
            )

        return raw_bytes

    @classmethod
    def generate_exfat_overlay(
        cls,
        iso_path: Union[Path, str],
        output_cpio_path: Union[Path, str],
        target_kernel_version: Optional[str] = None,
        seven_zip_path: Optional[Union[Path, str]] = None,
    ) -> OverlayGenerationResult:
        """Extract exfat.ko from an Ubuntu Live ISO and build a bootable CPIO overlay.

        Args:
            iso_path: Path to the source Ubuntu / Casper ISO file.
            output_cpio_path: Destination path on MVX_BOOT for the generated CPIO archive.
            target_kernel_version: Optional explicit kernel version string. If omitted,
                                  automatically parsed from the ISO's vmlinuz boot header.
            seven_zip_path: Optional path to 7z.exe executable.

        Returns:
            OverlayGenerationResult detailing extracted modules, vermagic, and signature.
        """
        iso_p = Path(iso_path).resolve()
        if not iso_p.is_file():
            raise FileNotFoundError(f"Source ISO file not found: {iso_p}")

        out_p = Path(output_cpio_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)

        tool_7z = Path(seven_zip_path) if seven_zip_path else find_seven_zip()
        if not tool_7z or not tool_7z.is_file():
            raise OverlayGenerationError(
                "7-Zip executable (7z.exe) not found. Required for unpacking ISO SquashFS filesystem structures."
            )

        warnings: List[str] = []
        errors: List[str] = []

        temp_workspace = Path(tempfile.mkdtemp(prefix="mventx_overlay_build_"))
        try:
            # 1. Identify kernel version if not provided
            kernel_ver = target_kernel_version
            if not kernel_ver:
                # Extract vmlinuz from ISO
                vmlinuz_dir = temp_workspace / "vmlinuz_extract"
                vmlinuz_dir.mkdir(parents=True, exist_ok=True)
                cmd_k = [str(tool_7z), "e", str(iso_p), f"-o{vmlinuz_dir}", "*vmlinuz*", "-r", "-y"]
                subprocess.run(cmd_k, check=False, capture_output=True)
                vmlinuz_candidates = list(vmlinuz_dir.glob("*vmlinuz*"))
                if vmlinuz_candidates:
                    k_data = vmlinuz_candidates[0].read_bytes()
                    kernel_ver = KernelModuleInspector.extract_vmlinuz_version(k_data)

            # 2. Extract Live SquashFS and locate exfat.ko + NLS modules
            sqfs_dir = temp_workspace / "squashfs_stage"
            sqfs_dir.mkdir(parents=True, exist_ok=True)
            modules_dir = temp_workspace / "extracted_modules"
            modules_dir.mkdir(parents=True, exist_ok=True)

            exfat_files: List[Path] = []
            tried_squashfs: List[str] = []
            last_sq_err = ""

            for pattern in cls.SQUASHFS_CANDIDATE_PATTERNS:
                cmd_sq = [str(tool_7z), "e", str(iso_p), f"-o{sqfs_dir}", pattern, "-r", "-y"]
                proc_sq = subprocess.run(cmd_sq, capture_output=True, text=True)
                if proc_sq.returncode != 0:
                    last_sq_err = proc_sq.stderr or proc_sq.stdout or ""
                    continue

                sqfs_candidates = sorted(
                    sqfs_dir.glob("*.squashfs"),
                    key=lambda p: (0 if p.name in ("filesystem.squashfs", "minimal.squashfs") else 1, len(p.name)),
                )
                if not sqfs_candidates:
                    continue

                for sqfs_path in sqfs_candidates:
                    tried_squashfs.append(sqfs_path.name)
                    cmd_exfat = [str(tool_7z), "e", str(sqfs_path), f"-o{modules_dir}", "*exfat.ko*", "-r", "-y"]
                    proc_exfat = subprocess.run(cmd_exfat, capture_output=True, text=True)
                    if proc_exfat.returncode != 0:
                        last_sq_err = proc_exfat.stderr or proc_exfat.stdout or ""

                    found_now = [f for f in modules_dir.glob("*exfat.ko*") if f.is_file()]
                    if found_now:
                        # Also extract supporting NLS modules from the same SquashFS layer
                        cmd_nls = [
                            str(tool_7z),
                            "e",
                            str(sqfs_path),
                            f"-o{modules_dir}",
                            "*nls_utf8.ko*",
                            "*nls_iso8859-1.ko*",
                            "*nls_cp437.ko*",
                            "*nls_ascii.ko*",
                            "-r",
                            "-y",
                        ]
                        subprocess.run(cmd_nls, capture_output=True, text=True)
                        exfat_files = found_now

                    # Always unlink the extracted SquashFS immediately to conserve disk space
                    try:
                        sqfs_path.unlink()
                    except OSError:
                        pass

                    if exfat_files:
                        break

                # Clean up any remaining .squashfs files in sqfs_dir before next pattern
                for rem in sqfs_dir.glob("*.squashfs"):
                    try:
                        rem.unlink()
                    except OSError:
                        pass

                if exfat_files:
                    break

            if not tried_squashfs:
                raise OverlayGenerationError(
                    f"No Live SquashFS image (filesystem.squashfs / minimal.squashfs) found inside ISO '{iso_p.name}'. "
                    f"{last_sq_err}".strip()
                )

            if not exfat_files:
                raise OverlayGenerationError(
                    f"No exfat kernel module (*exfat.ko*) found inside '{iso_p.name}' SquashFS layers "
                    f"({', '.join(tried_squashfs)}). {last_sq_err}".strip()
                )

            target_exfat_file = exfat_files[0]
            exfat_raw = cls.decompress_module_bytes(target_exfat_file, target_exfat_file.read_bytes(), tool_7z)

            # 5. Inspect and validate exfat.ko
            mod_info = KernelModuleInspector.inspect_elf_module(exfat_raw)

            # If kernel_ver was not determined from vmlinuz, take it from the module itself
            if not kernel_ver:
                kernel_ver = mod_info.kernel_version

            # Validate vermagic compatibility
            is_compat, compat_reasons = KernelModuleInspector.verify_module_compatibility(mod_info, kernel_ver)
            if not is_compat:
                for r in compat_reasons:
                    warnings.append(r)

            if not mod_info.has_signature:
                warnings.append("Extracted module does not carry an appended cryptographic signature.")
            else:
                signer_str = mod_info.signer or "Unknown"
                if not any(
                    trusted in signer_str
                    for trusted in ("Canonical", "Ubuntu", "Build time autogenerated kernel key")
                ):
                    warnings.append(f"Module signer is not recognized ({signer_str}).")

            # 6. Decompress supporting modules if found
            supporting_mods: Dict[str, bytes] = {}
            for f in modules_dir.glob("*.ko*"):
                if f != target_exfat_file and f.is_file():
                    mod_bytes = cls.decompress_module_bytes(f, f.read_bytes(), tool_7z)
                    clean_name = f.name.split(".ko")[0] + ".ko"
                    supporting_mods[clean_name] = mod_bytes

            # 7. Assemble CPIO archive entries
            # Use usr/lib/modules/<kver>/... so we never overwrite the /lib -> usr/lib symlink
            # on usrmerged Ubuntu initramfs archives.
            cpio_entries: List[Tuple[str, int, bytes]] = []

            # Directory nodes
            cpio_entries.append(("usr", 0o040755, b""))
            cpio_entries.append(("usr/lib", 0o040755, b""))
            cpio_entries.append(("usr/lib/modules", 0o040755, b""))
            cpio_entries.append((f"usr/lib/modules/{kernel_ver}", 0o040755, b""))
            cpio_entries.append((f"usr/lib/modules/{kernel_ver}/kernel", 0o040755, b""))
            cpio_entries.append((f"usr/lib/modules/{kernel_ver}/kernel/fs", 0o040755, b""))
            cpio_entries.append((f"usr/lib/modules/{kernel_ver}/kernel/fs/exfat", 0o040755, b""))

            # Primary exfat module
            cpio_entries.append((f"usr/lib/modules/{kernel_ver}/kernel/fs/exfat/exfat.ko", 0o100755, exfat_raw))
            extracted_names = ["exfat.ko"]

            # Supporting NLS modules
            if supporting_mods:
                cpio_entries.append((f"usr/lib/modules/{kernel_ver}/kernel/fs/nls", 0o040755, b""))
                for s_name, s_bytes in supporting_mods.items():
                    cpio_entries.append((f"usr/lib/modules/{kernel_ver}/kernel/fs/nls/{s_name}", 0o100755, s_bytes))
                    extracted_names.append(s_name)

            # /etc/modules and /conf/conf.d early-boot configuration
            etc_modules_content = b"exfat\n"
            cpio_entries.append(("etc", 0o040755, b""))
            cpio_entries.append(("etc/modules", 0o100644, etc_modules_content))

            cpio_entries.append(("conf", 0o040755, b""))
            cpio_entries.append(("conf/conf.d", 0o040755, b""))
            cpio_entries.append(("conf/conf.d/zz_mventx_exfat.conf", 0o100644, cls.INITRAMFS_CONF_HOOK))

            # Early boot hooks
            cpio_entries.append(("scripts", 0o040755, b""))
            cpio_entries.append(("scripts/casper-premount", 0o040755, b""))
            cpio_entries.append(("scripts/casper-premount/00_mventx_exfat", 0o100755, cls.CASPER_PREMOUNT_SCRIPT))

            cpio_entries.append(("scripts/init-top", 0o040755, b""))
            cpio_entries.append(("scripts/init-top/00_mventx_exfat", 0o100755, cls.CASPER_PREMOUNT_SCRIPT))

            # 8. Compile and write CPIO archive
            cpio_data = create_newc_cpio(cpio_entries)

            # Atomic file write
            temp_out = out_p.with_suffix(".tmp")
            temp_out.write_bytes(cpio_data)
            temp_out.replace(out_p)

            return OverlayGenerationResult(
                success=True,
                cpio_path=out_p,
                kernel_version=kernel_ver,
                module_info=mod_info,
                extracted_modules=extracted_names,
                file_size_bytes=len(cpio_data),
                warnings=warnings,
                errors=errors,
            )

        finally:
            shutil.rmtree(temp_workspace, ignore_errors=True)
