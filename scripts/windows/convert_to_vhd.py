"""Virtual Disk Image Format Converter for MVentX.

Converts raw GPT disk images (.img) into Microsoft Virtual Hard Disk (.vhd / .vhdx) formats:
1. Pure-Python Fixed VHD generator (appends Microsoft-compliant 512-byte Connectix footer).
2. QEMU-img dynamic VHD (vpc) and VHDX converter when qemu-img is available.
"""

from __future__ import annotations

import argparse
import datetime
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import uuid


def compute_vhd_geometry(total_sectors: int) -> tuple[int, int, int]:
    """Compute CHS geometry for VHD footer according to Microsoft Virtual Hard Disk spec."""
    if total_sectors > 65535 * 16 * 255:
        total_sectors = 65535 * 16 * 255

    if total_sectors >= 65535 * 16 * 63:
        sectors_per_track = 255
        heads_per_cylinder = 16
        cylinder_times_heads = total_sectors // sectors_per_track
    else:
        sectors_per_track = 17
        cylinder_times_heads = total_sectors // sectors_per_track
        heads_per_cylinder = (cylinder_times_heads + 1023) // 1024
        if heads_per_cylinder < 4:
            heads_per_cylinder = 4
        if cylinder_times_heads >= (heads_per_cylinder * 1024) or heads_per_cylinder > 16:
            sectors_per_track = 31
            heads_per_cylinder = 16
            cylinder_times_heads = total_sectors // sectors_per_track
        if cylinder_times_heads >= (heads_per_cylinder * 1024):
            sectors_per_track = 63
            heads_per_cylinder = 16
            cylinder_times_heads = total_sectors // sectors_per_track

    cylinders = cylinder_times_heads // heads_per_cylinder
    if cylinders > 65535:
        cylinders = 65535

    return cylinders, heads_per_cylinder, sectors_per_track


def build_vhd_footer(disk_size_bytes: int) -> bytes:
    """Generate 512-byte Microsoft Connectix Fixed VHD footer."""
    footer = bytearray(512)
    # 0..7: Cookie
    footer[0:8] = b"conectix"
    # 8..11: Features (0x00000002)
    struct.pack_into(">I", footer, 8, 0x00000002)
    # 12..15: File Format Version (1.0 = 0x00010000)
    struct.pack_into(">I", footer, 12, 0x00010000)
    # 16..23: Data Offset (0xFFFFFFFFFFFFFFFF for fixed disk)
    struct.pack_into(">Q", footer, 16, 0xFFFFFFFFFFFFFFFF)

    # 24..27: Timestamp (seconds since 2000-01-01 00:00:00 UTC)
    epoch_2000 = datetime.datetime(2000, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    timestamp = int((now_utc - epoch_2000).total_seconds())
    struct.pack_into(">I", footer, 24, timestamp & 0xFFFFFFFF)

    # 28..31: Creator Application
    footer[28:32] = b"mvtx"
    # 32..35: Creator Version
    struct.pack_into(">I", footer, 32, 0x00010000)
    # 36..39: Creator Host OS (Wi2k for Windows)
    footer[36:40] = b"Wi2k"

    # 40..47: Original Size
    struct.pack_into(">Q", footer, 40, disk_size_bytes)
    # 48..55: Current Size
    struct.pack_into(">Q", footer, 48, disk_size_bytes)

    # 56..59: Disk Geometry
    total_sectors = disk_size_bytes // 512
    cyl, heads, spt = compute_vhd_geometry(total_sectors)
    struct.pack_into(">H", footer, 56, cyl)
    footer[58] = heads
    footer[59] = spt

    # 60..63: Disk Type (0x00000002 = Fixed Hard Disk)
    struct.pack_into(">I", footer, 60, 0x00000002)

    # 68..83: Unique ID (UUID)
    footer[68:84] = uuid.uuid4().bytes

    # 64..67: Checksum (One's complement of the sum of all bytes in footer with checksum zeroed)
    struct.pack_into(">I", footer, 64, 0)
    chk = sum(footer)
    chk_final = (~chk) & 0xFFFFFFFF
    struct.pack_into(">I", footer, 64, chk_final)

    return bytes(footer)


def convert_raw_to_fixed_vhd(raw_image_path: Path, output_vhd_path: Path) -> Path:
    """Create a fixed VHD by copying raw image and appending 512-byte Connectix footer."""
    raw_path = Path(raw_image_path).resolve()
    vhd_path = Path(output_vhd_path).resolve()

    if not raw_path.is_file():
        raise FileNotFoundError(f"Source raw image not found: {raw_path}")

    size_bytes = raw_path.stat().st_size
    if size_bytes % 512 != 0:
        raise ValueError(f"Raw image size must be multiple of 512 bytes, got {size_bytes}")

    vhd_path.parent.mkdir(parents=True, exist_ok=True)
    footer = build_vhd_footer(size_bytes)

    # Copy raw image to destination and append footer
    shutil.copyfile(raw_path, vhd_path)
    with open(vhd_path, "ab") as f:
        f.write(footer)

    return vhd_path


def find_qemu_img() -> Optional[Path]:
    """Locate qemu-img executable on the host system."""
    candidate = shutil.which("qemu-img")
    if candidate:
        return Path(candidate)

    default_paths = [
        Path(r"C:\Program Files\qemu\qemu-img.exe"),
        Path(r"C:\Program Files (x86)\qemu\qemu-img.exe"),
    ]
    for p in default_paths:
        if p.is_file():
            return p

    return None


def convert_with_qemu_img(raw_image_path: Path, output_path: Path, target_format: str = "vpc") -> bool:
    """Convert raw image to VHD (vpc) or VHDX using qemu-img."""
    qemu_img = find_qemu_img()
    if not qemu_img:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(qemu_img),
        "convert",
        "-O",
        target_format,
        str(raw_image_path),
        str(output_path),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert raw MVentX disk image to VHD or VHDX")
    parser.add_argument("--input", type=Path, required=True, help="Path to input raw .img file")
    parser.add_argument("--output", type=Path, required=True, help="Path to output .vhd or .vhdx file")
    parser.add_argument(
        "--format",
        choices=["vhd-fixed", "vhd-dynamic", "vhdx"],
        default="vhd-fixed",
        help="Target format (vhd-fixed: pure Python; vhd-dynamic / vhdx: requires qemu-img)",
    )

    args = parser.parse_args()

    try:
        if args.format == "vhd-fixed":
            print(f"[*] Generating pure-Python Fixed VHD: {args.output}")
            convert_raw_to_fixed_vhd(args.input, args.output)
            print(f"[+] Successfully created Fixed VHD: {args.output} ({args.output.stat().st_size} bytes)")
        elif args.format == "vhd-dynamic":
            print(f"[*] Converting to dynamic VHD via qemu-img...")
            ok = convert_with_qemu_img(args.input, args.output, "vpc")
            if not ok:
                print("[!] qemu-img conversion failed. Falling back to fixed VHD.", file=sys.stderr)
                convert_raw_to_fixed_vhd(args.input, args.output)
            print(f"[+] Created VHD: {args.output}")
        elif args.format == "vhdx":
            print(f"[*] Converting to VHDX via qemu-img...")
            ok = convert_with_qemu_img(args.input, args.output, "vhdx")
            if not ok:
                raise RuntimeError("qemu-img is required for VHDX conversion but failed or was not found.")
            print(f"[+] Created VHDX: {args.output}")
        return 0
    except Exception as exc:
        print(f"[!] Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

