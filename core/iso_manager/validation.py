"""ISO Validation Service.

Validates the integrity, format, and structure of ISO images prior to registration
or boot execution.

Architectural Rule: Validating filesystem signatures confirms the structure of the
ISO image, but does not guarantee that the image is bootable on a given hardware/firmware
platform. Actual boot compatibility is validated during boot execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
from typing import List, Optional, Tuple, Union

# Sector size standard for ISO 9660 and UDF media
ISO_SECTOR_SIZE = 2048

# Offset where Volume Recognition Area (VRA) begins (Sector 16: 16 * 2048 = 32768)
VRA_START_OFFSET = 16 * ISO_SECTOR_SIZE

# Recognized Standard Identifiers in the Volume Recognition Area
SIGNATURE_ISO9660 = b"CD001"
SIGNATURE_HIGH_SIERRA = b"CDROM"
SIGNATURE_UDF_BEA = b"BEA01"
SIGNATURE_UDF_NSR02 = b"NSR02"
SIGNATURE_UDF_NSR03 = b"NSR03"
SIGNATURE_UDF_TEA = b"TEA01"


@dataclass
class ISOValidationResult:
    """Detailed results from validating an ISO file."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    file_size_bytes: int = 0
    filesystem_format: str = "Unknown"  # "ISO9660", "UDF", "ISO9660/UDF", "Unknown"
    volume_identifier: Optional[str] = None
    system_identifier: Optional[str] = None
    publisher_identifier: Optional[str] = None
    has_el_torito_boot: bool = False
    disclaimer: str = (
        "Valid filesystem signatures confirm image structure only. "
        "They do not guarantee that the image is bootable on a given target firmware."
    )

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "file_size_bytes": self.file_size_bytes,
            "filesystem_format": self.filesystem_format,
            "volume_identifier": self.volume_identifier,
            "system_identifier": self.system_identifier,
            "publisher_identifier": self.publisher_identifier,
            "has_el_torito_boot": self.has_el_torito_boot,
            "disclaimer": self.disclaimer,
        }


class ISOValidationError(Exception):
    """Raised when ISO file validation fails."""


class ISOValidator:
    """Verifies ISO filesystem headers, partition records, and checksums."""

    @staticmethod
    def validate_file_exists(iso_path: Union[Path, str]) -> bool:
        """Verify that the ISO file exists, is a regular file, and is accessible."""
        p = Path(iso_path)
        try:
            return p.exists() and p.is_file()
        except OSError:
            return False

    @staticmethod
    def verify_iso_header(iso_path: Union[Path, str]) -> Tuple[bool, str, Optional[str], Optional[str]]:
        """Verify ISO 9660 primary volume descriptor or UDF filesystem header.

        Returns:
            Tuple of (is_valid, format_description, volume_id, system_id)
        """
        p = Path(iso_path)
        if not p.is_file():
            return False, "File does not exist", None, None

        size = p.stat().st_size
        if size < VRA_START_OFFSET + ISO_SECTOR_SIZE:
            return False, f"File too small for ISO headers ({size} bytes, minimum {VRA_START_OFFSET + ISO_SECTOR_SIZE})", None, None

        has_iso9660 = False
        has_udf = False
        has_boot_record = False
        volume_id = None
        system_id = None

        try:
            with open(p, "rb") as f:
                # Seek to Volume Recognition Area at sector 16
                f.seek(VRA_START_OFFSET)

                # Scan up to 32 sectors of descriptors
                for _ in range(32):
                    sector = f.read(ISO_SECTOR_SIZE)
                    if len(sector) < ISO_SECTOR_SIZE:
                        break

                    type_code = sector[0]
                    identifier = sector[1:6]

                    if identifier == SIGNATURE_ISO9660:
                        has_iso9660 = True
                        if type_code == 0x01 and volume_id is None:  # Primary Volume Descriptor
                            # System ID: bytes 8..40
                            sys_raw = sector[8:40].decode("latin-1", errors="replace").strip()
                            system_id = sys_raw if sys_raw else None
                            # Volume ID: bytes 40..72
                            vol_raw = sector[40:72].decode("latin-1", errors="replace").strip()
                            volume_id = vol_raw if vol_raw else None
                        elif type_code == 0x00:  # Boot Record (El Torito)
                            boot_sys = sector[7:39].decode("latin-1", errors="replace").strip()
                            if "EL TORITO" in boot_sys.upper():
                                has_boot_record = True
                        elif type_code == 0xFF:  # Terminator
                            break
                    elif identifier in (SIGNATURE_UDF_BEA, SIGNATURE_UDF_NSR02, SIGNATURE_UDF_NSR03):
                        has_udf = True
                    elif identifier == SIGNATURE_UDF_TEA:
                        has_udf = True
                        break
                    elif identifier == SIGNATURE_HIGH_SIERRA:
                        has_iso9660 = True
        except (OSError, PermissionError) as exc:
            return False, f"Unable to read ISO file: {exc}", None, None

        if has_iso9660 and has_udf:
            fmt = "ISO9660/UDF (Hybrid)"
        elif has_iso9660:
            fmt = "ISO9660"
        elif has_udf:
            fmt = "UDF"
        else:
            return False, "No valid ISO9660 or UDF volume descriptor signature found at sector 16", None, None

        return True, fmt, volume_id, system_id

    @classmethod
    def validate_iso(cls, iso_path: Union[Path, str]) -> ISOValidationResult:
        """Perform full validation of an ISO file for manual registration.

        Checks:
        1. Path is provided and extension is .iso.
        2. File exists and is readable.
        3. File size is greater than 0.
        4. Valid ISO9660 or UDF filesystem signatures exist at sector 16.
        """
        p = Path(iso_path)
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Extension check
        if p.suffix.lower() != ".iso":
            errors.append(f"File extension must be '.iso', got '{p.suffix}'")

        # 2. File existence check
        if not p.exists():
            errors.append(f"ISO file does not exist: {p}")
            return ISOValidationResult(is_valid=False, errors=errors, warnings=warnings)

        if not p.is_file():
            errors.append(f"Path is not a regular file: {p}")
            return ISOValidationResult(is_valid=False, errors=errors, warnings=warnings)

        # 3. File size check
        try:
            stat_info = p.stat()
            size = stat_info.st_size
        except OSError as exc:
            errors.append(f"Unable to read file metadata: {exc}")
            return ISOValidationResult(is_valid=False, errors=errors, warnings=warnings)

        if size == 0:
            errors.append("ISO file is empty (0 bytes)")
            return ISOValidationResult(is_valid=False, errors=errors, warnings=warnings, file_size_bytes=0)

        # 4. Header & filesystem signature check
        valid_hdr, fmt_or_err, vol_id, sys_id = cls.verify_iso_header(p)
        if not valid_hdr:
            errors.append(fmt_or_err)
            return ISOValidationResult(
                is_valid=False,
                errors=errors,
                warnings=warnings,
                file_size_bytes=size,
                filesystem_format="Unknown",
            )

        # Check El Torito boot catalog presence
        has_el_torito = False
        try:
            with open(p, "rb") as f:
                f.seek(VRA_START_OFFSET)
                for _ in range(16):
                    sector = f.read(ISO_SECTOR_SIZE)
                    if len(sector) < ISO_SECTOR_SIZE:
                        break
                    if sector[0] == 0x00 and sector[1:6] == SIGNATURE_ISO9660:
                        boot_sys = sector[7:39].decode("latin-1", errors="replace").strip()
                        if "EL TORITO" in boot_sys.upper():
                            has_el_torito = True
                            break
                    if sector[0] == 0xFF:
                        break
        except OSError:
            pass

        return ISOValidationResult(
            is_valid=True,
            errors=[],
            warnings=warnings,
            file_size_bytes=size,
            filesystem_format=fmt_or_err,
            volume_identifier=vol_id,
            system_identifier=sys_id,
            has_el_torito_boot=has_el_torito,
        )

    @staticmethod
    def calculate_checksum(iso_path: Union[Path, str], algorithm: str = "sha256") -> str:
        """Compute cryptographic hash of the ISO file in 64 KiB chunks."""
        p = Path(iso_path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")

        algo_lower = algorithm.lower()
        if algo_lower == "sha256":
            hasher = hashlib.sha256()
        elif algo_lower == "sha512":
            hasher = hashlib.sha512()
        elif algo_lower == "md5":
            hasher = hashlib.md5()
        else:
            raise ValueError(f"Unsupported hash algorithm '{algorithm}'; choose sha256, sha512, or md5")

        with open(p, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)

        return hasher.hexdigest()

    @classmethod
    def verify_checksum(cls, iso_path: Union[Path, str], expected_hash: str, algorithm: str = "sha256") -> bool:
        """Compare calculated hash against expected hash."""
        calculated = cls.calculate_checksum(iso_path, algorithm=algorithm)
        return calculated.lower() == expected_hash.strip().lower()
