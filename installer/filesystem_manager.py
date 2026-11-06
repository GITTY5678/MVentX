"""Filesystem Management Layer for MVentX Installation.

Initializes FAT32 and exFAT filesystem structures and default MVentX
directory trees for the dual-partition architecture.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Optional

from core.storage.storage_manager import StorageManager


class FilesystemManager:
    """Initializes partition filesystem structures and default configurations."""

    @classmethod
    def initialize_data_partition(
        cls,
        data_mount_path: Path | str,
        initial_title: str = "MVentX Multiboot System",
        initial_timeout: int = 10,
    ) -> Path:
        """Initialize the MVX_DATA partition directory layout and default mventx.json."""
        data_dir = Path(data_mount_path).resolve()
        storage = StorageManager(data_mount=data_dir)
        storage.initialize_data_partition_structure()

        # Ensure mventx.json exists with zero registered environments
        config_path = storage.config_file_path
        if not config_path.is_file():
            default_config = {
                "version": "1.0.0",
                "meta": {
                    "title": initial_title,
                    "timeout": initial_timeout,
                    "created_at": None,
                    "updated_at": None,
                },
                "environments": [],
            }
            storage.write_configuration_atomically(default_config, create_backup=False)

        return config_path

    @classmethod
    def initialize_boot_partition(
        cls,
        boot_mount_path: Path | str,
    ) -> None:
        """Create required directory skeletons on MVX_BOOT."""
        boot_dir = Path(boot_mount_path).resolve()
        (boot_dir / "EFI" / "BOOT").mkdir(parents=True, exist_ok=True)
        (boot_dir / "boot" / "grub" / "fonts").mkdir(parents=True, exist_ok=True)
        (boot_dir / "boot" / "grub" / "x86_64-efi").mkdir(parents=True, exist_ok=True)

