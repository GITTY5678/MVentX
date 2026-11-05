#!/usr/bin/env python3
"""Native Windows FAT / GPT Virtual Disk Builder for MVentX.

Constructs a bootable UEFI GPT disk image containing an EFI System Partition (ESP)
formatted as FAT16/FAT32 from a source directory.

Runs with pure Python standard library modules without requiring:
- Administrator privileges
- WSL2 / Linux utilities (parted, mtools, mkfs.fat)
- Third-party drivers (ImDisk, WinFsp)
"""

from __future__ import annotations

import argparse
import math
import os
import struct
import sys
import uuid
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def generate_83_name(filename: str, existing_names: set[str]) -> bytes:
    """Generate a unique FAT 8.3 short filename (11 bytes uppercase)."""
    parts = filename.upper().split(".")
    base = parts[0].replace(" ", "").replace(".", "")[:8]
    ext = parts[1][:3] if len(parts) > 1 else ""

    candidate = f"{base:<8}{ext:<3}".encode("ascii", "replace")[:11]
    if candidate not in existing_names:
        # pyrefly: ignore[bad-argument-type]
        existing_names.add(candidate)
        return candidate

    # If collision, truncate and add ~1, ~2, etc.
    counter = 1
    while True:
        suffix = f"~{counter}"
        avail = 8 - len(suffix)
        sub_base = base[:avail] + suffix
        candidate = f"{sub_base:<8}{ext:<3}".encode("ascii", "replace")[:11]
        if candidate not in existing_names:
            # pyrefly: ignore[bad-argument-type]
            existing_names.add(candidate)
            return candidate
        counter += 1


def vfat_checksum(short_name: bytes) -> int:
    """Calculate VFAT short name checksum."""
    chk = 0
    for b in short_name[:11]:
        chk = (((chk & 1) << 7) + (chk >> 1) + b) & 0xFF
    return chk


def make_vfat_entries(long_name: str, short_name: bytes) -> bytes:
    """Generate VFAT long filename directory entries."""
    chk = vfat_checksum(short_name)
    chars = [ord(c) for c in long_name] + [0]  # Null-terminate
    # Pad with 0xFFFF to multiple of 13
    remainder = len(chars) % 13
    if remainder != 0:
        chars.extend([0xFFFF] * (13 - remainder))

    num_entries = len(chars) // 13
    entries = bytearray()

    for i in range(num_entries):
        seq = (num_entries - i)
        if i == 0:
            seq |= 0x40  # Mark last logical entry

        chunk = chars[(num_entries - 1 - i) * 13 : (num_entries - i) * 13]
        part1 = struct.pack("<5H", *chunk[0:5])
        part2 = struct.pack("<6H", *chunk[5:11])
        part3 = struct.pack("<2H", *chunk[11:13])

        entry = bytearray(32)
        entry[0] = seq
        entry[1:11] = part1
        entry[11] = 0x0F  # VFAT attribute (read-only, hidden, system, volume_id)
        entry[12] = 0x00  # Type
        entry[13] = chk
        entry[14:26] = part2
        entry[26:28] = b"\x00\x00"  # Cluster
        entry[28:32] = part3

        entries.extend(entry)

    return bytes(entries)


class SimpleFat16Builder:
    """Constructs a standard FAT16 filesystem image in memory."""

    def __init__(self, size_bytes: int, volume_label: str = "MVX_BOOT") -> None:
        self.size_bytes = size_bytes
        self.volume_label = f"{volume_label:<11}"[:11].encode("ascii")

        self.bytes_per_sector = 512
        self.total_sectors = size_bytes // self.bytes_per_sector
        self.sectors_per_cluster = 4  # 2KB clusters
        self.cluster_size = self.bytes_per_sector * self.sectors_per_cluster

        self.reserved_sectors = 4
        self.num_fats = 2
        self.root_dir_entries = 512  # 32 sectors

        # Calculate clusters and FAT size
        root_dir_sectors = (self.root_dir_entries * 32) // self.bytes_per_sector
        data_sectors = self.total_sectors - (self.reserved_sectors + root_dir_sectors)
        # Approximate sectors per FAT
        est_clusters = data_sectors // self.sectors_per_cluster
        self.sectors_per_fat = max(16, (est_clusters * 2 + self.bytes_per_sector - 1) // self.bytes_per_sector)

        self.fat_offset = self.reserved_sectors * self.bytes_per_sector
        self.root_dir_offset = (self.reserved_sectors + self.num_fats * self.sectors_per_fat) * self.bytes_per_sector
        self.data_offset = self.root_dir_offset + (self.root_dir_entries * 32)

        self.total_clusters = (size_bytes - self.data_offset) // self.cluster_size
        if self.total_clusters >= 65525:
            self.total_clusters = 65524

        # Allocate filesystem buffer
        self.fs_buffer = bytearray(size_bytes)

        # Allocate FAT tables
        self.fat = [0] * (self.total_clusters + 2)
        self.fat[0] = 0xFFF8  # Media descriptor (hard disk)
        self.fat[1] = 0xFFFF  # End of cluster chain marker

        self.allocated_clusters = 2
        self._write_vbr()

    def _write_vbr(self) -> None:
        """Write Volume Boot Record (VBR)."""
        vbr = bytearray(512)
        # JMP instruction
        vbr[0:3] = b"\xeb\x3c\x90"
        # OEM Name
        vbr[3:11] = b"MVENTX  "
        # BPB fields
        struct.pack_into("<H", vbr, 11, self.bytes_per_sector)
        vbr[13] = self.sectors_per_cluster
        struct.pack_into("<H", vbr, 14, self.reserved_sectors)
        vbr[16] = self.num_fats
        struct.pack_into("<H", vbr, 17, self.root_dir_entries)

        if self.total_sectors < 65536:
            struct.pack_into("<H", vbr, 19, self.total_sectors)
            struct.pack_into("<I", vbr, 32, 0)
        else:
            struct.pack_into("<H", vbr, 19, 0)
            struct.pack_into("<I", vbr, 32, self.total_sectors)

        vbr[21] = 0xF8  # Media descriptor (fixed disk)
        struct.pack_into("<H", vbr, 22, self.sectors_per_fat)
        struct.pack_into("<H", vbr, 24, 32)  # Sectors per track
        struct.pack_into("<H", vbr, 26, 64)  # Heads
        struct.pack_into("<I", vbr, 28, 0)   # Hidden sectors

        # Extended BPB
        vbr[36] = 0x80  # Drive number
        vbr[38] = 0x29  # Extended boot signature
        struct.pack_into("<I", vbr, 39, 0x19842026)  # Volume serial number
        vbr[43:54] = self.volume_label
        vbr[54:62] = b"FAT16   "

        # Boot signature
        vbr[510:512] = b"\x55\xaa"

        self.fs_buffer[0:512] = vbr

    def allocate_file(self, data: bytes) -> int:
        """Allocate clusters for file content and write data."""
        if not data:
            return 0  # Empty file has cluster 0

        clusters_needed = math.ceil(len(data) / self.cluster_size)
        start_cluster = self.allocated_clusters

        for i in range(clusters_needed):
            curr_cluster = start_cluster + i
            if curr_cluster >= len(self.fat) - 1:
                raise RuntimeError("Disk full: ran out of FAT clusters")

            # Link FAT
            if i == clusters_needed - 1:
                self.fat[curr_cluster] = 0xFFFF  # EOF
            else:
                self.fat[curr_cluster] = curr_cluster + 1

            # Write cluster payload
            chunk = data[i * self.cluster_size : (i + 1) * self.cluster_size]
            offset = self.data_offset + (curr_cluster - 2) * self.cluster_size
            self.fs_buffer[offset : offset + len(chunk)] = chunk

        self.allocated_clusters += clusters_needed
        return start_cluster

    def build_from_directory(self, source_dir: Path) -> bytes:
        """Recursively populate FAT filesystem from host directory."""
        source_dir = Path(source_dir).resolve()
        if not source_dir.exists():
            raise FileNotFoundError(f"Source directory not found: {source_dir}")

        root_entries = bytearray()
        existing_root_names: set[str] = set()

        # Volume label entry in root directory
        vol_entry = bytearray(32)
        vol_entry[0:11] = self.volume_label
        vol_entry[11] = 0x08  # Volume ID attribute
        root_entries.extend(vol_entry)

        def process_directory(dir_path: Path, is_root: bool, parent_cluster: int = 0) -> int:
            nonlocal root_entries
            dir_entries = bytearray()
            existing_names: set[str] = set()

            items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

            # If subdirectory, add '.' and '..' entries first
            dir_cluster = 0
            if not is_root:
                dir_cluster = self.allocated_clusters
                self.allocated_clusters += 1
                self.fat[dir_cluster] = 0xFFFF

                dot = bytearray(32)
                dot[0:11] = b".          "
                dot[11] = 0x10  # Directory
                struct.pack_into("<H", dot, 26, dir_cluster)
                dir_entries.extend(dot)

                dotdot = bytearray(32)
                dotdot[0:11] = b"..         "
                dotdot[11] = 0x10  # Directory
                struct.pack_into("<H", dotdot, 26, parent_cluster)
                dir_entries.extend(dotdot)

            for item in items:
                short_name = generate_83_name(item.name, existing_root_names if is_root else existing_names)
                vfat = make_vfat_entries(item.name, short_name)

                entry = bytearray(32)
                entry[0:11] = short_name

                if item.is_dir():
                    entry[11] = 0x10  # Subdirectory
                    sub_cluster = process_directory(item, is_root=False, parent_cluster=dir_cluster)
                    struct.pack_into("<H", entry, 26, sub_cluster)
                    struct.pack_into("<I", entry, 28, 0)
                else:
                    entry[11] = 0x20  # Archive / regular file
                    file_bytes = item.read_bytes()
                    file_cluster = self.allocate_file(file_bytes)
                    struct.pack_into("<H", entry, 26, file_cluster)
                    struct.pack_into("<I", entry, 28, len(file_bytes))

                if is_root:
                    root_entries.extend(vfat)
                    root_entries.extend(entry)
                else:
                    dir_entries.extend(vfat)
                    dir_entries.extend(entry)

            if not is_root:
                # Write dir_entries into cluster
                offset = self.data_offset + (dir_cluster - 2) * self.cluster_size
                self.fs_buffer[offset : offset + len(dir_entries)] = dir_entries
                return dir_cluster

            return 0

        process_directory(source_dir, is_root=True)

        # Write root directory table
        if len(root_entries) > self.root_dir_entries * 32:
            raise RuntimeError("Root directory overflow: too many entries")
        self.fs_buffer[self.root_dir_offset : self.root_dir_offset + len(root_entries)] = root_entries

        # Pack FAT table into buffer
        raw_fat = bytearray()
        for val in self.fat:
            raw_fat.extend(struct.pack("<H", val))
        # Pad FAT to sectors_per_fat
        fat_bytes_len = self.sectors_per_fat * self.bytes_per_sector
        raw_fat.extend(b"\x00" * (fat_bytes_len - len(raw_fat)))

        # Write primary and secondary FATs
        for f_idx in range(self.num_fats):
            f_offset = self.fat_offset + f_idx * fat_bytes_len
            self.fs_buffer[f_offset : f_offset + fat_bytes_len] = raw_fat

        return bytes(self.fs_buffer)


def build_gpt_disk_image(
    source_dir: Path,
    output_path: Path,
    disk_size_mb: int = 64,
    volume_label: str = "MVX_BOOT",
) -> None:
    """Build a GPT-partitioned raw disk image with an EFI System Partition."""
    disk_size_bytes = disk_size_mb * 1024 * 1024
    total_sectors = disk_size_bytes // 512

    # Partition 1 starts at 1MiB (sector 2048) and ends 34 sectors before disk end
    start_lba = 2048
    end_lba = total_sectors - 34
    part_sectors = end_lba - start_lba + 1
    part_size_bytes = part_sectors * 512

    # 1. Build FAT filesystem in memory
    fat_builder = SimpleFat16Builder(size_bytes=part_size_bytes, volume_label=volume_label)
    fat_image = fat_builder.build_from_directory(source_dir)

    # 2. Allocate complete disk buffer
    disk = bytearray(disk_size_bytes)

    # 3. Protective MBR (Sector 0)
    pmbr = bytearray(512)
    # Partition entry 1 at offset 446
    struct.pack_into(
        "<B3sB3sII",
        pmbr,
        446,
        0x00,               # Bootable
        b"\x00\x02\x00",    # Starting CHS
        0xEE,               # Type: GPT Protective
        b"\xff\xff\xff",    # Ending CHS
        1,                  # Starting LBA
        total_sectors - 1,  # Size in LBA
    )
    pmbr[510:512] = b"\x55\xaa"
    disk[0:512] = pmbr

    # 4. Partition Table Entries (128 entries of 128 bytes = 16384 bytes = 32 sectors)
    # EFI System Partition Type GUID: C12A7328-F81F-11D2-BA4B-00A0C93EC93B
    esp_type_guid = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B").bytes_le
    part_unique_guid = uuid.uuid4().bytes_le

    part_entry = bytearray(128)
    part_entry[0:16] = esp_type_guid
    part_entry[16:32] = part_unique_guid
    struct.pack_into("<Q", part_entry, 32, start_lba)
    struct.pack_into("<Q", part_entry, 40, end_lba)
    struct.pack_into("<Q", part_entry, 48, 0)  # Attributes
    part_name = "EFI System Partition".encode("utf-16le")
    part_entry[56 : 56 + len(part_name)] = part_name

    partition_array = bytearray(128 * 128)
    partition_array[0:128] = part_entry
    part_crc = zlib.crc32(partition_array)

    # 5. Primary GPT Header (Sector 1)
    disk_guid = uuid.uuid4().bytes_le
    gpt_header = bytearray(92)
    gpt_header[0:8] = b"EFI PART"
    struct.pack_into("<I", gpt_header, 8, 0x00010000)  # Version 1.0
    struct.pack_into("<I", gpt_header, 12, 92)         # Header size
    struct.pack_into("<I", gpt_header, 16, 0)          # CRC32 placeholder
    struct.pack_into("<I", gpt_header, 20, 0)          # Reserved
    struct.pack_into("<Q", gpt_header, 24, 1)          # Current LBA
    struct.pack_into("<Q", gpt_header, 32, total_sectors - 1)  # Backup LBA
    struct.pack_into("<Q", gpt_header, 40, start_lba)  # First usable LBA
    struct.pack_into("<Q", gpt_header, 48, end_lba)    # Last usable LBA
    gpt_header[56:72] = disk_guid
    struct.pack_into("<Q", gpt_header, 72, 2)          # Partition array LBA
    struct.pack_into("<I", gpt_header, 80, 128)        # Number of partition entries
    struct.pack_into("<I", gpt_header, 84, 128)        # Size of each entry
    struct.pack_into("<I", gpt_header, 88, part_crc)   # Partition array CRC32

    # Calculate Header CRC32
    hdr_crc = zlib.crc32(gpt_header)
    struct.pack_into("<I", gpt_header, 16, hdr_crc)

    disk[512 : 512 + 92] = gpt_header
    disk[1024 : 1024 + len(partition_array)] = partition_array

    # 6. Burn Partition 1 (FAT Image) at 1MiB offset (sector 2048)
    fat_offset = start_lba * 512
    disk[fat_offset : fat_offset + len(fat_image)] = fat_image

    # 7. Backup Partition Array and Backup GPT Header at disk end
    backup_part_lba = total_sectors - 33
    backup_header_lba = total_sectors - 1

    disk[backup_part_lba * 512 : (backup_part_lba + 32) * 512] = partition_array

    backup_header = bytearray(gpt_header)
    struct.pack_into("<Q", backup_header, 24, backup_header_lba)  # Current LBA
    struct.pack_into("<Q", backup_header, 32, 1)                  # Backup LBA
    struct.pack_into("<Q", backup_header, 72, backup_part_lba)    # Partition array LBA
    struct.pack_into("<I", backup_header, 16, 0)                  # Reset CRC
    backup_crc = zlib.crc32(backup_header)
    struct.pack_into("<I", backup_header, 16, backup_crc)

    disk[backup_header_lba * 512 : backup_header_lba * 512 + 92] = backup_header

    # 8. Write output atomically
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.tmp")

    with open(temp_path, "wb") as f:
        f.write(disk)
        f.flush()
        os.fsync(f.fileno())

    temp_path.replace(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build UEFI GPT/FAT disk image from directory")
    parser.add_argument("--source", type=Path, required=True, help="Source directory (e.g. build/esp)")
    parser.add_argument("--output", type=Path, required=True, help="Output image file path (e.g. build/mventx_test.img)")
    parser.add_argument("--size-mb", type=int, default=64, help="Total disk image size in MB (default: 64)")
    parser.add_argument("--label", type=str, default="MVX_BOOT", help="FAT Volume label (default: MVX_BOOT)")

    args = parser.parse_args()

    try:
        build_gpt_disk_image(
            source_dir=args.source,
            output_path=args.output,
            disk_size_mb=args.size_mb,
            volume_label=args.label,
        )
        print(f"Successfully created GPT virtual disk image: {args.output}")
        return 0
    except Exception as exc:
        print(f"Error building virtual disk image: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

