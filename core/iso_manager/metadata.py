"""ISO Metadata Extraction and Persistence Service.

Extracts non-invasive metadata hints (volume ID, system ID, publisher,
estimated architecture, and boot methods) from ISO headers to assist users when registering.
Persists and manages metadata records in MVX_DATA/metadata/.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Union

from core.iso_manager.validation import (
    ISO_SECTOR_SIZE,
    SIGNATURE_ISO9660,
    VRA_START_OFFSET,
    ISOValidator,
)


@dataclass
class ISOMetadata:
    """Metadata extracted from an ISO image or stored in MVX_DATA/metadata/."""
    file_name: str
    file_size_bytes: int
    volume_identifier: Optional[str] = None
    system_identifier: Optional[str] = None
    publisher_identifier: Optional[str] = None
    detected_architecture: Optional[str] = None  # e.g., 'x86_64', 'i386', 'arm64'
    suggested_boot_method: Optional[str] = None
    detected_recipe: Optional[str] = None
    recipe_confidence: str = "uncertain"  # 'high', 'medium', 'uncertain'
    discovered_kernel_path: Optional[str] = None
    discovered_initrd_path: Optional[str] = None
    warnings: Optional[List[str]] = None
    registered_at: Optional[str] = None  # ISO 8601 UTC timestamp
    has_el_torito_boot: bool = False
    checksum: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ISOMetadata:
        return cls(
            file_name=data.get("file_name", ""),
            file_size_bytes=data.get("file_size_bytes", 0),
            volume_identifier=data.get("volume_identifier"),
            system_identifier=data.get("system_identifier"),
            publisher_identifier=data.get("publisher_identifier"),
            detected_architecture=data.get("detected_architecture"),
            suggested_boot_method=data.get("suggested_boot_method"),
            detected_recipe=data.get("detected_recipe"),
            recipe_confidence=data.get("recipe_confidence", "uncertain"),
            discovered_kernel_path=data.get("discovered_kernel_path"),
            discovered_initrd_path=data.get("discovered_initrd_path"),
            warnings=data.get("warnings"),
            registered_at=data.get("registered_at"),
            has_el_torito_boot=data.get("has_el_torito_boot", False),
            checksum=data.get("checksum"),
        )


class ISOMetadataExtractor:
    """Inspects ISO volume descriptors to extract initial metadata hints."""

    _ARCH_PATTERNS = [
        (re.compile(r"x86_64|amd64|x64", re.IGNORECASE), "x86_64"),
        (re.compile(r"arm64|aarch64", re.IGNORECASE), "arm64"),
        (re.compile(r"i386|i686|x86", re.IGNORECASE), "i386"),
    ]

    @classmethod
    def detect_architecture(cls, *hint_strings: Optional[str]) -> Optional[str]:
        """Infer CPU architecture from filename or volume identifiers."""
        for hint in hint_strings:
            if not hint:
                continue
            for pattern, arch in cls._ARCH_PATTERNS:
                if pattern.search(hint):
                    return arch
        return None

    @classmethod
    def extract_metadata(
        cls,
        iso_path: Union[Path, str],
        calculate_hash: bool = False,
        hash_algorithm: str = "sha256",
    ) -> ISOMetadata:
        """Inspect ISO 9660 descriptors and return populated ISOMetadata."""
        p = Path(iso_path)
        if not p.is_file():
            raise FileNotFoundError(f"ISO file does not exist: {p}")

        file_size = p.stat().st_size
        volume_id = None
        system_id = None
        publisher_id = None
        has_el_torito = False

        if file_size >= VRA_START_OFFSET + ISO_SECTOR_SIZE:
            try:
                with open(p, "rb") as f:
                    f.seek(VRA_START_OFFSET)
                    for _ in range(32):
                        sector = f.read(ISO_SECTOR_SIZE)
                        if len(sector) < ISO_SECTOR_SIZE:
                            break
                        type_code = sector[0]
                        identifier = sector[1:6]

                        if identifier == SIGNATURE_ISO9660:
                            if type_code == 0x01 and volume_id is None:
                                # Primary Volume Descriptor
                                sys_raw = sector[8:40].decode("latin-1", errors="replace").strip()
                                system_id = sys_raw if sys_raw else None

                                vol_raw = sector[40:72].decode("latin-1", errors="replace").strip()
                                volume_id = vol_raw if vol_raw else None

                                pub_raw = sector[318:446].decode("latin-1", errors="replace").strip()
                                publisher_id = pub_raw if pub_raw else None
                            elif type_code == 0x00:
                                boot_sys = sector[7:39].decode("latin-1", errors="replace").strip()
                                if "EL TORITO" in boot_sys.upper():
                                    has_el_torito = True
                            elif type_code == 0xFF:
                                break
            except OSError:
                pass

        arch = cls.detect_architecture(p.name, volume_id, system_id)

        # Deep inspection of ISO filesystem structure to discover kernel, initramfs, and recipe
        from core.iso_manager.iso_inspector import ISOInspector
        inspector = ISOInspector(p)
        insp_res = inspector.inspect()

        if insp_res.volume_id:
            volume_id = insp_res.volume_id
        if insp_res.system_id:
            system_id = insp_res.system_id
        if insp_res.publisher_id:
            publisher_id = insp_res.publisher_id

        detected_recipe = insp_res.detected_recipe
        recipe_confidence = insp_res.confidence
        discovered_kernel = insp_res.discovered_kernel_path
        discovered_initrd = insp_res.discovered_initrd_path
        inspection_warnings = insp_res.warnings if insp_res.warnings else None

        # Suggest default boot method matching detected distribution recipe
        if detected_recipe and detected_recipe not in ("generic_kernel", "generic"):
            suggested_method = detected_recipe
        else:
            suggested_method = "linux_generic_loopback"

        checksum_data = None
        if calculate_hash:
            calculated_hash = ISOValidator.calculate_checksum(p, algorithm=hash_algorithm)
            checksum_data = {"algorithm": hash_algorithm, "value": calculated_hash}

        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

        return ISOMetadata(
            file_name=p.name,
            file_size_bytes=file_size,
            volume_identifier=volume_id,
            system_identifier=system_id,
            publisher_identifier=publisher_id,
            detected_architecture=arch,
            suggested_boot_method=suggested_method,
            detected_recipe=detected_recipe,
            recipe_confidence=recipe_confidence,
            discovered_kernel_path=discovered_kernel,
            discovered_initrd_path=discovered_initrd,
            warnings=inspection_warnings,
            registered_at=now_utc,
            has_el_torito_boot=has_el_torito,
            checksum=checksum_data,
        )

    @classmethod
    def save_metadata(cls, metadata_dir: Union[Path, str], env_id: str, metadata: ISOMetadata) -> Path:
        """Save environment metadata JSON record to MVX_DATA/metadata/<env_id>.json."""
        meta_dir = Path(metadata_dir)
        meta_dir.mkdir(parents=True, exist_ok=True)
        meta_file = meta_dir / f"{env_id}.json"

        content = json.dumps(metadata.to_dict(), indent=2, ensure_ascii=False) + "\n"
        with open(meta_file, "w", encoding="utf-8") as f:
            f.write(content)
        return meta_file

    @classmethod
    def load_metadata(cls, metadata_dir: Union[Path, str], env_id: str) -> Optional[ISOMetadata]:
        """Load environment metadata JSON record from MVX_DATA/metadata/<env_id>.json."""
        meta_file = Path(metadata_dir) / f"{env_id}.json"
        if not meta_file.is_file():
            return None
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ISOMetadata.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return None

    @classmethod
    def delete_metadata(cls, metadata_dir: Union[Path, str], env_id: str) -> bool:
        """Remove metadata cache file for the given environment."""
        meta_file = Path(metadata_dir) / f"{env_id}.json"
        if meta_file.is_file():
            try:
                meta_file.unlink()
                return True
            except OSError:
                return False
        return False
