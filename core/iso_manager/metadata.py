"""ISO Metadata Extraction Service.

Extracts non-invasive metadata hints (volume ID, system ID, publisher,
estimated OS architecture) from ISO headers to assist users when registering.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ISOMetadata:
    """Metadata extracted from an ISO image."""
    file_name: str
    file_size_bytes: int
    volume_identifier: Optional[str] = None
    system_identifier: Optional[str] = None
    publisher_identifier: Optional[str] = None
    detected_architecture: Optional[str] = None  # e.g., 'x86_64', 'arm64'
    suggested_boot_method: Optional[str] = None


class ISOMetadataExtractor:
    """Inspects ISO volume descriptors to extract initial metadata hints."""

    @classmethod
    def extract_metadata(cls, iso_path: Path) -> ISOMetadata:
        """Inspect ISO 9660 descriptors and return populated ISOMetadata."""
        raise NotImplementedError("To be implemented in Phase 2: Core Subsystems")

