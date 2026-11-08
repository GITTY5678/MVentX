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


def parse_newc_cpio_entries(cpio_bytes: bytes) -> Dict[str, bytes]:
    """Parse a newc (070701/070702) CPIO archive into a mapping of {archive_path: content_bytes}."""
    entries: Dict[str, bytes] = {}
    offset = 0
    n = len(cpio_bytes)
    while offset + 110 <= n:
        magic = cpio_bytes[offset : offset + 6]
        if magic not in (b"070701", b"070702"):
            # Skip zero padding between concatenated archives
            if cpio_bytes[offset : offset + 1] == b"\x00":
                offset += 1
                continue
            break
        try:
            filesize = int(cpio_bytes[offset + 54 : offset + 62], 16)
            namesize = int(cpio_bytes[offset + 94 : offset + 102], 16)
        except ValueError:
            break
        name_start = offset + 110
        name_end = name_start + namesize
        if name_end > n:
            break
        name = cpio_bytes[name_start : name_end - 1].decode("utf-8", errors="ignore").lstrip("./")
        pad1 = (4 - ((110 + namesize) % 4)) % 4
        data_start = name_end + pad1
        data_end = data_start + filesize
        if data_end > n:
            break
        pad2 = (4 - (filesize % 4)) % 4
        offset = data_end + pad2
        if name == "TRAILER!!!":
            continue
        entries[name] = cpio_bytes[data_start:data_end]
    return entries


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

    ALPINE_MDEV_HOOK = (
        b"#!/bin/sh\n"
        b"# MVentX Alpine Early-Boot mdev Hook for exFAT + ISO Loopback Attachment\n"
        b"/bin/busybox mdev \"$@\"\n"
        b"MDEV_RC=$?\n"
        b"\n"
        b"if [ -f /init ] && [ ! -f /tmp/.mventx_iso_attached ] && [ -n \"$DEVNAME\" ]; then\n"
        b"    case \"$DEVNAME\" in\n"
        b"        loop*|ram*|fd*) exit $MDEV_RC ;;\n"
        b"    esac\n"
        b"    DEVNODE=\"/dev/${DEVNAME}\"\n"
        b"    [ -b \"$DEVNODE\" ] || exit $MDEV_RC\n"
        b"\n"
        b"    ISO_PATH=\"\"\n"
        b"    for arg in $(cat /proc/cmdline 2>/dev/null); do\n"
        b"        case \"$arg\" in\n"
        b"            iso-scan/filename=*) ISO_PATH=\"${arg#iso-scan/filename=}\" ;;\n"
        b"            findiso=*) ISO_PATH=\"${arg#findiso=}\" ;;\n"
        b"            alpine_iso=*) ISO_PATH=\"${arg#alpine_iso=}\" ;;\n"
        b"        esac\n"
        b"    done\n"
        b"    ISO_REL=\"${ISO_PATH#/}\"\n"
        b"    [ -n \"$ISO_REL\" ] || exit $MDEV_RC\n"
        b"\n"
        b"    KVER=$(uname -r)\n"
        b"    if ! grep -q exfat /proc/filesystems 2>/dev/null; then\n"
        b"        modprobe exfat 2>/dev/null || true\n"
        b"        if ! grep -q exfat /proc/filesystems 2>/dev/null; then\n"
        b"            for mod in \"/usr/lib/modules/${KVER}\"/kernel/fs/nls/*.ko \"/usr/lib/modules/${KVER}\"/kernel/fs/exfat/*.ko; do\n"
        b"                [ -f \"$mod\" ] && insmod \"$mod\" 2>/dev/null || true\n"
        b"            done\n"
        b"        fi\n"
        b"    fi\n"
        b"    modprobe loop 2>/dev/null || true\n"
        b"    modprobe isofs 2>/dev/null || true\n"
        b"\n"
        b"    mkdir -p /media/mventx_data 2>/dev/null || true\n"
        b"    if ! grep -q \" /media/mventx_data \" /proc/mounts 2>/dev/null; then\n"
        b"        if mount -t exfat -o ro \"$DEVNODE\" /media/mventx_data 2>/dev/null || mount -o ro \"$DEVNODE\" /media/mventx_data 2>/dev/null; then\n"
        b"            if [ -f \"/media/mventx_data/${ISO_REL}\" ]; then\n"
        b"                touch /tmp/.mventx_iso_attached\n"
        b"                echo \"[MVentX] Mounted MVX_DATA ($DEVNODE) on /media/mventx_data (exfat)\" >/dev/kmsg 2>/dev/null || true\n"
        b"                LOOP_DEV=$(losetup -f 2>/dev/null || echo /dev/loop0)\n"
        b"                if losetup -r \"$LOOP_DEV\" \"/media/mventx_data/${ISO_REL}\" 2>/dev/null || losetup \"$LOOP_DEV\" \"/media/mventx_data/${ISO_REL}\" 2>/dev/null; then\n"
        b"                    echo \"[MVentX] Attached Alpine ISO /media/mventx_data/${ISO_REL} to ${LOOP_DEV}\" >/dev/kmsg 2>/dev/null || true\n"
        b"                    LOOP_NAME=\"${LOOP_DEV#/dev/}\"\n"
        b"                    if [ -e \"/sys/class/block/${LOOP_NAME}/uevent\" ]; then\n"
        b"                        echo add > \"/sys/class/block/${LOOP_NAME}/uevent\" 2>/dev/null || true\n"
        b"                    fi\n"
        b"                fi\n"
        b"            else\n"
        b"                umount /media/mventx_data 2>/dev/null || true\n"
        b"            fi\n"
        b"        fi\n"
        b"    fi\n"
        b"fi\n"
        b"exit $MDEV_RC\n"
    )

    ALPINE_NLPLUG_WRAPPER = (
        b"#!/bin/sh\n"
        b"# MVentX Alpine Early-Boot nlplug-findfs Wrapper for exFAT ISO Loopback\n"
        b"KVER=$(uname -r)\n"
        b"\n"
        b"if ! grep -q exfat /proc/filesystems 2>/dev/null; then\n"
        b"    modprobe exfat 2>/dev/null || true\n"
        b"    if ! grep -q exfat /proc/filesystems 2>/dev/null; then\n"
        b"        for mod in \"/usr/lib/modules/${KVER}\"/kernel/fs/nls/*.ko \"/usr/lib/modules/${KVER}\"/kernel/fs/exfat/*.ko; do\n"
        b"            [ -f \"$mod\" ] && insmod \"$mod\" 2>/dev/null || true\n"
        b"        done\n"
        b"    fi\n"
        b"fi\n"
        b"if grep -q exfat /proc/filesystems 2>/dev/null; then\n"
        b"    echo \"[MVentX] exfat kernel module loaded successfully for ${KVER}\" >/dev/kmsg 2>/dev/null || true\n"
        b"    echo \"[MVentX] exfat kernel module loaded successfully for ${KVER}\"\n"
        b"fi\n"
        b"modprobe loop 2>/dev/null || true\n"
        b"modprobe isofs 2>/dev/null || true\n"
        b"modprobe squashfs 2>/dev/null || true\n"
        b"modprobe usb-storage 2>/dev/null || true\n"
        b"modprobe uas 2>/dev/null || true\n"
        b"modprobe sd-mod 2>/dev/null || true\n"
        b"\n"
        b"NL_RC=0\n"
        b"if [ -x /usr/sbin/nlplug-findfs.real ]; then\n"
        b"    /usr/sbin/nlplug-findfs.real \"$@\"\n"
        b"    NL_RC=$?\n"
        b"fi\n"
        b"\n"
        b"if [ -s /tmp/repositories ]; then\n"
        b"    echo \"[MVentX] Alpine boot repository discovered: $(cat /tmp/repositories)\" >/dev/kmsg 2>/dev/null || true\n"
        b"    exit 0\n"
        b"fi\n"
        b"\n"
        b"ISO_PATH=\"\"\n"
        b"for arg in $(cat /proc/cmdline 2>/dev/null); do\n"
        b"    case \"$arg\" in\n"
        b"        iso-scan/filename=*) ISO_PATH=\"${arg#iso-scan/filename=}\" ;;\n"
        b"        findiso=*) ISO_PATH=\"${arg#findiso=}\" ;;\n"
        b"        alpine_iso=*) ISO_PATH=\"${arg#alpine_iso=}\" ;;\n"
        b"    esac\n"
        b"done\n"
        b"ISO_REL=\"${ISO_PATH#/}\"\n"
        b"\n"
        b"if [ -n \"$ISO_REL\" ]; then\n"
        b"    mkdir -p /media/mventx_data /media/cdrom 2>/dev/null || true\n"
        b"    attempt=0\n"
        b"    while [ $attempt -lt 8 ]; do\n"
        b"        if [ ! -f \"/media/mventx_data/${ISO_REL}\" ]; then\n"
        b"            for dev in /dev/sd*[0-9] /dev/vd*[0-9] /dev/nvme*n*p* /dev/mmcblk*p* /dev/hd*[0-9]; do\n"
        b"                [ -b \"$dev\" ] || continue\n"
        b"                grep -q \"^${dev} \" /proc/mounts 2>/dev/null && continue\n"
        b"                if mount -t exfat -o ro \"$dev\" /media/mventx_data 2>/dev/null || mount -o ro \"$dev\" /media/mventx_data 2>/dev/null; then\n"
        b"                    if [ -f \"/media/mventx_data/${ISO_REL}\" ]; then\n"
        b"                        echo \"[MVentX] Mounted MVX_DATA ($dev) on /media/mventx_data\" >/dev/kmsg 2>/dev/null || true\n"
        b"                        break\n"
        b"                    fi\n"
        b"                    umount /media/mventx_data 2>/dev/null || true\n"
        b"                fi\n"
        b"            done\n"
        b"        fi\n"
        b"\n"
        b"        if [ -f \"/media/mventx_data/${ISO_REL}\" ]; then\n"
        b"            if ! grep -q \" /media/cdrom \" /proc/mounts 2>/dev/null && ! grep -q \" /media/loop0 \" /proc/mounts 2>/dev/null; then\n"
        b"                ATTACHED_LOOP=\"\"\n"
        b"                for lsys in /sys/block/loop*/loop/backing_file; do\n"
        b"                    [ -f \"$lsys\" ] || continue\n"
        b"                    if [ \"$(cat \"$lsys\" 2>/dev/null)\" = \"/media/mventx_data/${ISO_REL}\" ]; then\n"
        b"                        lname=\"${lsys#/sys/block/}\"\n"
        b"                        lname=\"${lname%/loop/backing_file}\"\n"
        b"                        ATTACHED_LOOP=\"/dev/${lname}\"\n"
        b"                        break\n"
        b"                    fi\n"
        b"                done\n"
        b"                if [ -n \"$ATTACHED_LOOP\" ]; then\n"
        b"                    mount -t iso9660 -o ro \"$ATTACHED_LOOP\" /media/cdrom 2>/dev/null || true\n"
        b"                else\n"
        b"                    mount -t iso9660 -o loop,ro \"/media/mventx_data/${ISO_REL}\" /media/cdrom 2>/dev/null || true\n"
        b"                fi\n"
        b"            fi\n"
        b"\n"
        b"            for mnt in /media/cdrom /media/loop0; do\n"
        b"                if [ -f \"${mnt}/apks/.boot_repository\" ]; then\n"
        b"                    echo \"${mnt}/apks\" > /tmp/repositories\n"
        b"                    echo \"[MVentX] Alpine ISO mounted at ${mnt}; repository=${mnt}/apks\" >/dev/kmsg 2>/dev/null || true\n"
        b"                    echo \"[MVentX] Alpine ISO mounted at ${mnt}; repository=${mnt}/apks\"\n"
        b"                    exit 0\n"
        b"                fi\n"
        b"            done\n"
        b"        fi\n"
        b"\n"
        b"        attempt=$((attempt + 1))\n"
        b"        sleep 1\n"
        b"    done\n"
        b"fi\n"
        b"\n"
        b"exit $NL_RC\n"
    )

    SQUASHFS_CANDIDATE_PATTERNS = (
        "*filesystem.squashfs",
        "casper/minimal.squashfs",
        "*minimal.squashfs",
        "*ubuntu-server-minimal.squashfs",
        "*minimal.standard.live.squashfs",
        "*minimal.standard.squashfs",
    )

    ALPINE_MODLOOP_PATTERNS = (
        "boot/modloop-*",
        "*modloop-*",
        "*modloop*",
    )

    @classmethod
    def can_generate_overlay(
        cls,
        iso_path: Union[Path, str],
        seven_zip_path: Optional[Union[Path, str]] = None,
    ) -> bool:
        """Check if 7-Zip is available and the target ISO contains a Live SquashFS or Alpine modloop image."""
        tool_7z = Path(seven_zip_path) if seven_zip_path else find_seven_zip()
        if not tool_7z or not tool_7z.is_file():
            return False
        iso_p = Path(iso_path)
        if not iso_p.is_file():
            return False
        try:
            cmd = [str(tool_7z), "l", str(iso_p), "*.squashfs*", "*modloop*", "-r"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            out_low = res.stdout.lower()
            return bool(res.returncode == 0 and (".squashfs" in out_low or "modloop" in out_low))
        except Exception:
            return False

    @classmethod
    def extract_file_from_initramfs(
        cls,
        initrd_file: Path,
        target_rel_paths: Tuple[str, ...],
    ) -> Optional[bytes]:
        """Extract a specific file (such as usr/sbin/nlplug-findfs) from a gzip/raw CPIO initramfs."""
        if not initrd_file.is_file():
            return None
        try:
            raw = initrd_file.read_bytes()
            if raw[:2] == b"\x1f\x8b":
                cpio_bytes = gzip.decompress(raw)
            elif raw[:6] == b"\xfd7zXZ\x00":
                cpio_bytes = lzma.decompress(raw)
            else:
                cpio_bytes = raw
            entries = parse_newc_cpio_entries(cpio_bytes)
            for rel in target_rel_paths:
                norm = rel.lstrip("./")
                if norm in entries and len(entries[norm]) > 0:
                    return entries[norm]
        except Exception:
            pass
        return None

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
        recipe: Optional[str] = None,
        kernel_path: Optional[str] = None,
        initrd_path: Optional[str] = None,
    ) -> OverlayGenerationResult:
        """Extract exfat.ko from an Ubuntu Casper or Alpine Linux ISO and build a bootable CPIO overlay.

        Args:
            iso_path: Path to the source ISO file.
            output_cpio_path: Destination path on MVX_BOOT for the generated CPIO archive.
            target_kernel_version: Optional explicit kernel version string. If omitted,
                                  automatically parsed from the ISO's vmlinuz boot header.
            seven_zip_path: Optional path to 7z.exe executable.
            recipe: Optional recipe hint ('casper' or 'alpine').
            kernel_path: Optional discovered/configured kernel path inside ISO (e.g. '/boot/vmlinuz-lts').
            initrd_path: Optional discovered/configured initramfs path inside ISO (e.g. '/boot/initramfs-lts').

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

        # Determine if target flavour is specified for Alpine (e.g. 'lts' or 'virt')
        flavour_hint: Optional[str] = None
        for cand_p in (kernel_path, initrd_path):
            if cand_p:
                base = cand_p.strip().replace("\\", "/").rsplit("/", 1)[-1]
                if "-" in base:
                    flavour_hint = base.split("-", 1)[1]
                    break
                elif "_" in base:
                    flavour_hint = base.split("_", 1)[1]
                    break

        temp_workspace = Path(tempfile.mkdtemp(prefix="mventx_overlay_build_"))
        try:
            # 1. Identify kernel version if not provided
            kernel_ver = target_kernel_version
            vmlinuz_dir = temp_workspace / "vmlinuz_extract"
            vmlinuz_dir.mkdir(parents=True, exist_ok=True)
            vmlinuz_pattern = f"*vmlinuz-{flavour_hint}" if flavour_hint else "*vmlinuz*"
            cmd_k = [str(tool_7z), "e", str(iso_p), f"-o{vmlinuz_dir}", vmlinuz_pattern, "-r", "-y"]
            subprocess.run(cmd_k, check=False, capture_output=True)
            vmlinuz_candidates = sorted(vmlinuz_dir.glob("*vmlinuz*"))
            if not vmlinuz_candidates and flavour_hint:
                cmd_k_any = [str(tool_7z), "e", str(iso_p), f"-o{vmlinuz_dir}", "*vmlinuz*", "-r", "-y"]
                subprocess.run(cmd_k_any, check=False, capture_output=True)
                vmlinuz_candidates = sorted(vmlinuz_dir.glob("*vmlinuz*"))
            if vmlinuz_candidates:
                selected_vmlinuz = vmlinuz_candidates[0]
                if not flavour_hint and "-" in selected_vmlinuz.name:
                    flavour_hint = selected_vmlinuz.name.split("-", 1)[1]
                if not kernel_ver:
                    k_data = selected_vmlinuz.read_bytes()
                    kernel_ver = KernelModuleInspector.extract_vmlinuz_version(k_data)

            # 2. Extract Live SquashFS or Alpine modloop and locate exfat.ko + NLS modules
            sqfs_dir = temp_workspace / "squashfs_stage"
            sqfs_dir.mkdir(parents=True, exist_ok=True)
            modules_dir = temp_workspace / "extracted_modules"
            modules_dir.mkdir(parents=True, exist_ok=True)

            exfat_files: List[Path] = []
            tried_squashfs: List[str] = []
            last_sq_err = ""
            is_alpine_overlay = bool(recipe and recipe.strip().lower() in ("alpine", "alpine_linux", "linux_alpine"))

            # Build search pattern sequence based on whether Alpine or Casper is targeted
            if is_alpine_overlay:
                if flavour_hint:
                    candidate_patterns: Tuple[str, ...] = (
                        f"boot/modloop-{flavour_hint}",
                        f"*modloop-{flavour_hint}",
                        *cls.ALPINE_MODLOOP_PATTERNS,
                    )
                else:
                    candidate_patterns = cls.ALPINE_MODLOOP_PATTERNS
            else:
                candidate_patterns = (*cls.SQUASHFS_CANDIDATE_PATTERNS, *cls.ALPINE_MODLOOP_PATTERNS)

            for pattern in candidate_patterns:
                cmd_sq = [str(tool_7z), "e", str(iso_p), f"-o{sqfs_dir}", pattern, "-r", "-y"]
                proc_sq = subprocess.run(cmd_sq, capture_output=True, text=True)
                if proc_sq.returncode != 0:
                    last_sq_err = proc_sq.stderr or proc_sq.stdout or ""
                    continue

                sqfs_candidates = sorted(
                    [p for p in sqfs_dir.iterdir() if p.is_file()],
                    key=lambda p: (
                        0 if (flavour_hint and p.name == f"modloop-{flavour_hint}") else
                        1 if p.name in ("filesystem.squashfs", "minimal.squashfs", "modloop-lts", "modloop-virt") else 2,
                        len(p.name),
                    ),
                )
                if not sqfs_candidates:
                    continue

                for sqfs_path in sqfs_candidates:
                    # If a specific Alpine flavour was requested and multiple modloops exist, enforce flavour match
                    if flavour_hint and sqfs_path.name.startswith("modloop-") and sqfs_path.name != f"modloop-{flavour_hint}":
                        if any(c.name == f"modloop-{flavour_hint}" for c in sqfs_candidates):
                            continue

                    if sqfs_path.name.startswith("modloop"):
                        is_alpine_overlay = True

                    tried_squashfs.append(sqfs_path.name)
                    cmd_exfat = [str(tool_7z), "e", str(sqfs_path), f"-o{modules_dir}", "*exfat.ko*", "-r", "-y"]
                    proc_exfat = subprocess.run(cmd_exfat, capture_output=True, text=True)
                    if proc_exfat.returncode != 0:
                        last_sq_err = proc_exfat.stderr or proc_exfat.stdout or ""

                    found_now = [f for f in modules_dir.glob("*exfat.ko*") if f.is_file()]
                    if found_now:
                        # Also extract supporting NLS modules and module dependency indices from the same SquashFS layer
                        cmd_nls = [
                            str(tool_7z),
                            "e",
                            str(sqfs_path),
                            f"-o{modules_dir}",
                            "*nls_utf8.ko*",
                            "*nls_iso8859-1.ko*",
                            "*nls_cp437.ko*",
                            "*nls_ascii.ko*",
                            "*modules.dep*",
                            "*modules.alias*",
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

                # Clean up any remaining archive files in sqfs_dir before next pattern
                for rem in sqfs_dir.iterdir():
                    try:
                        if rem.is_file():
                            rem.unlink()
                    except OSError:
                        pass

                if exfat_files:
                    break

            if not tried_squashfs:
                raise OverlayGenerationError(
                    f"No Live SquashFS or modloop image (filesystem.squashfs / minimal.squashfs / modloop-*) "
                    f"found inside ISO '{iso_p.name}'. {last_sq_err}".strip()
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
                    for trusted in (
                        "Canonical",
                        "Ubuntu",
                        "Alpine Linux kernel key",
                        "Alpine",
                        "Build time autogenerated kernel key",
                    )
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
            # on usrmerged Ubuntu and Alpine initramfs archives.
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
                for s_name, s_bytes in sorted(supporting_mods.items()):
                    cpio_entries.append((f"usr/lib/modules/{kernel_ver}/kernel/fs/nls/{s_name}", 0o100755, s_bytes))
                    extracted_names.append(s_name)

            if is_alpine_overlay:
                # Include module dependency and alias metadata for Alpine's libkmod / modprobe / nlplug-findfs
                dep_file = modules_dir / "modules.dep"
                dep_bin_file = modules_dir / "modules.dep.bin"
                alias_file = modules_dir / "modules.alias"
                alias_bin_file = modules_dir / "modules.alias.bin"

                if dep_file.is_file():
                    cpio_entries.append((f"usr/lib/modules/{kernel_ver}/modules.dep", 0o100644, dep_file.read_bytes()))
                else:
                    synth_dep = ["kernel/fs/exfat/exfat.ko:"]
                    for s_name in sorted(supporting_mods.keys()):
                        synth_dep.append(f"kernel/fs/nls/{s_name}:")
                    cpio_entries.append(
                        (f"usr/lib/modules/{kernel_ver}/modules.dep", 0o100644, ("\n".join(synth_dep) + "\n").encode("utf-8"))
                    )

                if dep_bin_file.is_file():
                    cpio_entries.append((f"usr/lib/modules/{kernel_ver}/modules.dep.bin", 0o100644, dep_bin_file.read_bytes()))

                if alias_file.is_file():
                    cpio_entries.append((f"usr/lib/modules/{kernel_ver}/modules.alias", 0o100644, alias_file.read_bytes()))
                else:
                    cpio_entries.append((f"usr/lib/modules/{kernel_ver}/modules.alias", 0o100644, b"alias fs-exfat exfat\n"))

                if alias_bin_file.is_file():
                    cpio_entries.append((f"usr/lib/modules/{kernel_ver}/modules.alias.bin", 0o100644, alias_bin_file.read_bytes()))

                cpio_entries.append(("etc", 0o040755, b""))
                cpio_entries.append(("etc/modules", 0o100644, b"exfat\nisofs\nloop\nsquashfs\n"))

                # Extract stock usr/sbin/nlplug-findfs from the matching Alpine initramfs if present
                initrd_extract_dir = temp_workspace / "initrd_extract"
                initrd_extract_dir.mkdir(parents=True, exist_ok=True)
                initrd_pattern = f"*initramfs-{flavour_hint}" if flavour_hint else "*initramfs*"
                subprocess.run(
                    [str(tool_7z), "e", str(iso_p), f"-o{initrd_extract_dir}", initrd_pattern, "-r", "-y"],
                    check=False,
                    capture_output=True,
                )
                initrd_candidates = sorted(initrd_extract_dir.glob("*initramfs*"))
                nlplug_real_bytes: Optional[bytes] = None
                if initrd_candidates:
                    nlplug_real_bytes = cls.extract_file_from_initramfs(
                        initrd_candidates[0],
                        ("usr/sbin/nlplug-findfs", "sbin/nlplug-findfs"),
                    )

                cpio_entries.append(("usr/sbin", 0o040755, b""))
                cpio_entries.append(("usr/sbin/mdev", 0o100755, cls.ALPINE_MDEV_HOOK))
                if nlplug_real_bytes:
                    cpio_entries.append(("usr/sbin/nlplug-findfs.real", 0o100755, nlplug_real_bytes))
                    cpio_entries.append(("usr/sbin/nlplug-findfs", 0o100755, cls.ALPINE_NLPLUG_WRAPPER))
                else:
                    cpio_entries.append(("usr/sbin/mventx-alpine-mount", 0o100755, cls.ALPINE_NLPLUG_WRAPPER))
            else:
                # Ubuntu / Casper early-boot configuration and hooks
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
