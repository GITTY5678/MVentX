"""Storage Manager for MVentX.

Manages interactions with mounted MVentX partitions:
- Partition 1: MVX_BOOT (FAT32 EFI System Partition)
- Partition 2: MVX_DATA (exFAT Data Partition)

Ensures atomic configuration writing, automated backups, and synchronization
between the authoritative JSON configuration on MVX_DATA and the generated
GRUB boot menu on MVX_BOOT.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


class StorageManager:
    """Handles path resolution, atomic updates, backups, and menu synchronization on MVentX drives."""

    def __init__(self, data_mount: Path | str, boot_mount: Optional[Path | str] = None) -> None:
        self.data_mount = Path(data_mount).resolve()
        self.boot_mount = Path(boot_mount).resolve() if boot_mount else None

    # --------------------------------------------------------------------------
    # Path Properties
    # --------------------------------------------------------------------------

    @property
    def config_directory_path(self) -> Path:
        """Return path to the config directory on the data partition."""
        return self.data_mount / "config"

    @property
    def config_file_path(self) -> Path:
        """Return authoritative path to mventx.json on the data partition (config/mventx.json)."""
        return self.config_directory_path / "mventx.json"

    @property
    def iso_directory_path(self) -> Path:
        """Return path to the ISO storage directory on the data partition (ISO/)."""
        return self.data_mount / "ISO"

    @property
    def metadata_directory_path(self) -> Path:
        """Return path to the metadata cache directory on the data partition (metadata/)."""
        return self.data_mount / "metadata"

    @property
    def backups_directory_path(self) -> Path:
        """Return path to the configuration backups directory on the data partition (backups/)."""
        return self.data_mount / "backups"

    @property
    def generated_menu_path(self) -> Optional[Path]:
        """Return path to mventx_menu.cfg on the boot partition (boot/grub/mventx_menu.cfg)."""
        if self.boot_mount:
            return self.boot_mount / "boot" / "grub" / "mventx_menu.cfg"
        return None

    # --------------------------------------------------------------------------
    # Partition Directory Setup & Validation
    # --------------------------------------------------------------------------

    def initialize_data_partition_structure(self, default_config: Optional[Dict[str, Any]] = None) -> None:
        """Create standard MVX_DATA directory structure: ISO/, config/, metadata/, backups/.

        If config/mventx.json does not exist, writes the provided default_config or defaults.json.
        """
        self.data_mount.mkdir(parents=True, exist_ok=True)
        self.iso_directory_path.mkdir(parents=True, exist_ok=True)
        self.config_directory_path.mkdir(parents=True, exist_ok=True)
        self.metadata_directory_path.mkdir(parents=True, exist_ok=True)
        self.backups_directory_path.mkdir(parents=True, exist_ok=True)

        if not self.config_file_path.exists():
            if default_config is None:
                # Load project defaults.json
                defaults_file = Path(__file__).resolve().parent.parent / "config" / "defaults.json"
                if defaults_file.exists():
                    with open(defaults_file, "r", encoding="utf-8") as f:
                        default_config = json.load(f)
                else:
                    default_config = {
                        "version": "1.0.0",
                        "meta": {"title": "MVentX Multiboot System", "theme": "mventx", "timeout": 10},
                        "environments": [],
                    }
            self.write_configuration_atomically(default_config, create_backup=False)

    def validate_data_partition_structure(self) -> Tuple[bool, List[str]]:
        """Verify presence of required MVX_DATA directories and configuration file."""
        missing = []
        if not self.data_mount.exists():
            return False, ["data_mount does not exist"]

        for req_dir, name in [
            (self.iso_directory_path, "ISO/"),
            (self.config_directory_path, "config/"),
            (self.metadata_directory_path, "metadata/"),
            (self.backups_directory_path, "backups/"),
        ]:
            if not req_dir.is_dir():
                missing.append(f"Missing required directory: {name}")

        if not self.config_file_path.is_file():
            missing.append("Missing authoritative configuration file: config/mventx.json")

        return len(missing) == 0, missing

    # --------------------------------------------------------------------------
    # Configuration Management
    # --------------------------------------------------------------------------

    def load_configuration(self, auto_repair: bool = False) -> Dict[str, Any]:
        """Load and parse the authoritative mventx.json configuration from MVX_DATA.

        If auto_repair is True, automatically repairs corrupt/truncated files or recovers
        from the latest intact backup in backups/.
        """
        if not auto_repair:
            if not self.config_file_path.exists():
                raise FileNotFoundError(f"Configuration file not found: {self.config_file_path}")
            with open(self.config_file_path, "r", encoding="utf-8") as f:
                return json.load(f)

        from core.config.repair import ConfigRepairer
        cfg, _ = ConfigRepairer.load_or_recover(
            config_path=self.config_file_path,
            backups_dir=self.backups_directory_path,
            iso_dir=self.iso_directory_path,
        )
        return cfg

    def backup_configuration(self, max_retained_backups: int = 10) -> Path:
        """Create a timestamped backup of config/mventx.json in backups/.

        Rotates backups so that at most max_retained_backups files are kept.
        """
        if not self.config_file_path.exists():
            raise FileNotFoundError(f"Cannot backup non-existent configuration: {self.config_file_path}")

        self.backups_directory_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_filename = f"mventx_{timestamp}.json"
        backup_path = self.backups_directory_path / backup_filename

        # Counter for collisions in same second
        counter = 1
        while backup_path.exists():
            backup_path = self.backups_directory_path / f"mventx_{timestamp}_{counter}.json"
            counter += 1

        shutil.copy2(self.config_file_path, backup_path)

        # Rotate old backups
        all_backups = sorted(
            self.backups_directory_path.glob("mventx_*.json"),
            key=lambda p: p.stat().st_mtime,
        )
        while len(all_backups) > max_retained_backups:
            oldest = all_backups.pop(0)
            try:
                oldest.unlink()
            except OSError:
                pass

        return backup_path

    def list_backups(self) -> List[Path]:
        """Return list of available backup files sorted from newest to oldest."""
        if not self.backups_directory_path.exists():
            return []
        return sorted(
            self.backups_directory_path.glob("mventx_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

    def restore_backup(self, backup_path: Path | str, sync_menu: bool = True) -> Dict[str, Any]:
        """Restore configuration from a backup file.

        Validates backup JSON structure, creates a backup of current config,
        writes the restored configuration atomically, and resynchronizes boot menu.
        Guarantees that files in ISO/ are never touched or deleted.
        """
        bp = Path(backup_path).resolve()
        if not bp.exists():
            raise FileNotFoundError(f"Backup file not found: {bp}")

        with open(bp, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(f"Corrupted backup JSON file: {e}") from e

        if not isinstance(data, dict) or "version" not in data or "environments" not in data:
            raise ValueError(f"Invalid backup file structure: missing required keys in {bp.name}")

        self.write_configuration_atomically(data, create_backup=True)

        if sync_menu and self.boot_mount:
            self.sync_boot_menu()

        return data

    def write_configuration_atomically(
        self,
        config_data: Union[Dict[str, Any], str],
        create_backup: bool = True,
    ) -> None:
        """Write configuration to config/mventx.json atomically using a temporary file.

        Creates a timestamped backup before overwriting if requested and file already exists.
        """
        if isinstance(config_data, dict):
            content = json.dumps(config_data, indent=2, ensure_ascii=False) + "\n"
        elif isinstance(config_data, str):
            # Verify JSON syntax
            json.loads(config_data)
            content = config_data
        else:
            raise TypeError(f"config_data must be dict or str, got {type(config_data).__name__}")

        self.config_directory_path.mkdir(parents=True, exist_ok=True)

        if create_backup and self.config_file_path.exists():
            self.backup_configuration()

        # Atomic write to temporary file in same directory
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.config_directory_path,
                prefix=".mventx.json.",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_file = Path(f.name)
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            temp_file.replace(self.config_file_path)
            temp_file = None
        finally:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

    # --------------------------------------------------------------------------
    # Menu Synchronization
    # --------------------------------------------------------------------------

    def sync_boot_menu(self, generator: Optional[Any] = None) -> Optional[Path]:
        """Read authoritative mventx.json from MVX_DATA and generate mventx_menu.cfg on MVX_BOOT.

        Returns the path to the updated mventx_menu.cfg on the boot partition.
        """
        if not self.boot_mount:
            return None

        if generator is None:
            from core.boot_config.generator import GrubMenuGenerator
            generator = GrubMenuGenerator(data_dir=self.data_mount)
        elif not getattr(generator, "data_dir", None):
            generator.data_dir = self.data_mount

        config_data = self.load_configuration()
        menu_path = self.generated_menu_path
        if not menu_path:
            return None

        generator.write_menu_file(config_data, menu_path)
        return menu_path
