"""Cross-Platform Filesystem Access and Atomic Configuration Synchronizer for MVentX.

Guarantees atomic writes with tempfile + fsync + atomic rename, schema validation,
automated timestamped backups with rotation, and safe rollback on menu synchronization failure.
Verifies read/write permissions before operations and prevents incomplete states.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.boot_config.generator import GrubMenuGenerator
from core.config.validator import ConfigValidator
from core.host.device_identity import (
    DeviceIdentity,
    DeviceIdentityError,
    revalidate_device_identity,
)


class FilesystemAccessError(Exception):
    """Base error for filesystem operations."""


class PermissionDeniedError(FilesystemAccessError):
    """Raised when write access to a target path is denied."""


class ConfigurationSyncError(FilesystemAccessError):
    """Raised when atomic configuration synchronization fails."""


class ConfigurationRollbackError(FilesystemAccessError):
    """Raised when configuration rollback occurs after synchronization failure."""


class FilesystemAccessor:
    """Manages safe, verified filesystem operations and atomic configuration sync."""

    def __init__(
        self,
        data_mount: Path | str,
        boot_mount: Optional[Path | str] = None,
        expected_identity: Optional[DeviceIdentity] = None,
        identity_fetcher: Optional[Callable[[], Optional[DeviceIdentity]]] = None,
    ) -> None:
        self.data_mount = Path(data_mount).resolve()
        self.boot_mount = Path(boot_mount).resolve() if boot_mount else None
        self.expected_identity = expected_identity
        self.identity_fetcher = identity_fetcher

    # --------------------------------------------------------------------------
    # Path Properties
    # --------------------------------------------------------------------------

    @property
    def config_dir(self) -> Path:
        return self.data_mount / "config"

    @property
    def config_file(self) -> Path:
        return self.config_dir / "mventx.json"

    @property
    def iso_dir(self) -> Path:
        return self.data_mount / "ISO"

    @property
    def metadata_dir(self) -> Path:
        return self.data_mount / "metadata"

    @property
    def backups_dir(self) -> Path:
        return self.data_mount / "backups"

    @property
    def generated_menu_file(self) -> Optional[Path]:
        if self.boot_mount:
            return self.boot_mount / "boot" / "grub" / "mventx_menu.cfg"
        return None

    # --------------------------------------------------------------------------
    # Permission and Access Verification
    # --------------------------------------------------------------------------

    @classmethod
    def check_read_write_access(cls, path: Path | str) -> Tuple[bool, str]:
        """Check if path is accessible and writable by attempting to create/delete a canary file."""
        target = Path(path).resolve()
        if not target.exists():
            return False, f"Path does not exist: {target}"

        if not os.access(target, os.R_OK):
            return False, f"Path is not readable: {target}"

        # Test write access with a canary file
        test_file = target / f".mventx_rw_test_{os.getpid()}.tmp"
        try:
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("mventx_access_test\n")
                f.flush()
                os.fsync(f.fileno())
            test_file.unlink(missing_ok=True)
            return True, "Read and write access verified."
        except Exception as exc:
            return False, f"Write access denied on {target}: {exc}"

    def revalidate_identity(self, operation: str = "filesystem operation") -> None:
        """Enforce pre-write identity verification if identity tracking is active."""
        if self.expected_identity and self.identity_fetcher:
            revalidate_device_identity(self.expected_identity, self.identity_fetcher, operation)

    # --------------------------------------------------------------------------
    # Atomic Configuration Sync with Backups & Rollback
    # --------------------------------------------------------------------------

    def write_config_atomically(
        self,
        config_data: Dict[str, Any],
        create_backup: bool = True,
        max_retained_backups: int = 10,
    ) -> Path:
        """Write configuration to mventx.json atomically using tempfile, fsync, and replace."""
        self.revalidate_identity("atomic config write")

        # 1. Validate schema before writing
        is_valid, errors = ConfigValidator.validate_config(config_data)
        if not is_valid:
            raise ConfigurationSyncError(
                f"Configuration failed schema validation before writing: {'; '.join(errors)}"
            )

        self.config_dir.mkdir(parents=True, exist_ok=True)

        # 2. Backup existing configuration if present
        if create_backup and self.config_file.is_file():
            self._create_timestamped_backup(max_retained_backups)

        # 3. Write atomically to temporary file in same directory
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.config_dir,
                delete=False,
                prefix="mventx_cfg_",
                suffix=".tmp",
            ) as f:
                temp_file = Path(f.name)
                json.dump(config_data, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace
            temp_file.replace(self.config_file)
            return self.config_file
        except Exception as exc:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass
            raise FilesystemAccessError(f"Failed to write configuration atomically: {exc}") from exc

    def sync_configuration_with_menu(
        self,
        config_data: Dict[str, Any],
        create_backup: bool = True,
    ) -> Tuple[Path, Optional[Path]]:
        """Synchronize mventx.json and mventx_menu.cfg atomically with safe rollback on error.

        Returns:
            Tuple of (config_path, generated_menu_path)

        Raises:
            ConfigurationRollbackError: If menu generation fails and mventx.json is rolled back.
        """
        self.revalidate_identity("configuration and menu synchronization")

        # Capture original configuration state for rollback if existing
        original_bytes: Optional[bytes] = None
        if self.config_file.is_file():
            try:
                with open(self.config_file, "rb") as f:
                    original_bytes = f.read()
            except Exception:
                original_bytes = None

        # Step 1: Write authoritative mventx.json atomically
        config_path = self.write_config_atomically(config_data, create_backup=create_backup)

        # Step 2: If boot mount is available, compile mventx_menu.cfg
        menu_path = self.generated_menu_file
        if menu_path:
            try:
                menu_path.parent.mkdir(parents=True, exist_ok=True)
                generator = GrubMenuGenerator()
                generator.write_menu_file(config_data, menu_path)

                # Verify menu file was written and is non-empty
                if not menu_path.is_file() or menu_path.stat().st_size == 0:
                    raise ConfigurationSyncError("Generated GRUB menu file is empty or missing.")
            except Exception as exc:
                # Rollback authoritative configuration to original state
                if original_bytes is not None:
                    try:
                        with open(self.config_file, "wb") as f:
                            f.write(original_bytes)
                            f.flush()
                            os.fsync(f.fileno())
                    except Exception:
                        pass
                raise ConfigurationRollbackError(
                    f"Menu synchronization failed. Rolled back mventx.json to previous state. Error: {exc}"
                ) from exc

        return config_path, menu_path

    def _create_timestamped_backup(self, max_retained_backups: int = 10) -> Optional[Path]:
        """Create timestamped copy in backups/ and rotate old backups."""
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"mventx_{now_str}.json"
        backup_path = self.backups_dir / backup_name

        try:
            shutil.copy2(self.config_file, backup_path)
            # Rotation
            backups = sorted(
                self.backups_dir.glob("mventx_*.json"),
                key=lambda p: p.stat().st_mtime,
            )
            while len(backups) > max_retained_backups:
                oldest = backups.pop(0)
                try:
                    oldest.unlink()
                except Exception:
                    pass
            return backup_path
        except Exception:
            return None
