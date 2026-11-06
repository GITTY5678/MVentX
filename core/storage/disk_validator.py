"""Disk Validator for MVentX Dual-Partition Storage Architecture.

Provides deep structural and semantic inspection of raw GPT virtual disk images:
- Protective MBR (Sector 0, 0x55AA signature, 0xEE partition spanning full disk).
- Primary GPT Header (Sector 1, CRC32, LBA bounds, partition array CRC32).
- Backup GPT Header (Last sector, CRC32, LBA bounds, matching GUID & partition array).
- Backup GPT Partition Array (Preceding backup header, CRC32 match).
- Partition Alignment & Boundaries (1 MiB / 2048-sector alignment, no overlap, within usable LBAs).
- Partition 1 (MVX_BOOT): EFI System Partition GUID, FAT32 VBR, FSInfo, backup VBR, files.
- Partition 2 (MVX_DATA): Microsoft Basic Data Partition GUID, exFAT VBR, Sector 11 checksum,
  backup boot region, volume label MVX_DATA, required subdirectories (ISO/, config/, metadata/, backups/),
  and direct sector-level file reading.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import struct
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid
import zlib
from dataclasses import dataclass

ESP_TYPE_GUID = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")
BASIC_DATA_GUID = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")


class DiskValidationError(Exception):
    """Raised when virtual disk layout fails structural or semantic validation."""


@dataclass
class PartitionInfo:
    """Metadata of a partition decoded from GPT entry."""
    index: int
    type_guid: uuid.UUID
    unique_guid: uuid.UUID
    start_lba: int
    end_lba: int
    attributes: int
    name: str

    @property
    def sector_count(self) -> int:
        return self.end_lba - self.start_lba + 1

    @property
    def size_bytes(self) -> int:
        return self.sector_count * 512

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / (1024 * 1024), 2)


class DiskValidator:
    """Validates MVentX dual-partition GPT virtual disk images."""

    def __init__(self, disk_image_path: Path | str) -> None:
        self.disk_path = Path(disk_image_path).resolve()
        if not self.disk_path.exists():
            raise FileNotFoundError(f"Disk image not found: {self.disk_path}")

        self.disk_size = self.disk_path.stat().st_size
        self.total_sectors = self.disk_size // 512

        if self.disk_size < 34 * 512:
            raise DiskValidationError(f"Disk image too small ({self.disk_size} bytes); must be at least 34 sectors")

        if self.disk_size % 512 != 0:
            raise DiskValidationError(f"Disk size ({self.disk_size}) is not a multiple of 512 bytes")

    # --------------------------------------------------------------------------
    # Master Validation Entry Point
    # --------------------------------------------------------------------------

    def validate_all(self) -> Dict[str, Any]:
        """Perform comprehensive validation of GPT, FAT32 MVX_BOOT, and exFAT MVX_DATA."""
        report: Dict[str, Any] = {
            "disk_path": str(self.disk_path),
            "disk_size_mb": round(self.disk_size / (1024 * 1024), 2),
            "total_sectors": self.total_sectors,
        }

        with open(self.disk_path, "rb") as f:
            # 1. Protective MBR
            self.validate_pmbr(f)
            report["pmbr_valid"] = True

            # 2. Primary & Backup GPT Headers & Partition Array
            partitions = self.validate_gpt(f)
            report["gpt_valid"] = True
            report["partitions"] = [
                {
                    "index": p.index,
                    "name": p.name,
                    "type_guid": str(p.type_guid),
                    "start_lba": p.start_lba,
                    "end_lba": p.end_lba,
                    "size_mb": p.size_mb,
                }
                for p in partitions
            ]

            # 3. Partition 1: MVX_BOOT (FAT32)
            p1 = partitions[0]
            report["mvx_boot"] = self.validate_fat32_mvx_boot(f, p1)

            # 4. Partition 2: MVX_DATA (exFAT)
            p2 = partitions[1]
            report["mvx_data"] = self.validate_exfat_mvx_data(f, p2)

        return report

    # --------------------------------------------------------------------------
    # 1. Protective MBR Validation
    # --------------------------------------------------------------------------

    def validate_pmbr(self, f) -> None:
        """Validate Protective MBR at Sector 0."""
        f.seek(0)
        sector0 = f.read(512)
        if len(sector0) < 512:
            raise DiskValidationError("Unable to read Sector 0 (truncated image)")

        if sector0[510:512] != b"\x55\xaa":
            raise DiskValidationError(f"Sector 0 missing standard MBR boot signature 0x55AA, got {sector0[510:512]!r}")

        # Partition 1 entry at offset 446 (16 bytes)
        part1 = sector0[446:462]
        part_type = part1[4]
        if part_type != 0xEE:
            raise DiskValidationError(f"MBR Partition 1 type is {hex(part_type)}; must be 0xEE (GPT Protective)")

        start_lba = struct.unpack_from("<I", part1, 8)[0]
        if start_lba != 1:
            raise DiskValidationError(f"MBR Protective partition start LBA must be 1, got {start_lba}")

    # --------------------------------------------------------------------------
    # 2. GPT Primary & Backup Headers & Partition Array
    # --------------------------------------------------------------------------

    def validate_gpt(self, f) -> List[PartitionInfo]:
        """Validate Primary and Backup GPT Headers and partition entries."""
        # --- Primary GPT Header (Sector 1) ---
        f.seek(512)
        hdr = bytearray(f.read(512))
        if len(hdr) < 512 or hdr[0:8] != b"EFI PART":
            raise DiskValidationError(f"Invalid primary GPT signature: {hdr[0:8]!r}")

        hdr_revision = struct.unpack_from("<I", hdr, 8)[0]
        if hdr_revision != 0x00010000:
            raise DiskValidationError(f"Unexpected GPT revision: {hex(hdr_revision)} (expected 1.0 / 0x00010000)")

        hdr_size = struct.unpack_from("<I", hdr, 12)[0]
        if hdr_size < 92 or hdr_size > 512:
            raise DiskValidationError(f"Invalid primary GPT header size: {hdr_size}")

        recorded_crc = struct.unpack_from("<I", hdr, 16)[0]
        struct.pack_into("<I", hdr, 16, 0)
        calc_crc = zlib.crc32(hdr[:hdr_size])
        if calc_crc != recorded_crc:
            raise DiskValidationError(f"Primary GPT header CRC32 mismatch: calculated {hex(calc_crc)} != recorded {hex(recorded_crc)}")

        current_lba = struct.unpack_from("<Q", hdr, 24)[0]
        if current_lba != 1:
            raise DiskValidationError(f"Primary GPT current LBA must be 1, got {current_lba}")

        backup_lba = struct.unpack_from("<Q", hdr, 32)[0]
        if backup_lba != self.total_sectors - 1:
            raise DiskValidationError(f"Primary GPT backup LBA ({backup_lba}) does not point to last sector ({self.total_sectors - 1})")

        first_usable = struct.unpack_from("<Q", hdr, 40)[0]
        last_usable = struct.unpack_from("<Q", hdr, 48)[0]
        if first_usable < 34:
            raise DiskValidationError(f"First usable LBA must be >= 34, got {first_usable}")
        if last_usable > self.total_sectors - 34:
            raise DiskValidationError(f"Last usable LBA exceeds disk boundaries: {last_usable} > {self.total_sectors - 34}")

        disk_guid = uuid.UUID(bytes_le=bytes(hdr[56:72]))
        part_lba = struct.unpack_from("<Q", hdr, 72)[0]
        num_parts = struct.unpack_from("<I", hdr, 80)[0]
        part_entry_size = struct.unpack_from("<I", hdr, 84)[0]
        recorded_part_crc = struct.unpack_from("<I", hdr, 88)[0]

        if part_lba != 2:
            raise DiskValidationError(f"Primary GPT partition array must start at LBA 2, got {part_lba}")
        if part_entry_size < 128:
            raise DiskValidationError(f"GPT partition entry size must be >= 128 bytes, got {part_entry_size}")

        # Read Primary Partition Array
        f.seek(part_lba * 512)
        part_array = f.read(num_parts * part_entry_size)
        calc_part_crc = zlib.crc32(part_array)
        if calc_part_crc != recorded_part_crc:
            raise DiskValidationError("Primary GPT partition array CRC32 mismatch")

        # --- Backup GPT Header (Last sector: total_sectors - 1) ---
        f.seek((self.total_sectors - 1) * 512)
        bhdr = bytearray(f.read(512))
        if len(bhdr) < 512 or bhdr[0:8] != b"EFI PART":
            raise DiskValidationError("Backup GPT header signature is invalid")

        b_hdr_size = struct.unpack_from("<I", bhdr, 12)[0]
        b_recorded_crc = struct.unpack_from("<I", bhdr, 16)[0]
        struct.pack_into("<I", bhdr, 16, 0)
        b_calc_crc = zlib.crc32(bhdr[:b_hdr_size])
        if b_calc_crc != b_recorded_crc:
            raise DiskValidationError(f"Backup GPT header CRC32 mismatch: calculated {hex(b_calc_crc)} != recorded {hex(b_recorded_crc)}")

        b_current_lba = struct.unpack_from("<Q", bhdr, 24)[0]
        if b_current_lba != self.total_sectors - 1:
            raise DiskValidationError(f"Backup GPT current LBA must be {self.total_sectors - 1}, got {b_current_lba}")

        b_backup_lba = struct.unpack_from("<Q", bhdr, 32)[0]
        if b_backup_lba != 1:
            raise DiskValidationError(f"Backup GPT points to primary LBA {b_backup_lba}, expected 1")

        b_disk_guid = uuid.UUID(bytes_le=bytes(bhdr[56:72]))
        if b_disk_guid != disk_guid:
            raise DiskValidationError(f"Backup GPT Disk GUID mismatch: {b_disk_guid} != {disk_guid}")

        b_part_lba = struct.unpack_from("<Q", bhdr, 72)[0]
        b_recorded_part_crc = struct.unpack_from("<I", bhdr, 88)[0]
        if b_recorded_part_crc != recorded_part_crc:
            raise DiskValidationError("Backup GPT partition array CRC does not match primary partition array CRC")

        # Read Backup Partition Array (Sectors total_sectors - 33 to total_sectors - 2)
        f.seek(b_part_lba * 512)
        b_part_array = f.read(num_parts * part_entry_size)
        if b_part_array != part_array:
            raise DiskValidationError("Backup GPT partition array content does not match primary partition array")

        # --- Decode and Validate Partition Entries ---
        partitions: List[PartitionInfo] = []
        for i in range(num_parts):
            entry_bytes = part_array[i * part_entry_size : (i + 1) * part_entry_size]
            type_raw = entry_bytes[0:16]
            if type_raw == b"\x00" * 16:
                continue

            type_guid = uuid.UUID(bytes_le=type_raw)
            unique_guid = uuid.UUID(bytes_le=entry_bytes[16:32])
            start_lba = struct.unpack_from("<Q", entry_bytes, 32)[0]
            end_lba = struct.unpack_from("<Q", entry_bytes, 40)[0]
            attrs = struct.unpack_from("<Q", entry_bytes, 48)[0]
            name = entry_bytes[56:128].decode("utf-16le").rstrip("\x00")

            if start_lba < first_usable or end_lba > last_usable or start_lba > end_lba:
                raise DiskValidationError(
                    f"Partition {i + 1} '{name}' bounds out of range: LBA {start_lba}..{end_lba} "
                    f"(usable: {first_usable}..{last_usable})"
                )

            partitions.append(
                PartitionInfo(
                    index=len(partitions) + 1,
                    type_guid=type_guid,
                    unique_guid=unique_guid,
                    start_lba=start_lba,
                    end_lba=end_lba,
                    attributes=attrs,
                    name=name,
                )
            )

        if len(partitions) < 2:
            raise DiskValidationError(f"Expected at least 2 partitions for dual-partition architecture, found {len(partitions)}")

        # Partition Ordering and Overlap Check
        for idx in range(1, len(partitions)):
            prev_p = partitions[idx - 1]
            curr_p = partitions[idx]
            if curr_p.start_lba <= prev_p.end_lba:
                raise DiskValidationError(
                    f"Partition {curr_p.index} ('{curr_p.name}', start LBA {curr_p.start_lba}) overlaps "
                    f"Partition {prev_p.index} ('{prev_p.name}', end LBA {prev_p.end_lba})"
                )

        # Partition 1: Must be ESP, 1 MiB alignment
        p1 = partitions[0]
        if p1.type_guid != ESP_TYPE_GUID:
            raise DiskValidationError(f"Partition 1 type GUID {p1.type_guid} is not EFI System Partition ({ESP_TYPE_GUID})")
        if p1.start_lba != 2048:
            raise DiskValidationError(f"Partition 1 must start at 1MiB (LBA 2048), got LBA {p1.start_lba}")

        # Partition 2: Must be Microsoft Basic Data, 1 MiB alignment
        p2 = partitions[1]
        if p2.type_guid != BASIC_DATA_GUID:
            raise DiskValidationError(f"Partition 2 type GUID {p2.type_guid} is not Basic Data Partition ({BASIC_DATA_GUID})")
        if p2.start_lba % 2048 != 0:
            raise DiskValidationError(f"Partition 2 start LBA ({p2.start_lba}) is not aligned to 1MiB (2048 sectors)")

        return partitions

    # --------------------------------------------------------------------------
    # 3. FAT32 Filesystem Validation (Partition 1: MVX_BOOT)
    # --------------------------------------------------------------------------

    def validate_fat32_mvx_boot(self, f, p: PartitionInfo) -> Dict[str, Any]:
        """Validate FAT32 filesystem on Partition 1 (MVX_BOOT)."""
        offset = p.start_lba * 512
        f.seek(offset)
        vbr = f.read(512)

        if vbr[510:512] != b"\x55\xaa":
            raise DiskValidationError("MVX_BOOT VBR boot signature is not 0x55AA")

        bytes_per_sec = struct.unpack_from("<H", vbr, 11)[0]
        sec_per_clus = vbr[13]
        reserved_sec = struct.unpack_from("<H", vbr, 14)[0]
        num_fats = vbr[16]
        root_entries = struct.unpack_from("<H", vbr, 17)[0]
        total_sec_32 = struct.unpack_from("<I", vbr, 32)[0]
        sec_per_fat_32 = struct.unpack_from("<I", vbr, 36)[0]
        root_clus = struct.unpack_from("<I", vbr, 44)[0]
        fsinfo_sec = struct.unpack_from("<H", vbr, 48)[0]
        backup_boot_sec = struct.unpack_from("<H", vbr, 50)[0]
        label = vbr[71:82].decode("ascii", errors="ignore").strip()
        fs_type = vbr[82:90].decode("ascii", errors="ignore").strip()

        if bytes_per_sec != 512:
            raise DiskValidationError(f"MVX_BOOT bytes per sector must be 512, got {bytes_per_sec}")
        if root_entries != 0:
            raise DiskValidationError("MVX_BOOT root entries must be 0 for FAT32")
        if label != "MVX_BOOT":
            raise DiskValidationError(f"MVX_BOOT label mismatch: expected 'MVX_BOOT', got '{label}'")
        if "FAT32" not in fs_type:
            raise DiskValidationError(f"MVX_BOOT filesystem type must be FAT32, got '{fs_type}'")

        # Cluster Count FAT32 Threshold: Total clusters must be >= 65,525
        data_sectors = total_sec_32 - (reserved_sec + num_fats * sec_per_fat_32)
        total_clusters = data_sectors // sec_per_clus
        if total_clusters < 65525:
            raise DiskValidationError(
                f"MVX_BOOT cluster count ({total_clusters}) is below FAT32 minimum threshold of 65,525 clusters"
            )

        # Validate FSInfo at sector 1
        f.seek(offset + fsinfo_sec * 512)
        fsinfo = f.read(512)
        if fsinfo[0:4] != b"RRaA" or fsinfo[484:488] != b"rrAa":
            raise DiskValidationError("MVX_BOOT FSInfo sector signature mismatch")

        # Check Backup VBR at sector backup_boot_sec (sector 6)
        f.seek(offset + backup_boot_sec * 512)
        backup_vbr = f.read(512)
        if backup_vbr != vbr:
            raise DiskValidationError(f"MVX_BOOT backup VBR at sector {backup_boot_sec} does not match primary VBR")

        return {
            "label": label,
            "filesystem": "FAT32",
            "bytes_per_sector": bytes_per_sec,
            "sectors_per_cluster": sec_per_clus,
            "sectors_per_fat": sec_per_fat_32,
            "total_clusters": total_clusters,
            "root_cluster": root_clus,
        }

    # --------------------------------------------------------------------------
    # 4. exFAT Filesystem Validation (Partition 2: MVX_DATA)
    # --------------------------------------------------------------------------

    def validate_exfat_mvx_data(self, f, p: PartitionInfo) -> Dict[str, Any]:
        """Validate exFAT filesystem on Partition 2 (MVX_DATA)."""
        offset = p.start_lba * 512
        f.seek(offset)
        main_boot = bytearray(f.read(512 * 12))

        # Check Sector 0 (VBR)
        vbr = main_boot[0:512]
        if vbr[3:11] != b"EXFAT   ":
            raise DiskValidationError(f"MVX_DATA VBR filesystem name must be 'EXFAT   ', got {vbr[3:11]!r}")
        if vbr[510:512] != b"\x55\xaa":
            raise DiskValidationError("MVX_DATA VBR boot signature is not 0x55AA")

        part_offset_lba = struct.unpack_from("<Q", vbr, 64)[0]
        if part_offset_lba != p.start_lba:
            raise DiskValidationError(f"MVX_DATA partition offset LBA ({part_offset_lba}) does not match GPT start ({p.start_lba})")

        vol_length_sec = struct.unpack_from("<Q", vbr, 72)[0]
        if vol_length_sec != p.sector_count:
            raise DiskValidationError(f"MVX_DATA volume length ({vol_length_sec}) does not match partition size ({p.sector_count})")

        fat_offset = struct.unpack_from("<I", vbr, 80)[0]
        fat_length = struct.unpack_from("<I", vbr, 84)[0]
        cluster_heap_offset = struct.unpack_from("<I", vbr, 88)[0]
        cluster_count = struct.unpack_from("<I", vbr, 92)[0]
        root_dir_cluster = struct.unpack_from("<I", vbr, 96)[0]

        # Check Sector 11: Boot Checksum
        recorded_chk = struct.unpack_from("<I", main_boot, 11 * 512)[0]
        calc_chk = 0
        for i, b in enumerate(main_boot[: 11 * 512]):
            if i in (106, 107, 112):
                continue
            calc_chk = (((calc_chk << 31) & 0xFFFFFFFF) | (calc_chk >> 1)) + b
            calc_chk &= 0xFFFFFFFF

        if calc_chk != recorded_chk:
            raise DiskValidationError(f"MVX_DATA exFAT boot checksum mismatch: {hex(calc_chk)} != {hex(recorded_chk)}")

        # Check Backup Boot Region (Sectors 12-23)
        f.seek(offset + 12 * 512)
        backup_boot = f.read(512 * 12)
        if backup_boot != main_boot:
            raise DiskValidationError("MVX_DATA backup boot region does not match main boot region")

        # Read Root Directory entries to verify label and subdirectories
        clus_size = 512 * (1 << vbr[109])
        root_offset = offset + (cluster_heap_offset + (root_dir_cluster - 2) * (1 << vbr[109])) * 512
        f.seek(root_offset)
        root_entries = f.read(clus_size)

        found_label = ""
        found_subdirs: Set[str] = set()

        for idx in range(0, clus_size, 32):
            entry = root_entries[idx : idx + 32]
            etype = entry[0]
            if etype == 0x00:
                break
            elif etype == 0x83:  # Volume Label
                c_len = entry[1]
                found_label = entry[2 : 2 + c_len * 2].decode("utf-16le")
            elif etype == 0xC1:  # File Name Entry
                fn = entry[2:32].decode("utf-16le").rstrip("\x00")
                if fn:
                    found_subdirs.add(fn)

        if found_label != "MVX_DATA":
            raise DiskValidationError(f"MVX_DATA volume label mismatch: expected 'MVX_DATA', got '{found_label}'")

        required_subdirs = {"ISO", "config", "metadata", "backups"}
        missing_subdirs = required_subdirs - found_subdirs
        if missing_subdirs:
            raise DiskValidationError(f"MVX_DATA missing required subdirectories: {missing_subdirs}")

        return {
            "label": found_label,
            "filesystem": "exFAT",
            "cluster_count": cluster_count,
            "root_directory_cluster": root_dir_cluster,
            "subdirectories": list(found_subdirs),
        }

    # --------------------------------------------------------------------------
    # 5. Direct Sector-Level File Reading (No Kernel Loopback Required)
    # --------------------------------------------------------------------------

    def read_file_from_exfat(self, relative_path: str) -> bytes:
        """Read a file directly from the raw exFAT partition (Partition 2).

        Supports paths like 'config/mventx.json'.
        """
        norm_path = relative_path.replace("\\", "/").strip("/")
        parts = norm_path.split("/")

        with open(self.disk_path, "rb") as f:
            partitions = self.validate_gpt(f)
            p2 = partitions[1]
            p2_offset = p2.start_lba * 512

            f.seek(p2_offset)
            vbr = f.read(512)
            cluster_shift = vbr[109]
            clus_size = 512 * (1 << cluster_shift)
            cluster_heap_offset = struct.unpack_from("<I", vbr, 88)[0]
            root_dir_cluster = struct.unpack_from("<I", vbr, 96)[0]

            def cluster_offset(clus: int) -> int:
                return p2_offset + (cluster_heap_offset + (clus - 2) * (1 << cluster_shift)) * 512

            curr_dir_cluster = root_dir_cluster

            # Traverse directory path
            for part_idx, part_name in enumerate(parts):
                is_last = (part_idx == len(parts) - 1)
                f.seek(cluster_offset(curr_dir_cluster))
                dir_bytes = f.read(clus_size)

                # Search directory sets (groups of 32-byte records)
                target_first_cluster = None
                target_size = None
                target_is_dir = False

                idx = 0
                while idx < len(dir_bytes):
                    entry = dir_bytes[idx : idx + 32]
                    etype = entry[0]
                    if etype == 0x00:
                        break

                    if etype == 0x85:  # File Directory Entry
                        sec_count = entry[1]
                        attrs = struct.unpack_from("<H", entry, 4)[0]
                        is_dir_entry = bool(attrs & 0x10)

                        # Stream Extension Entry (0xC0) - relative offsets within 32-byte stream_entry
                        stream_entry = dir_bytes[idx + 32 : idx + 64]
                        stream_first_cluster = struct.unpack_from("<I", stream_entry, 20)[0]
                        stream_valid_size = struct.unpack_from("<Q", stream_entry, 8)[0]

                        # File Name Entries (0xC1...)
                        name_chars = []
                        for s in range(1, sec_count):
                            fn_entry = dir_bytes[idx + (s + 1) * 32 : idx + (s + 2) * 32]
                            if fn_entry[0] == 0xC1:
                                name_chars.append(fn_entry[2:32].decode("utf-16le").rstrip("\x00"))

                        entry_name = "".join(name_chars)
                        if entry_name.lower() == part_name.lower():
                            target_first_cluster = stream_first_cluster
                            target_size = stream_valid_size
                            target_is_dir = is_dir_entry
                            break

                        idx += (1 + sec_count) * 32
                    else:
                        idx += 32

                if target_first_cluster is None:
                    raise FileNotFoundError(f"Path component '{part_name}' not found in exFAT filesystem: {relative_path}")

                if is_last:
                    if target_is_dir:
                        raise IsADirectoryError(f"Target '{relative_path}' is a directory, not a file")
                    # Read file data
                    f.seek(cluster_offset(target_first_cluster))
                    return f.read(target_size)
                else:
                    if not target_is_dir:
                        raise NotADirectoryError(f"Path component '{part_name}' is not a directory")
                    curr_dir_cluster = target_first_cluster

            raise FileNotFoundError(f"File not found: {relative_path}")

    def list_exfat_root_files(self) -> List[str]:
        """List filenames and directory names in the root of the exFAT partition."""
        with open(self.disk_path, "rb") as f:
            partitions = self.validate_gpt(f)
            p2 = partitions[1]
            p2_offset = p2.start_lba * 512

            f.seek(p2_offset)
            vbr = f.read(512)
            cluster_shift = vbr[109]
            clus_size = 512 * (1 << cluster_shift)
            cluster_heap_offset = struct.unpack_from("<I", vbr, 88)[0]
            root_dir_cluster = struct.unpack_from("<I", vbr, 96)[0]

            def cluster_offset(clus: int) -> int:
                return p2_offset + (cluster_heap_offset + (clus - 2) * (1 << cluster_shift)) * 512

            f.seek(cluster_offset(root_dir_cluster))
            dir_bytes = f.read(clus_size)

            names = []
            idx = 0
            while idx < len(dir_bytes):
                entry = dir_bytes[idx : idx + 32]
                etype = entry[0]
                if etype == 0x00:
                    break
                if etype == 0x85:
                    sec_count = entry[1]
                    name_chars = []
                    for s in range(1, sec_count):
                        fn_entry = dir_bytes[idx + (s + 1) * 32 : idx + (s + 2) * 32]
                        if fn_entry[0] == 0xC1:
                            name_chars.append(fn_entry[2:32].decode("utf-16le").rstrip("\x00"))
                    names.append("".join(name_chars))
                    idx += (1 + sec_count) * 32
                else:
                    idx += 32
            return names

    def read_file_from_fat32(self, relative_path: str) -> bytes:
        """Read a file directly from the raw FAT32 partition (Partition 1: MVX_BOOT).

        Supports paths like 'boot/grub/grub.cfg' or 'EFI/BOOT/BOOTX64.EFI'.
        """
        norm_path = relative_path.replace("\\", "/").strip("/")
        parts = norm_path.split("/")

        with open(self.disk_path, "rb") as f:
            partitions = self.validate_gpt(f)
            p1 = partitions[0]
            p1_offset = p1.start_lba * 512

            f.seek(p1_offset)
            vbr = f.read(512)
            bytes_per_sec = struct.unpack_from("<H", vbr, 11)[0]
            sec_per_clus = vbr[13]
            clus_size = bytes_per_sec * sec_per_clus
            reserved_sec = struct.unpack_from("<H", vbr, 14)[0]
            num_fats = vbr[16]
            sec_per_fat_32 = struct.unpack_from("<I", vbr, 36)[0]
            root_clus = struct.unpack_from("<I", vbr, 44)[0]

            fat_start = p1_offset + reserved_sec * bytes_per_sec
            data_start = p1_offset + (reserved_sec + num_fats * sec_per_fat_32) * bytes_per_sec

            def read_fat_entry(cluster: int) -> int:
                f.seek(fat_start + cluster * 4)
                return struct.unpack("<I", f.read(4))[0] & 0x0FFFFFFF

            def cluster_offset(cluster: int) -> int:
                return data_start + (cluster - 2) * clus_size

            def read_cluster_chain(start_cluster: int, size: int) -> bytes:
                data = bytearray()
                curr = start_cluster
                while curr < 0x0FFFFFF8 and len(data) < size:
                    f.seek(cluster_offset(curr))
                    chunk = f.read(clus_size)
                    data.extend(chunk)
                    curr = read_fat_entry(curr)
                return bytes(data[:size])

            curr_dir_cluster = root_clus

            for part_idx, part_name in enumerate(parts):
                is_last = (part_idx == len(parts) - 1)

                # Read all clusters of current directory
                dir_clusters = []
                c = curr_dir_cluster
                while c < 0x0FFFFFF8:
                    dir_clusters.append(c)
                    c = read_fat_entry(c)

                target_cluster = None
                target_size = 0
                target_is_dir = False
                lfn_parts: List[str] = []

                for dir_c in dir_clusters:
                    f.seek(cluster_offset(dir_c))
                    dir_bytes = f.read(clus_size)

                    for idx in range(0, clus_size, 32):
                        entry = dir_bytes[idx : idx + 32]
                        if entry[0] == 0x00:
                            break
                        if entry[0] == 0xE5:
                            lfn_parts.clear()
                            continue

                        attr = entry[11]
                        if attr == 0x0F:  # LFN entry
                            # Decode LFN characters: bytes 1..10, 14..25, 28..31
                            chars = entry[1:11] + entry[14:26] + entry[28:32]
                            lfn_str = chars.decode("utf-16le", errors="ignore").split("\x00")[0]
                            lfn_parts.insert(0, lfn_str)
                            continue

                        # Regular 8.3 entry
                        name_raw = entry[0:8].decode("ascii", errors="ignore").strip()
                        ext_raw = entry[8:11].decode("ascii", errors="ignore").strip()
                        short_name = f"{name_raw}.{ext_raw}" if ext_raw else name_raw

                        full_name = "".join(lfn_parts) if lfn_parts else short_name
                        lfn_parts.clear()

                        if full_name.lower() == part_name.lower() or short_name.lower() == part_name.lower():
                            hi = struct.unpack_from("<H", entry, 20)[0]
                            lo = struct.unpack_from("<H", entry, 26)[0]
                            target_cluster = (hi << 16) | lo
                            target_size = struct.unpack_from("<I", entry, 28)[0]
                            target_is_dir = bool(attr & 0x10)
                            break

                    if target_cluster is not None:
                        break

                if target_cluster is None:
                    raise FileNotFoundError(f"Path component '{part_name}' not found in FAT32 filesystem: {relative_path}")

                if is_last:
                    if target_is_dir:
                        raise IsADirectoryError(f"Target '{relative_path}' is a directory, not a file")
                    return read_cluster_chain(target_cluster, target_size)
                else:
                    if not target_is_dir:
                        raise NotADirectoryError(f"Path component '{part_name}' is not a directory")
                    curr_dir_cluster = target_cluster

            raise FileNotFoundError(f"File not found: {relative_path}")
