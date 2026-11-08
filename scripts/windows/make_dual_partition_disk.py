#!/usr/bin/env python3
"""MVentX Dual-Partition Virtual Disk Builder.

Constructs a bootable UEFI GPT raw disk image with two distinct partitions:
- Partition 1: MVX_BOOT (EFI System Partition, FAT32)
- Partition 2: MVX_DATA (Microsoft Basic Data Partition, exFAT)

Operates with standard Python 3 without requiring root/Administrator privileges.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import uuid
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ==============================================================================
# 1. FAT32 Filesystem Builder (MVX_BOOT)
# ==============================================================================

def generate_83_name(filename: str, existing_names: Set[bytes]) -> bytes:
    """Generate a unique FAT 8.3 short filename (11 bytes uppercase)."""
    parts = filename.upper().split(".")
    base = parts[0].replace(" ", "").replace(".", "")[:8]
    ext = parts[1][:3] if len(parts) > 1 else ""

    candidate = f"{base:<8}{ext:<3}".encode("ascii", "replace")[:11]
    if candidate not in existing_names:
        existing_names.add(candidate)
        return candidate

    counter = 1
    while True:
        suffix = f"~{counter}"
        avail = 8 - len(suffix)
        sub_base = base[:avail] + suffix
        candidate = f"{sub_base:<8}{ext:<3}".encode("ascii", "replace")[:11]
        if candidate not in existing_names:
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
    chars = [ord(c) for c in long_name] + [0]
    remainder = len(chars) % 13
    if remainder != 0:
        chars.extend([0xFFFF] * (13 - remainder))

    num_entries = len(chars) // 13
    entries = bytearray()

    for i in range(num_entries):
        seq = (num_entries - i)
        if i == 0:
            seq |= 0x40

        chunk = chars[(num_entries - 1 - i) * 13 : (num_entries - i) * 13]
        part1 = struct.pack("<5H", *chunk[0:5])
        part2 = struct.pack("<6H", *chunk[5:11])
        part3 = struct.pack("<2H", *chunk[11:13])

        entry = bytearray(32)
        entry[0] = seq
        entry[1:11] = part1
        entry[11] = 0x0F  # VFAT attribute
        entry[12] = 0x00
        entry[13] = chk
        entry[14:26] = part2
        entry[26:28] = b"\x00\x00"
        entry[28:32] = part3
        entries.extend(entry)

    return bytes(entries)


class FAT32Builder:
    """Constructs a compliant FAT32 filesystem buffer for MVX_BOOT."""

    def __init__(self, size_bytes: int, volume_label: str = "MVX_BOOT", hidden_sectors: int = 2048) -> None:
        self.size_bytes = size_bytes
        self.volume_label = f"{volume_label:<11}"[:11].encode("ascii")
        self.hidden_sectors = hidden_sectors

        self.bytes_per_sector = 512
        self.total_sectors = size_bytes // self.bytes_per_sector
        self.sectors_per_cluster = 1  # 512 bytes per cluster ensures >= 65525 clusters for FAT32
        self.cluster_size = self.bytes_per_sector * self.sectors_per_cluster

        self.reserved_sectors = 32
        self.num_fats = 2

        # Estimate clusters
        est_clusters = (self.total_sectors - self.reserved_sectors) // self.sectors_per_cluster
        # Each FAT entry is 4 bytes
        self.sectors_per_fat = max(32, math.ceil((est_clusters * 4) / self.bytes_per_sector))

        self.fat_offset = self.reserved_sectors * self.bytes_per_sector
        self.cluster_heap_offset = (self.reserved_sectors + self.num_fats * self.sectors_per_fat) * self.bytes_per_sector

        self.total_clusters = (size_bytes - self.cluster_heap_offset) // self.cluster_size
        if self.total_clusters < 65525:
            raise ValueError(f"FAT32 requires at least 65525 clusters, got {self.total_clusters}")

        self.fs_buffer = bytearray(size_bytes)

        # FAT table: cluster 0 and 1 are reserved
        self.fat: List[int] = [0] * (self.total_clusters + 2)
        self.fat[0] = 0x0FFFFFF8  # Media descriptor
        self.fat[1] = 0x0FFFFFFF  # EOC marker
        self.fat[2] = 0x0FFFFFFF  # Cluster 2 is Root Directory

        self.allocated_clusters = 3  # Root dir is cluster 2, next free is 3

        self._write_vbr()
        self._write_fsinfo()

    def _write_vbr(self) -> None:
        """Write FAT32 Volume Boot Record (VBR) at sector 0 and backup at sector 6."""
        vbr = bytearray(512)
        vbr[0:3] = b"\xeb\x58\x90"
        vbr[3:11] = b"MSWIN4.1"
        struct.pack_into("<H", vbr, 11, self.bytes_per_sector)
        vbr[13] = self.sectors_per_cluster
        struct.pack_into("<H", vbr, 14, self.reserved_sectors)
        vbr[16] = self.num_fats
        struct.pack_into("<H", vbr, 17, 0)  # root_dir_entries must be 0 in FAT32
        struct.pack_into("<H", vbr, 19, 0)  # total_sectors_16 must be 0
        vbr[21] = 0xF8  # Media descriptor
        struct.pack_into("<H", vbr, 22, 0)  # sectors_per_fat_16 must be 0
        struct.pack_into("<H", vbr, 24, 32)
        struct.pack_into("<H", vbr, 26, 64)
        struct.pack_into("<I", vbr, 28, self.hidden_sectors)
        struct.pack_into("<I", vbr, 32, self.total_sectors)

        # FAT32 specific
        struct.pack_into("<I", vbr, 36, self.sectors_per_fat)
        struct.pack_into("<H", vbr, 40, 0)  # Extended flags
        struct.pack_into("<H", vbr, 42, 0)  # FS Version
        struct.pack_into("<I", vbr, 44, 2)  # Root cluster = 2
        struct.pack_into("<H", vbr, 48, 1)  # FSInfo sector = 1
        struct.pack_into("<H", vbr, 50, 6)  # Backup boot sector = 6

        vbr[64] = 0x80  # Drive number
        vbr[66] = 0x29  # Extended boot signature
        struct.pack_into("<I", vbr, 67, 0x19842026)  # Volume ID
        vbr[71:82] = self.volume_label
        vbr[82:90] = b"FAT32   "
        vbr[510:512] = b"\x55\xaa"

        self.fs_buffer[0:512] = vbr
        self.fs_buffer[6 * 512 : 7 * 512] = vbr  # Backup VBR at sector 6

    def _write_fsinfo(self) -> None:
        """Write FAT32 FSInfo sector at sector 1 and backup at sector 7."""
        fsinfo = bytearray(512)
        fsinfo[0:4] = b"RRaA"  # 0x41615252
        fsinfo[484:488] = b"rrAa"  # 0x61417272
        free_clusters = self.total_clusters - (self.allocated_clusters - 2)
        struct.pack_into("<I", fsinfo, 488, free_clusters)
        struct.pack_into("<I", fsinfo, 492, self.allocated_clusters)
        fsinfo[508:512] = b"\x00\x00\x55\xaa"

        self.fs_buffer[1 * 512 : 2 * 512] = fsinfo
        self.fs_buffer[7 * 512 : 8 * 512] = fsinfo  # Backup FSInfo at sector 7

    def allocate_file(self, data: bytes) -> int:
        """Allocate clusters for file data."""
        if not data:
            return 0

        clusters_needed = math.ceil(len(data) / self.cluster_size)
        start_cluster = self.allocated_clusters

        for i in range(clusters_needed):
            curr_cluster = start_cluster + i
            if curr_cluster >= len(self.fat) - 1:
                raise RuntimeError("MVX_BOOT disk full: ran out of clusters")

            if i == clusters_needed - 1:
                self.fat[curr_cluster] = 0x0FFFFFFF
            else:
                self.fat[curr_cluster] = curr_cluster + 1

            chunk = data[i * self.cluster_size : (i + 1) * self.cluster_size]
            offset = self.cluster_heap_offset + (curr_cluster - 2) * self.cluster_size
            self.fs_buffer[offset : offset + len(chunk)] = chunk

        self.allocated_clusters += clusters_needed
        return start_cluster

    def allocate_clusters(self, count: int) -> int:
        """Allocate a contiguous chain of clusters in the FAT table."""
        if count <= 0:
            return 0
        start = self.allocated_clusters
        for i in range(count):
            curr = start + i
            if curr >= len(self.fat) - 1:
                raise RuntimeError("MVX_BOOT disk full: ran out of clusters")
            if i == count - 1:
                self.fat[curr] = 0x0FFFFFFF
            else:
                self.fat[curr] = curr + 1
        self.allocated_clusters += count
        return start

    def write_chained_clusters(self, start_cluster: int, data: bytes) -> None:
        """Write buffer sequentially across chained clusters starting from start_cluster."""
        clusters_count = math.ceil(len(data) / self.cluster_size)
        for i in range(clusters_count):
            curr_cluster = start_cluster + i
            chunk = data[i * self.cluster_size : (i + 1) * self.cluster_size]
            offset = self.cluster_heap_offset + (curr_cluster - 2) * self.cluster_size
            self.fs_buffer[offset : offset + len(chunk)] = chunk

    def build_from_directory(self, source_dir: Path) -> bytes:
        """Populate FAT32 filesystem from host directory."""
        source_dir = Path(source_dir).resolve()
        root_entries = bytearray()
        existing_root_names: Set[bytes] = set()

        # Volume label entry
        vol_entry = bytearray(32)
        vol_entry[0:11] = self.volume_label
        vol_entry[11] = 0x08  # Volume ID
        root_entries.extend(vol_entry)

        def process_directory(dir_path: Path, is_root: bool, parent_cluster: int = 0) -> int:
            nonlocal root_entries
            dir_entries = bytearray()
            existing_names: Set[bytes] = set()

            items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

            dir_cluster = 0
            if not is_root:
                # Calculate exact clusters needed for directory entries to avoid truncating large directories or overrunning into file clusters
                exact_bytes = 64  # . (32) and .. (32)
                for it in items:
                    vfat_count = math.ceil((len(it.name) + 1) / 13)
                    exact_bytes += (vfat_count + 1) * 32
                dir_clusters_needed = max(1, math.ceil(exact_bytes / self.cluster_size))
                dir_cluster = self.allocate_clusters(dir_clusters_needed)

                dot = bytearray(32)
                dot[0:11] = b".          "
                dot[11] = 0x10
                struct.pack_into("<H", dot, 20, (dir_cluster >> 16) & 0xFFFF)
                struct.pack_into("<H", dot, 26, dir_cluster & 0xFFFF)
                dir_entries.extend(dot)

                dotdot = bytearray(32)
                dotdot[0:11] = b"..         "
                dotdot[11] = 0x10
                struct.pack_into("<H", dotdot, 20, (parent_cluster >> 16) & 0xFFFF)
                struct.pack_into("<H", dotdot, 26, parent_cluster & 0xFFFF)
                dir_entries.extend(dotdot)
            else:
                # Root directory starts at cluster 2. Check if multiple clusters are needed.
                exact_bytes = 32  # volume label
                for it in items:
                    vfat_count = math.ceil((len(it.name) + 1) / 13)
                    exact_bytes += (vfat_count + 1) * 32
                root_clusters_needed = max(1, math.ceil(exact_bytes / self.cluster_size))
                if root_clusters_needed > 1:
                    extra = root_clusters_needed - 1
                    for i in range(extra):
                        curr = 2 + i
                        self.fat[curr] = curr + 1
                    self.fat[2 + extra] = 0x0FFFFFFF
                    self.allocated_clusters = 3 + extra

            for item in items:
                short_name = generate_83_name(item.name, existing_root_names if is_root else existing_names)
                vfat = make_vfat_entries(item.name, short_name)

                entry = bytearray(32)
                entry[0:11] = short_name

                if item.is_dir():
                    entry[11] = 0x10
                    sub_cluster = process_directory(item, is_root=False, parent_cluster=dir_cluster)
                    struct.pack_into("<H", entry, 20, (sub_cluster >> 16) & 0xFFFF)
                    struct.pack_into("<H", entry, 26, sub_cluster & 0xFFFF)
                else:
                    entry[11] = 0x20
                    file_bytes = item.read_bytes()
                    file_cluster = self.allocate_file(file_bytes)
                    struct.pack_into("<H", entry, 20, (file_cluster >> 16) & 0xFFFF)
                    struct.pack_into("<H", entry, 26, file_cluster & 0xFFFF)
                    struct.pack_into("<I", entry, 28, len(file_bytes))

                if is_root:
                    root_entries.extend(vfat)
                    root_entries.extend(entry)
                else:
                    dir_entries.extend(vfat)
                    dir_entries.extend(entry)

            if not is_root:
                self.write_chained_clusters(dir_cluster, dir_entries)
                return dir_cluster

            return 2

        process_directory(source_dir, is_root=True)

        # Write root directory (cluster 2)
        self.write_chained_clusters(2, root_entries)

        # Pack FAT table into buffer
        raw_fat = bytearray()
        for val in self.fat:
            raw_fat.extend(struct.pack("<I", val & 0x0FFFFFFF))
        fat_len = self.sectors_per_fat * self.bytes_per_sector
        raw_fat.extend(b"\x00" * (fat_len - len(raw_fat)))

        for f_idx in range(self.num_fats):
            f_offset = self.fat_offset + f_idx * fat_len
            self.fs_buffer[f_offset : f_offset + fat_len] = raw_fat

        # Update FSInfo with final free cluster count
        self._write_fsinfo()
        return bytes(self.fs_buffer)


# ==============================================================================
# 2. exFAT Filesystem Builder (MVX_DATA)
# ==============================================================================

def exfat_boot_checksum(data: bytearray) -> int:
    """Calculate standard exFAT 32-bit boot region checksum over sectors 0 to 10."""
    chk = 0
    for i, b in enumerate(data):
        if i in (106, 107, 112):  # Skip VolumeFlags and PercentInUse
            continue
        chk = (((chk << 31) & 0xFFFFFFFF) | (chk >> 1)) + b
        chk &= 0xFFFFFFFF
    return chk


def exfat_set_checksum(entries: bytearray) -> int:
    """Calculate 16-bit directory set checksum."""
    chk = 0
    for i, b in enumerate(entries):
        if i in (2, 3):  # Skip SetChecksum field
            continue
        chk = (((chk << 15) & 0xFFFF) | (chk >> 1)) + b
        chk &= 0xFFFF
    return chk


def exfat_name_hash(name: str) -> int:
    """Calculate 16-bit exFAT up-cased filename hash."""
    h = 0
    for c in name.upper():
        ch = ord(c)
        h = (((h << 15) & 0xFFFF) | (h >> 1)) + (ch & 0xFF)
        h = (((h << 15) & 0xFFFF) | (h >> 1)) + ((ch >> 8) & 0xFF)
        h &= 0xFFFF
    return h


class ExFATBuilder:
    """Constructs a compliant exFAT filesystem buffer for MVX_DATA."""

    def __init__(self, size_bytes: int, volume_label: str = "MVX_DATA", partition_offset_lba: int = 133120) -> None:
        self.size_bytes = size_bytes
        self.volume_label = volume_label[:11]
        self.partition_offset_lba = partition_offset_lba

        self.bytes_per_sector = 512
        self.sector_shift = 9
        self.sectors_per_cluster = 8  # 4096-byte clusters
        self.cluster_shift = 3
        self.cluster_size = self.bytes_per_sector * self.sectors_per_cluster

        self.total_sectors = size_bytes // self.bytes_per_sector

        # Layout:
        # Sectors 0-11: Main Boot Region
        # Sectors 12-23: Backup Boot Region
        # Sectors 24+: FAT table
        self.fat_offset = 24
        est_clusters = (self.total_sectors - 512) // self.sectors_per_cluster
        self.fat_length = math.ceil(((est_clusters + 2) * 4) / self.bytes_per_sector)

        # Cluster heap starts aligned after FAT table (at least sector 512)
        min_heap_offset = self.fat_offset + self.fat_length
        self.cluster_heap_offset = max(512, ((min_heap_offset + 63) // 64) * 64)
        self.cluster_count = (self.total_sectors - self.cluster_heap_offset) // self.sectors_per_cluster

        self.fs_buffer = bytearray(size_bytes)

        # Clusters:
        # Cluster 2..N: Allocation Bitmap
        # Next 32 clusters: Up-Case Table (131072 bytes = 32 clusters)
        # Following cluster: Root Directory
        bitmap_bytes_len = math.ceil(self.cluster_count / 8)
        self.bitmap_clusters_count = max(1, math.ceil(bitmap_bytes_len / self.cluster_size))
        self.bitmap_cluster = 2
        self.upcase_cluster = self.bitmap_cluster + self.bitmap_clusters_count
        self.upcase_clusters_count = 32  # 128 KB
        self.root_dir_cluster = self.upcase_cluster + self.upcase_clusters_count

        self.allocated_clusters = self.root_dir_cluster + 1  # Next free cluster for user files

        # FAT table initialized with 1 FAT
        self.fat: List[int] = [0] * (self.cluster_count + 2)
        self.fat[0] = 0xFFFFFFF8  # Media
        self.fat[1] = 0xFFFFFFFF  # End
        for c in range(self.bitmap_cluster, self.bitmap_cluster + self.bitmap_clusters_count - 1):
            self.fat[c] = c + 1
        self.fat[self.bitmap_cluster + self.bitmap_clusters_count - 1] = 0xFFFFFFFF
        for c in range(self.upcase_cluster, self.upcase_cluster + self.upcase_clusters_count - 1):
            self.fat[c] = c + 1
        self.fat[self.upcase_cluster + self.upcase_clusters_count - 1] = 0xFFFFFFFF
        self.fat[self.root_dir_cluster] = 0xFFFFFFFF

        self._build_upcase_table()
        self._write_boot_regions()

    def _build_upcase_table(self) -> None:
        """Construct standard 128KB uncompressed Up-Case table."""
        self.upcase_data = bytearray(65536 * 2)
        for i in range(65536):
            uc = chr(i).upper()
            u = ord(uc) if len(uc) == 1 else i
            self.upcase_data[i * 2 : (i + 1) * 2] = u.to_bytes(2, "little")

        chk = 0
        for b in self.upcase_data:
            chk = (((chk << 31) & 0xFFFFFFFF) | (chk >> 1)) + b
            chk &= 0xFFFFFFFF
        self.upcase_checksum = chk

    def _write_boot_regions(self) -> None:
        """Write Main Boot Region (sectors 0-11) and Backup Boot Region (sectors 12-23)."""
        boot_region = bytearray(512 * 12)

        # Sector 0: Volume Boot Record
        vbr = bytearray(512)
        vbr[0:3] = b"\xeb\x76\x90"
        vbr[3:11] = b"EXFAT   "
        struct.pack_into("<Q", vbr, 64, self.partition_offset_lba)
        struct.pack_into("<Q", vbr, 72, self.total_sectors)
        struct.pack_into("<I", vbr, 80, self.fat_offset)
        struct.pack_into("<I", vbr, 84, self.fat_length)
        struct.pack_into("<I", vbr, 88, self.cluster_heap_offset)
        struct.pack_into("<I", vbr, 92, self.cluster_count)
        struct.pack_into("<I", vbr, 96, self.root_dir_cluster)
        struct.pack_into("<I", vbr, 100, 0x19842026)  # Serial
        struct.pack_into("<H", vbr, 104, 0x0100)      # Revision 1.00
        struct.pack_into("<H", vbr, 106, 0x0000)      # VolumeFlags
        vbr[108] = self.sector_shift
        vbr[109] = self.cluster_shift
        vbr[110] = 1     # NumberOfFats
        vbr[111] = 0x80  # DriveSelect
        vbr[112] = 0     # PercentInUse
        vbr[510:512] = b"\x55\xaa"

        boot_region[0:512] = vbr

        # Sectors 1-10: Extended boot sectors
        for s in range(1, 11):
            boot_region[s * 512 + 510 : (s + 1) * 512] = b"\x55\xaa"

        # Sector 11: Boot Checksum
        boot_chk = exfat_boot_checksum(boot_region[: 512 * 11])
        chk_bytes = struct.pack("<I", boot_chk) * 128
        boot_region[11 * 512 : 12 * 512] = chk_bytes

        # Main Boot Region at sector 0
        self.fs_buffer[0 : 512 * 12] = boot_region
        # Backup Boot Region at sector 12
        self.fs_buffer[512 * 12 : 512 * 24] = boot_region

    def allocate_file(self, data: bytes) -> Tuple[int, int]:
        """Allocate clusters for file data."""
        if not data:
            return 0, 0

        clusters_needed = math.ceil(len(data) / self.cluster_size)
        start_cluster = self.allocated_clusters

        for i in range(clusters_needed):
            curr_cluster = start_cluster + i
            chunk = data[i * self.cluster_size : (i + 1) * self.cluster_size]
            offset = (self.cluster_heap_offset + (curr_cluster - 2) * self.sectors_per_cluster) * self.bytes_per_sector
            self.fs_buffer[offset : offset + len(chunk)] = chunk

        self.allocated_clusters += clusters_needed
        return start_cluster, clusters_needed

    def make_entry_set(self, name: str, is_dir: bool, first_cluster: int, size: int) -> bytes:
        """Create a compliant exFAT directory set (File Entry, Stream Extension, and 1+ File Name entries)."""
        num_name_entries = max(1, math.ceil(len(name) / 15))
        secondary_count = 1 + num_name_entries
        total_entries = 1 + secondary_count  # 1 File Entry + 1 Stream Extension + N File Name entries

        entries = bytearray(32 * total_entries)

        # 1. File Entry (0x85)
        entries[0] = 0x85
        entries[1] = secondary_count  # SecondaryCount
        entries[4] = 0x10 if is_dir else 0x20  # FileAttributes
        ts = (46 << 25) | (1 << 21) | (1 << 16)  # 2026-01-01 00:00:00
        struct.pack_into("<I", entries, 8, ts)
        struct.pack_into("<I", entries, 12, ts)
        struct.pack_into("<I", entries, 16, ts)

        # 2. Stream Extension Entry (0xC0)
        stream_offset = 32
        entries[stream_offset] = 0xC0
        # 0x03 = AllocationPossible (bit 0) | NoFatChain (bit 1) for contiguous allocations
        entries[stream_offset + 1] = 0x03 if size > 0 else 0x01
        entries[stream_offset + 3] = len(name)
        struct.pack_into("<H", entries, stream_offset + 4, exfat_name_hash(name))
        struct.pack_into("<Q", entries, stream_offset + 8, size)
        struct.pack_into("<I", entries, stream_offset + 20, first_cluster if size > 0 else 0)
        struct.pack_into("<Q", entries, stream_offset + 24, size)

        # 3. File Name Entries (0xC1)
        for i in range(num_name_entries):
            fn_offset = 64 + (i * 32)
            entries[fn_offset] = 0xC1
            entries[fn_offset + 1] = 0x00
            chunk = name[i * 15 : (i + 1) * 15]
            chunk_utf16 = chunk.encode("utf-16le")
            entries[fn_offset + 2 : fn_offset + 2 + len(chunk_utf16)] = chunk_utf16

        # Checksum over all entries in the directory set
        c = exfat_set_checksum(entries)
        struct.pack_into("<H", entries, 2, c)
        return bytes(entries)

    def _write_directory_tree(self, dir_path: Path) -> Tuple[int, int]:
        """Recursively allocate and write a directory tree to exFAT buffer.

        Returns:
            Tuple of (first_cluster, total_directory_size_bytes)
        """
        dir_cluster = self.allocated_clusters
        self.allocated_clusters += 1

        dir_entries = bytearray()

        items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for item in items:
            if item.is_dir():
                sub_cluster, sub_size = self._write_directory_tree(item)
                dir_entries.extend(
                    self.make_entry_set(item.name, is_dir=True, first_cluster=sub_cluster, size=sub_size)
                )
            elif item.is_file():
                file_bytes = item.read_bytes()
                fc, _ = self.allocate_file(file_bytes)
                dir_entries.extend(
                    self.make_entry_set(item.name, is_dir=False, first_cluster=fc, size=len(file_bytes))
                )

        clusters_needed = max(1, math.ceil(len(dir_entries) / self.cluster_size))
        dir_offset = (
            self.cluster_heap_offset + (dir_cluster - 2) * self.sectors_per_cluster
        ) * self.bytes_per_sector
        self.fs_buffer[dir_offset : dir_offset + len(dir_entries)] = dir_entries

        return dir_cluster, self.cluster_size * clusters_needed

    def build_with_structure(
        self,
        default_config: Dict[str, Any],
        data_source_dir: Optional[Path] = None,
    ) -> bytes:
        """Build compliant exFAT filesystem containing ISO/, config/, metadata/, backups/, and any extra source folders."""
        # Write Up-case Table in clusters 3..34
        upcase_offset = (
            self.cluster_heap_offset + (self.upcase_cluster - 2) * self.sectors_per_cluster
        ) * self.bytes_per_sector
        self.fs_buffer[upcase_offset : upcase_offset + len(self.upcase_data)] = self.upcase_data

        root_entries = bytearray()

        # Volume Label Directory Entry (0x83)
        vol_entry = bytearray(32)
        vol_entry[0] = 0x83
        vol_entry[1] = len(self.volume_label)
        vol_label_utf16 = self.volume_label.encode("utf-16le")
        vol_entry[2 : 2 + len(vol_label_utf16)] = vol_label_utf16
        root_entries.extend(vol_entry)

        # Allocation Bitmap Directory Entry (0x81)
        bitmap_bytes_len = math.ceil(self.cluster_count / 8)
        bitmap_entry = bytearray(32)
        bitmap_entry[0] = 0x81
        struct.pack_into("<I", bitmap_entry, 20, self.bitmap_cluster)
        struct.pack_into("<Q", bitmap_entry, 24, bitmap_bytes_len)
        root_entries.extend(bitmap_entry)

        # Up-case Table Directory Entry (0x82)
        upcase_entry = bytearray(32)
        upcase_entry[0] = 0x82
        struct.pack_into("<I", upcase_entry, 4, self.upcase_checksum)
        struct.pack_into("<I", upcase_entry, 20, self.upcase_cluster)
        struct.pack_into("<Q", upcase_entry, 24, len(self.upcase_data))
        root_entries.extend(upcase_entry)

        # Process directories from data_source_dir if provided
        seen_root_names = set()
        if data_source_dir and data_source_dir.is_dir():
            for item in sorted(data_source_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                seen_root_names.add(item.name.lower())
                if item.is_dir():
                    cluster, size = self._write_directory_tree(item)
                    root_entries.extend(
                        self.make_entry_set(item.name, is_dir=True, first_cluster=cluster, size=size)
                    )
                elif item.is_file():
                    file_bytes = item.read_bytes()
                    fc, _ = self.allocate_file(file_bytes)
                    root_entries.extend(
                        self.make_entry_set(item.name, is_dir=False, first_cluster=fc, size=len(file_bytes))
                    )

        # Ensure baseline required MVentX folders exist even if empty
        for req_folder in ("ISO", "config", "metadata", "backups"):
            if req_folder.lower() not in seen_root_names:
                dir_cluster = self.allocated_clusters
                self.allocated_clusters += 1
                if req_folder == "config":
                    # Write default config
                    config_json_bytes = json.dumps(default_config, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
                    json_cluster, _ = self.allocate_file(config_json_bytes)
                    config_dir_entries = bytearray()
                    config_dir_entries.extend(
                        self.make_entry_set("mventx.json", is_dir=False, first_cluster=json_cluster, size=len(config_json_bytes))
                    )
                    cfg_offset = (
                        self.cluster_heap_offset + (dir_cluster - 2) * self.sectors_per_cluster
                    ) * self.bytes_per_sector
                    self.fs_buffer[cfg_offset : cfg_offset + len(config_dir_entries)] = config_dir_entries

                root_entries.extend(
                    self.make_entry_set(req_folder, is_dir=True, first_cluster=dir_cluster, size=self.cluster_size)
                )

        # Write Root Directory to cluster 35
        root_offset = (
            self.cluster_heap_offset + (self.root_dir_cluster - 2) * self.sectors_per_cluster
        ) * self.bytes_per_sector
        self.fs_buffer[root_offset : root_offset + len(root_entries)] = root_entries

        # Build and write Allocation Bitmap in Cluster 2
        bitmap = bytearray(bitmap_bytes_len)
        for clus in range(2, self.allocated_clusters):
            idx = (clus - 2) // 8
            bit = (clus - 2) % 8
            if idx < len(bitmap):
                bitmap[idx] |= (1 << bit)

        bitmap_offset = (
            self.cluster_heap_offset + (self.bitmap_cluster - 2) * self.sectors_per_cluster
        ) * self.bytes_per_sector
        self.fs_buffer[bitmap_offset : bitmap_offset + len(bitmap)] = bitmap

        # Write FAT table at fat_offset
        fat_byte_offset = self.fat_offset * self.bytes_per_sector
        for idx, val in enumerate(self.fat):
            if val != 0:
                struct.pack_into("<I", self.fs_buffer, fat_byte_offset + idx * 4, val)

        return bytes(self.fs_buffer)


# ==============================================================================
# 3. Dual-Partition GPT Disk Image Assembler
# ==============================================================================

def build_dual_partition_gpt_disk(
    esp_source_dir: Path,
    output_path: Path,
    disk_size_mb: int = 256,
    esp_size_mb: int = 64,
    default_config: Optional[Dict[str, Any]] = None,
    data_source_dir: Optional[Path] = None,
) -> None:
    """Build a complete GPT-partitioned raw disk image with MVX_BOOT and MVX_DATA."""
    total_sectors = (disk_size_mb * 1024 * 1024) // 512

    # Partition 1: MVX_BOOT (FAT32 ESP)
    esp_start_lba = 2048  # 1 MiB alignment
    esp_sectors = (esp_size_mb * 1024 * 1024) // 512
    esp_end_lba = esp_start_lba + esp_sectors - 1
    esp_size_bytes = esp_sectors * 512

    # Partition 2: MVX_DATA (exFAT Basic Data)
    # Starts aligned immediately following Partition 1
    data_start_lba = esp_end_lba + 1
    data_end_lba = total_sectors - 34  # Reserve 33 sectors for backup GPT array + 1 for backup header
    data_sectors = data_end_lba - data_start_lba + 1
    data_size_bytes = data_sectors * 512

    if data_sectors < 65536:
        raise ValueError(f"Disk size too small: {disk_size_mb} MB leaves only {data_sectors} sectors for data")

    # 1. Build FAT32 Filesystem for MVX_BOOT
    fat_builder = FAT32Builder(size_bytes=esp_size_bytes, volume_label="MVX_BOOT", hidden_sectors=esp_start_lba)
    fat_image = fat_builder.build_from_directory(esp_source_dir)

    # 2. Build exFAT Filesystem for MVX_DATA
    if default_config is None:
        if data_source_dir and (data_source_dir / "config" / "mventx.json").is_file():
            with open(data_source_dir / "config" / "mventx.json", "r", encoding="utf-8") as f:
                default_config = json.load(f)
        else:
            defaults_path = Path(__file__).resolve().parent.parent / "core" / "config" / "defaults.json"
            if defaults_path.exists():
                with open(defaults_path, "r", encoding="utf-8") as f:
                    default_config = json.load(f)
            else:
                default_config = {
                    "version": "1.0.0",
                    "meta": {"title": "MVentX Multiboot System", "theme": "mventx", "timeout": 10},
                    "environments": [],
                }

    exfat_builder = ExFATBuilder(size_bytes=data_size_bytes, volume_label="MVX_DATA", partition_offset_lba=data_start_lba)
    exfat_image = exfat_builder.build_with_structure(default_config, data_source_dir=data_source_dir)

    # 3. Assemble Full Disk Buffer
    disk = bytearray(total_sectors * 512)

    # Sector 0: Protective MBR
    pmbr = bytearray(512)
    struct.pack_into(
        "<B3sB3sII",
        pmbr,
        446,
        0x00,               # Bootable
        b"\x00\x02\x00",    # Start CHS
        0xEE,               # Type: GPT Protective
        b"\xff\xff\xff",    # End CHS
        1,                  # Start LBA
        total_sectors - 1,  # Size in LBA
    )
    pmbr[510:512] = b"\x55\xaa"
    disk[0:512] = pmbr

    # Sectors 2-33: Primary Partition Array (128 entries * 128 bytes)
    partition_array = bytearray(128 * 128)

    # Partition 1 Entry (ESP)
    esp_type_guid = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B").bytes_le
    esp_part_guid = uuid.uuid4().bytes_le
    part1_entry = bytearray(128)
    part1_entry[0:16] = esp_type_guid
    part1_entry[16:32] = esp_part_guid
    struct.pack_into("<Q", part1_entry, 32, esp_start_lba)
    struct.pack_into("<Q", part1_entry, 40, esp_end_lba)
    struct.pack_into("<Q", part1_entry, 48, 0)  # Attributes
    part1_name = "MVX_BOOT".encode("utf-16le")
    part1_entry[56 : 56 + len(part1_name)] = part1_name
    partition_array[0:128] = part1_entry

    # Partition 2 Entry (Microsoft Basic Data)
    data_type_guid = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7").bytes_le
    data_part_guid = uuid.uuid4().bytes_le
    part2_entry = bytearray(128)
    part2_entry[0:16] = data_type_guid
    part2_entry[16:32] = data_part_guid
    struct.pack_into("<Q", part2_entry, 32, data_start_lba)
    struct.pack_into("<Q", part2_entry, 40, data_end_lba)
    struct.pack_into("<Q", part2_entry, 48, 0)  # Attributes
    part2_name = "MVX_DATA".encode("utf-16le")
    part2_entry[56 : 56 + len(part2_name)] = part2_name
    partition_array[128:256] = part2_entry

    part_crc = zlib.crc32(partition_array)

    # Sector 1: Primary GPT Header
    disk_guid = uuid.uuid4().bytes_le
    gpt_header = bytearray(92)
    gpt_header[0:8] = b"EFI PART"
    struct.pack_into("<I", gpt_header, 8, 0x00010000)
    struct.pack_into("<I", gpt_header, 12, 92)
    struct.pack_into("<I", gpt_header, 16, 0)  # CRC placeholder
    struct.pack_into("<I", gpt_header, 20, 0)
    struct.pack_into("<Q", gpt_header, 24, 1)                  # Current LBA
    struct.pack_into("<Q", gpt_header, 32, total_sectors - 1)  # Backup LBA
    struct.pack_into("<Q", gpt_header, 40, esp_start_lba)      # First usable LBA
    struct.pack_into("<Q", gpt_header, 48, data_end_lba)       # Last usable LBA
    gpt_header[56:72] = disk_guid
    struct.pack_into("<Q", gpt_header, 72, 2)                  # Partition array LBA
    struct.pack_into("<I", gpt_header, 80, 128)
    struct.pack_into("<I", gpt_header, 84, 128)
    struct.pack_into("<I", gpt_header, 88, part_crc)
    hdr_crc = zlib.crc32(gpt_header)
    struct.pack_into("<I", gpt_header, 16, hdr_crc)

    disk[512 : 512 + 92] = gpt_header
    disk[1024 : 1024 + len(partition_array)] = partition_array

    # Embed Partition 1 (FAT32 MVX_BOOT)
    p1_offset = esp_start_lba * 512
    disk[p1_offset : p1_offset + len(fat_image)] = fat_image

    # Embed Partition 2 (exFAT MVX_DATA)
    p2_offset = data_start_lba * 512
    disk[p2_offset : p2_offset + len(exfat_image)] = exfat_image

    # Backup Partition Array and Backup GPT Header at disk end
    backup_part_lba = total_sectors - 33
    backup_header_lba = total_sectors - 1

    disk[backup_part_lba * 512 : (backup_part_lba + 32) * 512] = partition_array

    backup_header = bytearray(gpt_header)
    struct.pack_into("<Q", backup_header, 24, backup_header_lba)
    struct.pack_into("<Q", backup_header, 32, 1)
    struct.pack_into("<Q", backup_header, 72, backup_part_lba)
    struct.pack_into("<I", backup_header, 16, 0)
    backup_crc = zlib.crc32(backup_header)
    struct.pack_into("<I", backup_header, 16, backup_crc)

    disk[backup_header_lba * 512 : backup_header_lba * 512 + 92] = backup_header

    # 4. Atomic Write to Output Image
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.tmp")

    with open(temp_path, "wb") as f:
        f.write(disk)
        f.flush()
        os.fsync(f.fileno())

    temp_path.replace(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build dual-partition GPT virtual disk (MVX_BOOT FAT32 + MVX_DATA exFAT)")
    parser.add_argument("--esp-source", type=Path, required=True, help="Path to staged ESP directory (e.g. build/esp)")
    parser.add_argument("--output", type=Path, required=True, help="Path to output disk image (e.g. build/mventx_test.img)")
    parser.add_argument("--size-mb", type=int, default=256, help="Total disk image size in MB (default: 256)")
    parser.add_argument("--esp-size-mb", type=int, default=64, help="ESP partition size in MB (default: 64)")
    parser.add_argument("--data-source", type=Path, default=None, help="Optional staged MVX_DATA directory (e.g. build/data)")
    parser.add_argument("--config", type=Path, default=None, help="Optional custom mventx.json for MVX_DATA/config/")

    args = parser.parse_args()

    custom_cfg = None
    if args.config and args.config.exists():
        with open(args.config, "r", encoding="utf-8") as f:
            custom_cfg = json.load(f)

    try:
        build_dual_partition_gpt_disk(
            esp_source_dir=args.esp_source,
            output_path=args.output,
            disk_size_mb=args.size_mb,
            esp_size_mb=args.esp_size_mb,
            default_config=custom_cfg,
            data_source_dir=args.data_source,
        )
        print(f"Successfully created dual-partition GPT disk image: {args.output}")
        return 0
    except Exception as exc:
        print(f"Error creating dual-partition disk image: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

