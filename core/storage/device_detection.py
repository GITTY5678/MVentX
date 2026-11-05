"""Device Detection Service.

Identifies connected removable storage media and detects active MVentX drives
by looking for partition labels 'MVX_BOOT' and 'MVX_DATA'.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class DiscoveredDrive:
    """Represents a physical storage device attached to the host."""
    device_path: str
    model: str
    size_bytes: int
    is_removable: bool
    is_mventx_drive: bool
    boot_partition_mount: Optional[Path] = None
    data_partition_mount: Optional[Path] = None


class DeviceDetector:
    """Discovers connected storage drives across Linux and Windows platforms."""

    @classmethod
    def list_removable_drives(cls) -> List[DiscoveredDrive]:
        """Scan system for all removable storage devices."""
        raise NotImplementedError("To be implemented in Phase 2: Core Subsystems")

    @classmethod
    def detect_mventx_drives(cls) -> List[DiscoveredDrive]:
        """Scan system specifically for initialized MVentX USB drives."""
        raise NotImplementedError("To be implemented in Phase 2: Core Subsystems")

