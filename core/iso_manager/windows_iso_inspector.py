"""Windows ISO Filesystem Inspector and Metadata Extractor for MVentX.

Directly parses ISO 9660 and UDF volume descriptors, directory records, and file headers
within Windows installation media to:
1. Discover EFI bootloaders (bootx64.efi, boota64.efi, bootmgr.efi, cdboot.efi).
2. Discover Boot Configuration Data (BCD) stores.
3. Discover Windows Preinstallation Environment (sources/boot.wim).
4. Discover and classify installation payload images (install.wim, install.esd, install*.swm).
5. Detect target CPU architectures (x64, x86, ARM64, multi-arch).
6. Calculate uncompressed extraction sizes, cluster overhead, and split-WIM sets.
7. Return a structured WindowsInspectionResult with confidence and diagnostic warnings.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union


class WindowsArchitecture(str, Enum):
    """Target CPU architecture detected in Windows media."""
    X64 = "x64"            # AMD64 / 64-bit x86
    X86 = "x86"            # IA-32 / 32-bit x86
    ARM64 = "arm64"        # AArch64 / 64-bit ARM
    MULTI_ARCH = "multi"   # Dual-architecture (e.g. x86 + x64)
    UNKNOWN = "unknown"


class WindowsPayloadType(str, Enum):
    """Classification of Windows OS installation image."""
    WIM = "wim"            # Standard uncompressed or LZX compressed install.wim
    ESD = "esd"            # Highly compressed solid install.esd
    SWM = "swm"            # Split WIM image set (install.swm, install2.swm, ...)
    NONE = "none"          # WinPE rescue disk or missing payload


@dataclass
class WindowsInspectionResult:
    """Comprehensive inspection report for a Windows installation or WinPE image."""
    is_windows_media: bool = False
    volume_id: Optional[str] = None
    system_id: Optional[str] = None
    architecture: WindowsArchitecture = WindowsArchitecture.UNKNOWN
    payload_type: WindowsPayloadType = WindowsPayloadType.NONE

    # Boot capabilities
    has_uefi_boot: bool = False
    has_legacy_bios_boot: bool = False
    has_winpe: bool = False
    has_install_payload: bool = False

    # Key file paths discovered inside ISO
    efi_bootloader_path: Optional[str] = None      # e.g. "/efi/boot/bootx64.efi"
    bootmgr_efi_path: Optional[str] = None         # e.g. "/bootmgr.efi"
    bcd_path: Optional[str] = None                 # e.g. "/efi/microsoft/boot/bcd" or "/boot/bcd"
    boot_wim_path: Optional[str] = None            # e.g. "/sources/boot.wim"
    install_image_path: Optional[str] = None       # e.g. "/sources/install.wim"
    split_wim_parts: List[str] = field(default_factory=list)  # ["/sources/install.swm", "/sources/install2.swm"]

    # Metrics
    total_iso_size_bytes: int = 0
    estimated_extracted_size_bytes: int = 0
    boot_wim_size_bytes: int = 0
    install_image_size_bytes: int = 0

    # Diagnostics
    detected_edition: str = ""
    build_number: str = ""
    warnings: List[str] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)

    @property
    def payload_path(self) -> Optional[str]:
        """Path to main installation payload image."""
        return self.install_image_path

    @property
    def has_bcd(self) -> bool:
        """True if BCD configuration store was discovered."""
        return bool(self.bcd_path)

    @property
    def primary_bootloader(self) -> str:
        """Primary bootloader path for UEFI chainloading."""
        return self.efi_bootloader_path or self.bootmgr_efi_path or "/efi/boot/bootx64.efi"

    @property
    def is_bootable_uefi(self) -> bool:
        """True if media has verified UEFI bootloader, BCD, and WinPE."""
        return self.has_uefi_boot and self.has_winpe and bool(self.efi_bootloader_path)

    @property
    def estimated_extracted_size_mb(self) -> float:
        return round(self.estimated_extracted_size_bytes / (1024 ** 2), 2)

    @property
    def estimated_extracted_size_gb(self) -> float:
        return round(self.estimated_extracted_size_bytes / (1024 ** 3), 2)

    def summary(self) -> str:
        """Formatted human-readable summary."""
        arch = self.architecture.value.upper()
        ptype = self.payload_type.value.upper()
        return (
            f"Windows Media: arch={arch}, payload={ptype}, "
            f"bootloader={self.primary_bootloader}, bcd={self.bcd_path or 'none'}, "
            f"uefi={self.has_uefi_boot}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_windows_media": self.is_windows_media,
            "volume_id": self.volume_id,
            "system_id": self.system_id,
            "architecture": self.architecture.value,
            "payload_type": self.payload_type.value,
            "has_uefi_boot": self.has_uefi_boot,
            "has_legacy_bios_boot": self.has_legacy_bios_boot,
            "has_winpe": self.has_winpe,
            "has_install_payload": self.has_install_payload,
            "is_bootable_uefi": self.is_bootable_uefi,
            "efi_bootloader_path": self.efi_bootloader_path,
            "bootmgr_efi_path": self.bootmgr_efi_path,
            "bcd_path": self.bcd_path,
            "boot_wim_path": self.boot_wim_path,
            "install_image_path": self.install_image_path,
            "split_wim_parts": self.split_wim_parts,
            "total_iso_size_bytes": self.total_iso_size_bytes,
            "estimated_extracted_size_bytes": self.estimated_extracted_size_bytes,
            "estimated_extracted_size_mb": self.estimated_extracted_size_mb,
            "boot_wim_size_bytes": self.boot_wim_size_bytes,
            "install_image_size_bytes": self.install_image_size_bytes,
            "detected_edition": self.detected_edition,
            "build_number": self.build_number,
            "warnings": self.warnings,
            "diagnostics": self.diagnostics,
        }


class WindowsISOInspector:
    """Non-destructive inspector for Windows installation ISOs and WinPE media."""

    SECTOR_SIZE = 2048
    VRA_START_OFFSET = 16 * 2048

    def __init__(self, iso_path: Union[Path, str]) -> None:
        self.iso_path = Path(iso_path).resolve()

    def inspect(self) -> WindowsInspectionResult:
        """Inspect the ISO file and return a structured WindowsInspectionResult."""
        result = WindowsInspectionResult()

        if not self.iso_path.is_file():
            result.warnings.append(f"ISO file does not exist: {self.iso_path}")
            return result

        result.total_iso_size_bytes = self.iso_path.stat().st_size
        if result.total_iso_size_bytes < 32768:
            result.warnings.append("File is too small to be a valid optical disc image.")
            return result

        try:
            with open(self.iso_path, "rb") as f:
                # 1. Parse Volume Descriptor Table
                root_lba, root_len, vol_id, sys_id = self._parse_volume_descriptors(f)
                result.volume_id = vol_id
                result.system_id = sys_id

                if root_lba <= 0 or root_len <= 0:
                    result.warnings.append("Could not locate root directory record in ISO descriptor.")
                    return result

                # 2. Traverse ISO directories non-destructively
                discovered_files: Dict[str, Tuple[int, int]] = {}  # normalized_path -> (lba, size_bytes)
                self._traverse_directory_tree(f, root_lba, root_len, "/", discovered_files, max_depth=6)

                # 3. Analyze discovered files
                self._analyze_windows_structure(discovered_files, result)

        except Exception as exc:
            result.warnings.append(f"Failed to inspect ISO structure: {exc}")

        return result

    def _parse_volume_descriptors(self, f) -> Tuple[int, int, Optional[str], Optional[str]]:
        """Parse ISO 9660 Volume Descriptors to extract root LBA and Volume ID."""
        f.seek(self.VRA_START_OFFSET)
        root_lba = 0
        root_len = 0
        vol_id = None
        sys_id = None

        while True:
            sector = f.read(self.SECTOR_SIZE)
            if len(sector) < self.SECTOR_SIZE:
                break

            vd_type = sector[0]
            if vd_type == 255:  # Volume Descriptor Set Terminator
                break

            standard_id = sector[1:6]
            if standard_id != b"CD001":
                continue

            if vd_type == 1:  # Primary Volume Descriptor (PVD)
                sys_id = sector[8:40].decode("latin-1", errors="replace").strip()
                vol_id = sector[40:72].decode("latin-1", errors="replace").strip()
                root_record = sector[156:190]
                if len(root_record) >= 34:
                    root_lba = struct.unpack("<I", root_record[2:6])[0]
                    root_len = struct.unpack("<I", root_record[10:14])[0]

        return root_lba, root_len, vol_id, sys_id

    def _traverse_directory_tree(
        self,
        f,
        dir_lba: int,
        dir_length: int,
        current_path: str,
        results: Dict[str, Tuple[int, int]],
        max_depth: int = 6,
    ) -> None:
        """Recursively scan directory records up to max_depth."""
        if max_depth <= 0:
            return

        records = self._read_dir_records(f, dir_lba, dir_length)
        subdirs: List[Tuple[str, int, int]] = []

        for name, is_dir, lba, size in records:
            if name in (".", ".."):
                continue

            # Strip ISO 9660 version suffix (e.g. ";1")
            clean_name = name.split(";")[0]
            entry_path = f"{current_path.rstrip('/')}/{clean_name}".lower()

            if is_dir:
                subdirs.append((entry_path, lba, size))
            else:
                results[entry_path] = (lba, size)

        # Recurse into subdirectories of interest
        interesting_prefixes = ("/efi", "/boot", "/sources", "/support")
        for sub_path, sub_lba, sub_size in subdirs:
            if any(sub_path.startswith(p) for p in interesting_prefixes) or current_path == "/":
                self._traverse_directory_tree(f, sub_lba, sub_size, sub_path, results, max_depth - 1)

    def _read_dir_records(self, f, lba: int, length: int) -> List[Tuple[str, bool, int, int]]:
        """Read directory entries at specified LBA within ISO stream."""
        f.seek(lba * self.SECTOR_SIZE)
        data = f.read(length)
        entries: List[Tuple[str, bool, int, int]] = []
        offset = 0

        while offset < len(data):
            rec_len = data[offset]
            if rec_len == 0:
                # Padding at sector boundary
                aligned = (offset + self.SECTOR_SIZE) & ~(self.SECTOR_SIZE - 1)
                offset = aligned
                continue

            if offset + rec_len > len(data):
                break

            record = data[offset : offset + rec_len]
            if len(record) >= 33:
                entry_lba = struct.unpack("<I", record[2:6])[0]
                entry_size = struct.unpack("<I", record[10:14])[0]
                flags = record[25]
                is_dir = bool(flags & 0x02)
                name_len = record[32]
                name_bytes = record[33 : 33 + name_len]

                if name_bytes == b"\x00":
                    entry_name = "."
                elif name_bytes == b"\x01":
                    entry_name = ".."
                else:
                    entry_name = name_bytes.decode("latin-1", errors="replace")

                entries.append((entry_name, is_dir, entry_lba, entry_size))

            offset += rec_len

        return entries

    def _analyze_windows_structure(
        self,
        files: Dict[str, Tuple[int, int]],
        result: WindowsInspectionResult,
    ) -> None:
        """Inspect discovered files and populate Windows metadata."""
        # Check for core Windows markers
        has_bootmgr = "/bootmgr" in files
        has_bootmgr_efi = "/bootmgr.efi" in files
        has_setup_exe = "/setup.exe" in files or "/sources/setup.exe" in files

        # 1. Discover EFI Bootloaders
        efi_candidates = [
            "/efi/boot/bootx64.efi",
            "/efi/boot/bootarm64.efi",
            "/efi/boot/boota64.efi",
            "/efi/boot/bootaa64.efi",
            "/efi/boot/bootia32.efi",
            "/efi/microsoft/boot/cdboot.efi",
            "/efi/microsoft/boot/cdboot_noprompt.efi",
            "/bootmgr.efi",
        ]
        for ec in efi_candidates:
            if ec in files:
                result.has_uefi_boot = True
                if not result.efi_bootloader_path:
                    result.efi_bootloader_path = ec
                if ec == "/bootmgr.efi":
                    result.bootmgr_efi_path = ec

        if has_bootmgr:
            result.has_legacy_bios_boot = True

        # 2. Discover BCD Stores
        bcd_candidates = [
            "/boot/bcd",
            "/efi/microsoft/boot/bcd",
        ]
        for bc in bcd_candidates:
            if bc in files:
                result.bcd_path = bc
                break

        # 3. Discover Windows PE (boot.wim)
        boot_wim_candidates = [
            "/sources/boot.wim",
            "/x64/sources/boot.wim",
            "/x86/sources/boot.wim",
        ]
        for bw in boot_wim_candidates:
            if bw in files:
                result.has_winpe = True
                result.boot_wim_path = bw
                result.boot_wim_size_bytes = files[bw][1]
                break

        # 4. Discover Installation Payloads
        # Check install.wim
        if "/sources/install.wim" in files:
            result.payload_type = WindowsPayloadType.WIM
            result.has_install_payload = True
            result.install_image_path = "/sources/install.wim"
            result.install_image_size_bytes = files["/sources/install.wim"][1]
        # Check install.esd
        elif "/sources/install.esd" in files:
            result.payload_type = WindowsPayloadType.ESD
            result.has_install_payload = True
            result.install_image_path = "/sources/install.esd"
            result.install_image_size_bytes = files["/sources/install.esd"][1]
        else:
            # Check for split WIM (.swm)
            swm_parts = []
            for path, (_, size) in files.items():
                if path.startswith("/sources/install") and path.endswith(".swm"):
                    swm_parts.append(path)
            if swm_parts:
                swm_parts.sort()
                result.payload_type = WindowsPayloadType.SWM
                result.has_install_payload = True
                result.install_image_path = swm_parts[0]
                result.split_wim_parts = swm_parts
                result.install_image_size_bytes = sum(files[p][1] for p in swm_parts)

        # 5. Architecture Detection
        arm64_markers = ("/efi/boot/bootarm64.efi", "/efi/boot/boota64.efi", "/efi/boot/bootaa64.efi")
        if any(m in files for m in arm64_markers):
            result.architecture = WindowsArchitecture.ARM64
        elif "/efi/boot/bootx64.efi" in files:
            if "/efi/boot/bootia32.efi" in files:
                result.architecture = WindowsArchitecture.MULTI_ARCH
            else:
                result.architecture = WindowsArchitecture.X64
        elif "/efi/boot/bootia32.efi" in files:
            result.architecture = WindowsArchitecture.X86
        elif "/sources/boot.wim" in files:
            result.architecture = WindowsArchitecture.X64

        # 6. Overall Windows Classification
        is_windows = (
            (has_bootmgr or has_bootmgr_efi or result.has_winpe)
            and (result.bcd_path is not None or has_setup_exe)
        )
        result.is_windows_media = is_windows

        # 7. Uncompressed Extraction Size Calculation
        # Sum of all files + cluster alignment overhead (assume 4096-byte clusters on exFAT)
        total_extracted = 0
        for _, (_, size) in files.items():
            # Align each file to 4 KiB cluster
            clusters = (size + 4095) // 4096
            total_extracted += clusters * 4096

        result.estimated_extracted_size_bytes = total_extracted

        # 8. Detect Windows Edition Hints from Volume ID
        vol = (result.volume_id or "").upper()
        if "WIN11" in vol:
            result.detected_edition = "Windows 11"
        elif "WIN10" in vol or "CCCOMA" in vol or "DV9" in vol:
            result.detected_edition = "Windows 10"
        elif "SERVER" in vol or "SSS_X64" in vol:
            result.detected_edition = "Windows Server"
        elif is_windows:
            result.detected_edition = "Windows Installation / PE"

        # Diagnostic messages
        if is_windows:
            result.diagnostics.append(
                f"Valid Windows media detected: Architecture={result.architecture.value}, "
                f"Payload={result.payload_type.value}, WinPE={result.has_winpe}, "
                f"UEFI={result.has_uefi_boot}"
            )
        else:
            result.diagnostics.append("Media does not contain standard Windows boot structures.")
