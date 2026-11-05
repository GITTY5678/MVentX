"""Storage Manager.

Manages interactions with mounted MVentX partitions:
- Partition 1: MVX_BOOT (FAT32)
- Partition 2: MVX_DATA (exFAT)

Ensures atomic configuration writing and automated backup before modifications.
"""

from pathlib import Path
from typing import Optional


class StorageManager:
    """Handles path resolution, atomic updates, and backups on the MVentX USB drive."""

    def __init__(self, data_mount: Path, boot_mount: Optional[Path] = None) -> None:
        self.data_mount = data_mount
        self.boot_mount = boot_mount

    @property
    def config_file_path(self) -> Path:
        """Return path to mventx.json on the data partition."""
        return self.data_mount / "mventx" / "mventx.json"

    @property
    def iso_directory_path(self) -> Path:
        """Return path to the ISO storage directory on the data partition."""
        return self.data_mount / "isos"

    @property
    def generated_menu_path(self) -> Optional[Path]:
        """Return path to mventx_menu.cfg on the boot partition."""
        if self.boot_mount:
            return self.boot_mount / "boot" / "grub" / "mventx_menu.cfg"
        return None

    def backup_configuration(self) -> Path:
        """Create a timestamped backup of the current configuration file."""
        raise NotImplementedError("To be implemented in Phase 2: Core Subsystems")

    def write_configuration_atomically(self, raw_json_data: str) -> None:
        """Write new configuration to a temporary file before atomically replacing target."""
        raise NotImplementedError("To be implemented in Phase 2: Core Subsystems")

