"""ISO Environment Business Service for MVentX Manager.

Mediates all UI interactions with the core ISO registration engine, storage manager,
and validation subsystems without duplicating business logic in Qt views.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Tuple, Union

from PySide6.QtCore import QObject, Signal

from core.boot_config.generator import ConfigSecurityError, ConfigValidationError
from core.iso_manager import (
    DuplicateRegistrationError,
    EnvironmentNotFoundError,
    EnvironmentRecord,
    ISORegistrationManager,
    ISOValidationResult,
    ISOValidator,
    MenuSyncRollbackError,
    PathTraversalError,
    RegistrationError,
    StorageMetrics,
)
from core.storage.storage_manager import StorageManager
from manager.services.device_service import ActiveTarget, DeviceService


class ISOService(QObject):
    """Provides high-level reactive APIs for environment management and storage."""

    environments_changed = Signal()
    operation_succeeded = Signal(str)
    operation_failed = Signal(str, str)  # (title, detail)
    metrics_updated = Signal(object)     # StorageMetrics

    def __init__(self, device_service: DeviceService, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.device_service = device_service
        self._registration_manager: Optional[ISORegistrationManager] = None

        # Listen for active target changes
        self.device_service.target_changed.connect(self._on_target_changed)
        self._on_target_changed(self.device_service.active_target)

    def _on_target_changed(self, target: Optional[ActiveTarget]) -> None:
        """Re-bind registration manager when target drive changes."""
        if target:
            try:
                storage = StorageManager(
                    data_mount=target.data_mount,
                    boot_mount=target.boot_mount,
                )
                self._registration_manager = ISORegistrationManager(storage)
            except Exception as exc:
                self._registration_manager = None
                self.operation_failed.emit("Target Initialization Error", str(exc))
        else:
            self._registration_manager = None

        self.environments_changed.emit()

    @property
    def is_ready(self) -> bool:
        return self._registration_manager is not None

    def get_registration_manager(self) -> Optional[ISORegistrationManager]:
        return self._registration_manager

    # --------------------------------------------------------------------------
    # Environment Listing & Inspection
    # --------------------------------------------------------------------------

    def list_environments(self) -> List[EnvironmentRecord]:
        """Return registered environments sorted by display order."""
        if not self._registration_manager:
            return []
        try:
            return self._registration_manager.list_environments()
        except Exception as exc:
            self.operation_failed.emit("Load Error", f"Failed to load environments: {exc}")
            return []

    def get_storage_metrics(self) -> Optional[StorageMetrics]:
        """Compute storage metrics for the active data partition."""
        if not self._registration_manager:
            return None
        try:
            metrics = self._registration_manager.get_storage_metrics()
            self.metrics_updated.emit(metrics)
            return metrics
        except Exception:
            return None

    def detect_missing_isos(self) -> List[Tuple[str, str]]:
        """Identify registered environments whose ISO files are missing."""
        if not self._registration_manager:
            return []
        try:
            return self._registration_manager.detect_missing_isos()
        except Exception:
            return []

    def list_unregistered_isos(self) -> List[Path]:
        """List ISO files in MVX_DATA/ISO/ that have not yet been registered."""
        if not self._registration_manager:
            return []
        try:
            return self._registration_manager.list_unregistered_isos()
        except Exception:
            return []

    def validate_iso(self, iso_path: Union[Path, str]) -> ISOValidationResult:
        """Validate an ISO file without registering it."""
        return ISOValidator.validate_iso(iso_path)

    # --------------------------------------------------------------------------
    # Registration & Modification Actions
    # --------------------------------------------------------------------------

    def register_iso(
        self,
        iso_file: Union[Path, str],
        display_name: str,
        boot_method: str = "linux_generic_loopback",
        description: Optional[str] = None,
        custom_args: Optional[str] = None,
        enabled: bool = True,
        calculate_hash: bool = False,
    ) -> Optional[EnvironmentRecord]:
        """Register a new ISO environment."""
        if not self._registration_manager:
            self.operation_failed.emit("Device Not Selected", "Please select a valid MVentX drive first.")
            return None

        try:
            record = self._registration_manager.register_iso(
                iso_path=iso_file,
                display_name=display_name,
                boot_method=boot_method,
                description=description,
                custom_args=custom_args,
                enabled=enabled,
                calculate_hash=calculate_hash,
            )
            self.operation_succeeded.emit(f"Successfully registered '{record.display_name}'.")
            self.environments_changed.emit()
            return record
        except (
            DuplicateRegistrationError,
            PathTraversalError,
            RegistrationError,
            ConfigValidationError,
            ConfigSecurityError,
            ValueError,
            FileNotFoundError,
        ) as exc:
            self.operation_failed.emit("Registration Failed", str(exc))
            return None
        except MenuSyncRollbackError as exc:
            self.operation_failed.emit("Menu Generation Error (Rolled Back)", str(exc))
            return None

    def register_windows_media(
        self,
        display_name: str,
        bootloader_path: str = "/efi/boot/bootx64.efi",
        staged_subfolder: Optional[str] = None,
        iso_file: Optional[Union[Path, str]] = None,
        description: Optional[str] = None,
        enabled: bool = True,
    ) -> Optional[EnvironmentRecord]:
        """Register a Windows installation media environment."""
        if not self._registration_manager:
            self.operation_failed.emit("Device Not Selected", "Please select a valid MVentX drive first.")
            return None

        try:
            record = self._registration_manager.register_windows_media(
                display_name=display_name,
                bootloader_path=bootloader_path,
                staged_subfolder=staged_subfolder,
                iso_path=iso_file,
                description=description,
                enabled=enabled,
            )
            self.operation_succeeded.emit(f"Successfully registered Windows environment '{record.display_name}'.")
            self.environments_changed.emit()
            return record
        except Exception as exc:
            self.operation_failed.emit("Registration Failed", str(exc))
            return None

    def rename_environment(self, env_id: str, new_name: str) -> bool:
        """Rename an existing environment."""
        if not self._registration_manager:
            return False

        try:
            self._registration_manager.rename_environment(env_id, new_name)
            self.operation_succeeded.emit(f"Renamed environment to '{new_name}'.")
            self.environments_changed.emit()
            return True
        except (EnvironmentNotFoundError, ConfigValidationError, ConfigSecurityError, ValueError) as exc:
            self.operation_failed.emit("Rename Failed", str(exc))
            return False

    def toggle_environment_enabled(self, env_id: str, enable: bool) -> bool:
        """Toggle an environment between enabled and disabled states."""
        if not self._registration_manager:
            return False

        try:
            if enable:
                self._registration_manager.enable_environment(env_id)
                self.operation_succeeded.emit("Environment enabled.")
            else:
                self._registration_manager.disable_environment(env_id)
                self.operation_succeeded.emit("Environment disabled.")
            self.environments_changed.emit()
            return True
        except Exception as exc:
            self.operation_failed.emit("Update Failed", str(exc))
            return False

    def reorder_environments(self, ordered_ids: List[str]) -> bool:
        """Update display ordering."""
        if not self._registration_manager:
            return False

        try:
            self._registration_manager.reorder_environments(ordered_ids)
            self.operation_succeeded.emit("Environment display order updated.")
            self.environments_changed.emit()
            return True
        except Exception as exc:
            self.operation_failed.emit("Reorder Failed", str(exc))
            return False

    def move_environment(self, env_id: str, direction: str) -> bool:
        """Move an environment up or down by 1 position."""
        envs = self.list_environments()
        ids = [e.id for e in envs]
        if env_id not in ids:
            return False

        idx = ids.index(env_id)
        if direction == "up" and idx > 0:
            ids[idx], ids[idx - 1] = ids[idx - 1], ids[idx]
            return self.reorder_environments(ids)
        elif direction == "down" and idx < len(ids) - 1:
            ids[idx], ids[idx + 1] = ids[idx + 1], ids[idx]
            return self.reorder_environments(ids)
        return False

    def remove_environment(
        self,
        env_id: str,
        delete_file: bool = False,
        confirm_deletion: bool = False,
    ) -> bool:
        """Remove environment registration."""
        if not self._registration_manager:
            return False

        try:
            self._registration_manager.remove_environment(
                env_id=env_id,
                delete_file=delete_file,
                confirm_deletion=confirm_deletion,
            )
            msg = "Environment registration removed."
            if delete_file and confirm_deletion:
                msg += " Underlying ISO file was deleted."
            self.operation_succeeded.emit(msg)
            self.environments_changed.emit()
            return True
        except Exception as exc:
            self.operation_failed.emit("Removal Failed", str(exc))
            return False

    # --------------------------------------------------------------------------
    # Settings & Backups
    # --------------------------------------------------------------------------

    def get_config_metadata(self) -> Dict[str, Any]:
        """Return the 'meta' section from mventx.json."""
        if not self._registration_manager:
            return {}
        try:
            config = self._registration_manager.storage_manager.load_configuration()
            return config.get("meta", {})
        except Exception:
            return {}

    def update_config_metadata(self, title: str, timeout: int, default_id: Optional[str] = None) -> bool:
        """Update global boot menu title and timeout."""
        if not self._registration_manager:
            return False

        try:
            storage = self._registration_manager.storage_manager
            config = storage.load_configuration()
            config["meta"]["title"] = title
            config["meta"]["timeout"] = timeout
            config["meta"]["default_entry_id"] = default_id if default_id else None

            storage.write_configuration_atomically(config, create_backup=True)
            storage.sync_boot_menu()
            self.operation_succeeded.emit("Boot menu settings updated.")
            return True
        except Exception as exc:
            self.operation_failed.emit("Settings Update Failed", str(exc))
            return False

    def list_backups(self) -> List[Path]:
        """List configuration backups in MVX_DATA/backups/."""
        if not self._registration_manager:
            return []
        backup_dir = self._registration_manager.storage_manager.backups_directory_path
        if not backup_dir.is_dir():
            return []
        return sorted(backup_dir.glob("mventx_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    def restore_backup(self, backup_file: Path) -> bool:
        """Restore an archived configuration backup."""
        if not self._registration_manager:
            return False

        try:
            storage = self._registration_manager.storage_manager
            storage.restore_backup(backup_file, sync_menu=True)
            self.operation_succeeded.emit(f"Restored configuration from {backup_file.name}.")
            self.environments_changed.emit()
            return True
        except Exception as exc:
            self.operation_failed.emit("Restore Failed", str(exc))
            return False

