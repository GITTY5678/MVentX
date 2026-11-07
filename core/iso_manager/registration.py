"""ISO Registration and Environment Management Service.

Handles manual registration, renaming, reordering, enabling, disabling,
and deregistration of ISO images within the authoritative MVentX configuration store.

Architectural Rules:
1. No automatic ISO discovery/registration is performed. All environments must be
   explicitly registered by the user. Directory scanning may be used only for
   file selection or missing-file detection, never for automatic registration.
2. The authoritative configuration lives on MVX_DATA at config/mventx.json.
3. Configuration writes are atomic and create timestamped backups.
4. Changes are synchronized with the GRUB menu generator on MVX_BOOT.
5. If menu generation fails, changes are safely rolled back to preserve system integrity.
6. Removing an environment registration must never delete the underlying ISO unless
   explicit confirmation is provided.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import datetime
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Dict, List, Optional, Tuple, Union

from core.boot_config.generator import (
    ConfigSecurityError,
    ConfigValidationError,
    GrubMenuGenerator,
    sanitize_display_name,
    sanitize_id,
    sanitize_path,
    validate_display_name,
)
from core.iso_manager.metadata import ISOMetadata, ISOMetadataExtractor
from core.iso_manager.validation import ISOValidationResult, ISOValidator
from core.storage.storage_manager import StorageManager


class RegistrationError(Exception):
    """Base exception for ISO registration operations."""


class DuplicateRegistrationError(RegistrationError):
    """Raised when an ISO image is already registered under an environment."""


class PathTraversalError(RegistrationError):
    """Raised when an ISO file path attempts to escape the MVX_DATA ISO directory."""


class EnvironmentNotFoundError(RegistrationError):
    """Raised when an environment ID is not found in the configuration."""


class MenuSyncRollbackError(RegistrationError):
    """Raised when menu synchronization fails and configuration was rolled back."""


@dataclass
class EnvironmentRecord:
    """Represents a single registered boot environment."""
    id: str
    display_name: str
    type: str  # 'iso' or 'chainload'
    boot_method: str
    enabled: bool
    order: int
    iso_path: Optional[str] = None
    description: Optional[str] = None
    custom_args: Optional[str] = None
    target_path: Optional[str] = None
    target_hint: Optional[str] = None
    recipe: Optional[str] = None
    kernel_path: Optional[str] = None
    initrd_path: Optional[str] = None
    checksum: Optional[Dict[str, str]] = None
    file_size_bytes: Optional[int] = None
    registered_at: Optional[str] = None
    staged_subfolder: Optional[str] = None
    bootloader_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary matching schema.json definitions."""
        data: Dict[str, Any] = {
            "id": self.id,
            "display_name": self.display_name,
            "type": self.type,
            "boot_method": self.boot_method,
            "enabled": self.enabled,
            "order": self.order,
        }
        if self.iso_path is not None:
            data["iso_path"] = self.iso_path
        if self.description is not None:
            data["description"] = self.description
        if self.custom_args is not None:
            data["custom_args"] = self.custom_args
        if self.recipe is not None:
            data["recipe"] = self.recipe
        if self.kernel_path is not None:
            data["kernel_path"] = self.kernel_path
        if self.initrd_path is not None:
            data["initrd_path"] = self.initrd_path
        if self.target_path is not None:
            data["target_path"] = self.target_path
        if self.target_hint is not None:
            data["target_hint"] = self.target_hint
        if self.checksum is not None:
            data["checksum"] = self.checksum
        if self.staged_subfolder is not None:
            data["staged_subfolder"] = self.staged_subfolder
        if self.bootloader_path is not None:
            data["bootloader_path"] = self.bootloader_path
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EnvironmentRecord:
        return cls(
            id=data["id"],
            display_name=data["display_name"],
            type=data["type"],
            boot_method=data["boot_method"],
            enabled=data["enabled"],
            order=data["order"],
            iso_path=data.get("iso_path"),
            description=data.get("description"),
            custom_args=data.get("custom_args"),
            recipe=data.get("recipe"),
            kernel_path=data.get("kernel_path"),
            initrd_path=data.get("initrd_path"),
            target_path=data.get("target_path"),
            target_hint=data.get("target_hint"),
            checksum=data.get("checksum"),
            staged_subfolder=data.get("staged_subfolder"),
            bootloader_path=data.get("bootloader_path"),
        )


@dataclass
class StorageMetrics:
    """Storage metrics for the MVentX data partition."""
    total_bytes: int
    used_bytes: int
    available_bytes: int
    registered_iso_count: int
    total_registered_iso_bytes: int
    missing_iso_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ISORegistrationManager:
    """Manages the lifecycle of registered boot environments on the MVentX USB drive."""

    def __init__(
        self,
        storage_or_data: Union[StorageManager, Path, str],
        boot_mount: Optional[Union[Path, str]] = None,
    ) -> None:
        if isinstance(storage_or_data, StorageManager):
            self.storage_manager = storage_or_data
        else:
            p = Path(storage_or_data).resolve()
            if p.is_file() and p.name == "mventx.json":
                # Config file passed; data_mount is parent of config directory
                data_mount = p.parent.parent
            else:
                data_mount = p
            self.storage_manager = StorageManager(data_mount=data_mount, boot_mount=boot_mount)

        # Ensure directory structure exists
        self.storage_manager.initialize_data_partition_structure()

    @property
    def config_path(self) -> Path:
        return self.storage_manager.config_file_path

    @property
    def iso_directory(self) -> Path:
        return self.storage_manager.iso_directory_path

    @property
    def metadata_directory(self) -> Path:
        return self.storage_manager.metadata_directory_path

    # --------------------------------------------------------------------------
    # Path & Safety Helpers
    # --------------------------------------------------------------------------

    def resolve_iso_path(self, raw_iso_path: Union[Path, str]) -> Tuple[Path, str]:
        """Validate and resolve an ISO path within the MVX_DATA ISO directory.

        Returns:
            Tuple of (absolute_resolved_path, canonical_relative_path_for_json)

        Raises:
            PathTraversalError: If path attempts to navigate outside MVX_DATA/ISO/.
            FileNotFoundError: If the ISO file does not exist.
        """
        raw_str = str(raw_iso_path).strip()
        if not raw_str:
            raise ValueError("ISO path cannot be empty")

        # Sanitize string to check for forbidden characters or traversal patterns
        try:
            sanitize_path(raw_str)
        except (ConfigSecurityError, ConfigValidationError) as exc:
            raise PathTraversalError(f"Path traversal or security violation in '{raw_str}': {exc}") from exc

        iso_dir = self.iso_directory.resolve()
        candidate = Path(raw_str)

        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            # Check if relative path starts with 'ISO/' or is directly under ISO/
            parts = candidate.parts
            if parts and parts[0].upper() == "ISO":
                resolved = (self.storage_manager.data_mount / candidate).resolve()
            else:
                resolved = (iso_dir / candidate).resolve()

        # Enforce boundary: must reside strictly inside iso_dir
        try:
            resolved.relative_to(iso_dir)
        except ValueError:
            raise PathTraversalError(
                f"Path traversal detected: '{raw_str}' resolves outside ISO directory '{iso_dir}'"
            )

        if not resolved.exists():
            raise FileNotFoundError(f"ISO file does not exist: {resolved}")

        if not resolved.is_file():
            raise ValueError(f"Target path is not a file: {resolved}")

        # Compute relative path formatted for JSON config: 'ISO/subdir/file.iso'
        rel_from_data = resolved.relative_to(self.storage_manager.data_mount.resolve())
        canonical_rel_str = str(rel_from_data).replace("\\", "/")

        return resolved, canonical_rel_str

    def _generate_unique_id(self, base_name: str, existing_ids: set[str]) -> str:
        """Generate a valid, unique environment ID matching ^[a-zA-Z0-9_-]+$."""
        # Convert spaces and punctuation to underscores
        cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", base_name).strip("_-").lower()
        if not cleaned:
            cleaned = "env"

        # Truncate if excessively long
        cleaned = cleaned[:32]

        candidate = cleaned
        counter = 1
        while candidate in existing_ids:
            candidate = f"{cleaned}_{counter}"
            counter += 1

        return sanitize_id(candidate)

    # --------------------------------------------------------------------------
    # Configuration Load / Save / Rollback Helper
    # --------------------------------------------------------------------------

    def _load_config(self) -> Dict[str, Any]:
        """Load and return current authoritative configuration."""
        return self.storage_manager.load_configuration()

    def _save_and_sync_with_rollback(
        self,
        new_config: Dict[str, Any],
        metadata_to_cleanup_on_error: Optional[str] = None,
    ) -> None:
        """Validate, atomically save new config, and synchronize GRUB boot menu.

        If menu generation fails, automatically rolls back mventx.json to the prior state.
        """
        # 1. Structural and security pre-validation
        generator = GrubMenuGenerator()
        generator.validate_config(new_config)

        # 2. Capture prior configuration for rollback
        prior_config = self._load_config()

        # 3. Atomically write updated configuration (creates timestamped backup)
        self.storage_manager.write_configuration_atomically(new_config, create_backup=True)

        # 4. Synchronize GRUB boot menu
        try:
            self.storage_manager.sync_boot_menu(generator=generator)
        except Exception as exc:
            # ROLLBACK: Revert configuration to prior state
            self.storage_manager.write_configuration_atomically(prior_config, create_backup=False)

            # Cleanup newly created metadata if applicable
            if metadata_to_cleanup_on_error:
                ISOMetadataExtractor.delete_metadata(self.metadata_directory, metadata_to_cleanup_on_error)

            # Attempt to re-sync menu back to prior state
            try:
                self.storage_manager.sync_boot_menu(generator=generator)
            except Exception:
                pass

            raise MenuSyncRollbackError(
                f"Boot menu generation failed; configuration changes rolled back. Error: {exc}"
            ) from exc

    # --------------------------------------------------------------------------
    # Manual Registration (Never Automatic)
    # --------------------------------------------------------------------------

    def register_iso(
        self,
        iso_path: Union[Path, str],
        display_name: str,
        boot_method: str = "linux_generic_loopback",
        custom_args: Optional[str] = None,
        description: Optional[str] = None,
        enabled: bool = True,
        calculate_hash: bool = False,
        hash_algorithm: str = "sha256",
        custom_id: Optional[str] = None,
        recipe: Optional[str] = None,
        kernel_path: Optional[str] = None,
        initrd_path: Optional[str] = None,
    ) -> EnvironmentRecord:
        """Manually register an existing ISO image into the environment store.

        Architectural Rule: Never called via automatic directory scanning. The user must
        explicitly specify the ISO file to register.

        Raises:
            PathTraversalError: If iso_path resolves outside MVX_DATA/ISO/.
            FileNotFoundError: If the ISO file does not exist.
            ISOValidationError: If the file is not a valid ISO image.
            DuplicateRegistrationError: If this ISO file is already registered.
            ConfigValidationError / ConfigSecurityError: On invalid input.
        """
        # 1. Validate display name
        safe_name = validate_display_name(display_name)

        # 2. Resolve path and ensure directory confinement
        abs_iso_path, canonical_rel_path = self.resolve_iso_path(iso_path)

        # 3. Perform ISO validation (extension, size, ISO9660/UDF signatures)
        val_result = ISOValidator.validate_iso(abs_iso_path)
        if not val_result.is_valid:
            error_msg = "; ".join(val_result.errors)
            raise RegistrationError(f"ISO validation failed for '{abs_iso_path.name}': {error_msg}")

        # 4. Load existing configuration and check for duplicates
        config = self._load_config()
        environments = config.get("environments", [])
        existing_ids = {env["id"] for env in environments}

        # Check duplicate registration: compare canonical relative paths and real files
        for env in environments:
            if env.get("type") == "iso":
                existing_iso_path = env.get("iso_path")
                if existing_iso_path:
                    # Compare canonical relative paths
                    norm_existing = existing_iso_path.replace("\\", "/")
                    norm_candidate = canonical_rel_path.replace("\\", "/")
                    if norm_existing == norm_candidate:
                        raise DuplicateRegistrationError(
                            f"ISO '{canonical_rel_path}' is already registered in environment "
                            f"'{env['id']}' ('{env['display_name']}')."
                        )
                    # Also compare resolved real paths if file exists
                    try:
                        resolved_existing = (self.storage_manager.data_mount / existing_iso_path).resolve()
                        if resolved_existing == abs_iso_path:
                            raise DuplicateRegistrationError(
                                f"ISO file '{abs_iso_path.name}' is already registered in environment '{env['id']}'."
                            )
                    except OSError:
                        pass

        # 5. Generate or validate unique environment ID
        if custom_id:
            env_id = sanitize_id(custom_id)
            if env_id in existing_ids:
                raise DuplicateRegistrationError(f"Environment ID '{env_id}' is already registered.")
        else:
            stem_hint = abs_iso_path.stem
            env_id = self._generate_unique_id(stem_hint, existing_ids)

        # 6. Extract and persist ISO metadata
        file_size = abs_iso_path.stat().st_size
        meta = ISOMetadataExtractor.extract_metadata(
            abs_iso_path,
            calculate_hash=calculate_hash,
            hash_algorithm=hash_algorithm,
        )
        if description and not meta.publisher_identifier:
            meta.publisher_identifier = description
        ISOMetadataExtractor.save_metadata(self.metadata_directory, env_id, meta)

        # 7. Build EnvironmentRecord
        if not recipe:
            recipe = meta.detected_recipe
        if not recipe:
            from core.boot_config.recipes import detect_recipe_from_hints
            recipe = detect_recipe_from_hints(abs_iso_path.name, meta.volume_identifier).recipe_id

        # Use discovered kernel and initrd paths if user did not provide explicit overrides
        final_kernel_path = kernel_path or meta.discovered_kernel_path
        final_initrd_path = initrd_path or meta.discovered_initrd_path

        next_order = len(environments) + 1
        record = EnvironmentRecord(
            id=env_id,
            display_name=safe_name,
            type="iso",
            boot_method=boot_method,
            enabled=enabled,
            order=next_order,
            iso_path=canonical_rel_path,
            description=description,
            custom_args=custom_args,
            recipe=recipe,
            kernel_path=final_kernel_path,
            initrd_path=final_initrd_path,
            checksum=meta.checksum,
            file_size_bytes=file_size,
            registered_at=meta.registered_at,
        )

        # 8. Append to environments list and update configuration
        environments.append(record.to_dict())
        config["environments"] = environments

        # 9. Save and sync with automatic rollback on failure
        self._save_and_sync_with_rollback(config, metadata_to_cleanup_on_error=env_id)

        return record

    def register_windows_media(
        self,
        display_name: str,
        bootloader_path: str = "/efi/boot/bootx64.efi",
        staged_subfolder: Optional[str] = None,
        iso_path: Optional[Union[Path, str]] = None,
        description: Optional[str] = None,
        enabled: bool = True,
    ) -> EnvironmentRecord:
        """Register a Windows installation media environment."""
        display_name = display_name.strip()
        if not display_name:
            raise ValueError("display_name cannot be empty.")

        config = self._load_config()
        environments = config.get("environments", [])

        existing_ids = {e.get("id") for e in environments if isinstance(e, dict)}
        env_id = self._generate_unique_id(display_name, existing_ids)
        order = len(environments) + 1

        rel_iso_path = None
        if iso_path:
            p = Path(iso_path)
            if p.is_absolute():
                try:
                    rel_iso_path = p.relative_to(self.storage_manager.data_mount).as_posix()
                except ValueError:
                    rel_iso_path = p.name
            else:
                rel_iso_path = p.as_posix()
            if not rel_iso_path.startswith("/"):
                rel_iso_path = f"/{rel_iso_path}"

        record = EnvironmentRecord(
            id=env_id,
            display_name=display_name,
            type="windows",
            boot_method="windows_extracted_media",
            enabled=enabled,
            order=order,
            iso_path=rel_iso_path,
            description=description,
            staged_subfolder=staged_subfolder,
            bootloader_path=bootloader_path,
        )

        environments.append(record.to_dict())
        config["environments"] = environments
        self._save_and_sync_with_rollback(config)

        return record

    # --------------------------------------------------------------------------
    # Environment Management (List, Rename, Enable, Disable, Reorder, Remove)
    # --------------------------------------------------------------------------

    def list_environments(self, include_missing_status: bool = False) -> List[EnvironmentRecord]:
        """Return all registered environments ordered by their display sequence."""
        config = self._load_config()
        environments_raw = config.get("environments", [])

        # Sort by order field
        sorted_raw = sorted(environments_raw, key=lambda e: e.get("order", 0))

        records: List[EnvironmentRecord] = []
        for raw in sorted_raw:
            rec = EnvironmentRecord.from_dict(raw)
            # Try loading cached metadata
            cached_meta = ISOMetadataExtractor.load_metadata(self.metadata_directory, rec.id)
            if cached_meta:
                rec.file_size_bytes = cached_meta.file_size_bytes
                rec.registered_at = cached_meta.registered_at
            elif rec.iso_path:
                iso_file = self.storage_manager.data_mount / rec.iso_path
                if iso_file.is_file():
                    try:
                        rec.file_size_bytes = iso_file.stat().st_size
                    except OSError:
                        pass
            records.append(rec)

        return records

    def get_environment(self, env_id: str) -> Optional[EnvironmentRecord]:
        """Lookup a registered environment by its ID."""
        for env in self.list_environments():
            if env.id == env_id:
                return env
        return None

    def rename_environment(self, env_id: str, new_display_name: str) -> EnvironmentRecord:
        """Rename an existing environment's display name."""
        safe_name = validate_display_name(new_display_name)
        config = self._load_config()
        environments = config.get("environments", [])

        target_env = None
        for env in environments:
            if env.get("id") == env_id:
                target_env = env
                break

        if not target_env:
            raise EnvironmentNotFoundError(f"Environment '{env_id}' not found.")

        target_env["display_name"] = safe_name
        self._save_and_sync_with_rollback(config)

        updated = self.get_environment(env_id)
        assert updated is not None
        return updated

    def update_environment(
        self,
        env_id: str,
        display_name: Optional[str] = None,
        recipe: Optional[str] = None,
        kernel_path: Optional[str] = None,
        initrd_path: Optional[str] = None,
        custom_args: Optional[str] = None,
        description: Optional[str] = None,
        boot_method: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> EnvironmentRecord:
        """Update configurable fields of an existing registered environment."""
        config = self._load_config()
        environments = config.get("environments", [])

        target_env = None
        for env in environments:
            if env.get("id") == env_id:
                target_env = env
                break

        if not target_env:
            raise EnvironmentNotFoundError(f"Environment '{env_id}' not found.")

        if display_name is not None:
            target_env["display_name"] = validate_display_name(display_name)
        if recipe is not None:
            target_env["recipe"] = recipe.strip()
        if kernel_path is not None:
            target_env["kernel_path"] = kernel_path.strip()
        if initrd_path is not None:
            target_env["initrd_path"] = initrd_path.strip()
        if custom_args is not None:
            target_env["custom_args"] = custom_args.strip()
        if description is not None:
            target_env["description"] = description.strip()
        if boot_method is not None:
            target_env["boot_method"] = boot_method.strip()
        if enabled is not None:
            target_env["enabled"] = bool(enabled)

        self._save_and_sync_with_rollback(config)

        updated = self.get_environment(env_id)
        assert updated is not None
        return updated

    def enable_environment(self, env_id: str) -> EnvironmentRecord:
        """Enable an environment so it appears in the active boot menu."""
        return self._set_environment_enabled(env_id, True)

    def disable_environment(self, env_id: str) -> EnvironmentRecord:
        """Disable an environment so it is excluded from the active boot menu."""
        return self._set_environment_enabled(env_id, False)

    def _set_environment_enabled(self, env_id: str, enabled: bool) -> EnvironmentRecord:
        config = self._load_config()
        environments = config.get("environments", [])

        target_env = None
        for env in environments:
            if env.get("id") == env_id:
                target_env = env
                break

        if not target_env:
            raise EnvironmentNotFoundError(f"Environment '{env_id}' not found.")

        target_env["enabled"] = enabled
        self._save_and_sync_with_rollback(config)

        updated = self.get_environment(env_id)
        assert updated is not None
        return updated

    def reorder_environments(self, ordered_ids: List[str]) -> List[EnvironmentRecord]:
        """Update display order for all registered environments according to ordered_ids."""
        config = self._load_config()
        environments = config.get("environments", [])

        current_ids = [env["id"] for env in environments]
        if set(ordered_ids) != set(current_ids) or len(ordered_ids) != len(current_ids):
            raise ValueError(
                f"ordered_ids must contain all currently registered environment IDs exactly once. "
                f"Expected: {current_ids}, Got: {ordered_ids}"
            )

        # Map ID to dict entry
        env_map = {env["id"]: env for env in environments}

        # Re-assign 1-based order
        new_list = []
        for index, env_id in enumerate(ordered_ids, start=1):
            entry = env_map[env_id]
            entry["order"] = index
            new_list.append(entry)

        config["environments"] = new_list
        self._save_and_sync_with_rollback(config)

        return self.list_environments()

    def remove_environment(
        self,
        env_id: str,
        delete_file: bool = False,
        confirm_deletion: bool = False,
    ) -> bool:
        """Remove an environment from the registry.

        Architectural Invariant: Removing a registration must NEVER automatically delete
        the underlying ISO file. Only when delete_file=True AND confirm_deletion=True will
        the file be removed from disk.
        """
        config = self._load_config()
        environments = config.get("environments", [])

        target_idx = None
        target_env = None
        for idx, env in enumerate(environments):
            if env.get("id") == env_id:
                target_idx = idx
                target_env = env
                break

        if target_idx is None or target_env is None:
            raise EnvironmentNotFoundError(f"Environment '{env_id}' not found.")

        # If file deletion is requested, ensure explicit confirmation
        iso_file_to_delete: Optional[Path] = None
        if delete_file:
            if not confirm_deletion:
                raise ValueError(
                    "Deleting the underlying ISO file requires explicit confirmation "
                    "(confirm_deletion=True). Without confirmation, files cannot be deleted."
                )
            rel_path = target_env.get("iso_path")
            if rel_path:
                candidate = (self.storage_manager.data_mount / rel_path).resolve()
                if candidate.is_file() and candidate.is_relative_to(self.iso_directory.resolve()):
                    iso_file_to_delete = candidate

        # Remove environment and re-sequence orders
        environments.pop(target_idx)
        for idx, env in enumerate(environments, start=1):
            env["order"] = idx

        config["environments"] = environments

        # Save configuration and synchronize boot menu
        self._save_and_sync_with_rollback(config)

        # Remove metadata cache file
        ISOMetadataExtractor.delete_metadata(self.metadata_directory, env_id)

        # Safely delete underlying file only after configuration commit succeeds
        if iso_file_to_delete and iso_file_to_delete.exists():
            try:
                iso_file_to_delete.unlink()
            except OSError as exc:
                # File deletion error logged, but registration is already removed
                pass

        return True

    # --------------------------------------------------------------------------
    # Storage Capacity & Missing ISO Inspection
    # --------------------------------------------------------------------------

    def detect_missing_isos(self) -> List[Tuple[str, str]]:
        """Detect registered ISO environments whose files no longer exist on disk.

        Returns:
            List of tuples: (environment_id, configured_iso_path)
        """
        missing: List[Tuple[str, str]] = []
        for env in self.list_environments():
            if env.type == "iso" and env.iso_path:
                iso_file = self.storage_manager.data_mount / env.iso_path
                if not iso_file.is_file():
                    missing.append((env.id, env.iso_path))
        return missing

    def get_storage_metrics(self) -> StorageMetrics:
        """Compute storage metrics for the MVX_DATA partition.

        Invariant: Never confuses registered ISO size with total partition usage.
        """
        # Partition disk usage
        try:
            usage = shutil.disk_usage(self.storage_manager.data_mount)
            total = usage.total
            used = usage.used
            avail = usage.free
        except OSError:
            total = 0
            used = 0
            avail = 0

        environments = self.list_environments()
        iso_count = 0
        total_registered_iso_bytes = 0
        missing_count = 0

        for env in environments:
            if env.type == "iso" and env.iso_path:
                iso_count += 1
                iso_file = self.storage_manager.data_mount / env.iso_path
                if iso_file.is_file():
                    try:
                        total_registered_iso_bytes += iso_file.stat().st_size
                    except OSError:
                        missing_count += 1
                else:
                    missing_count += 1

        return StorageMetrics(
            total_bytes=total,
            used_bytes=used,
            available_bytes=avail,
            registered_iso_count=iso_count,
            total_registered_iso_bytes=total_registered_iso_bytes,
            missing_iso_count=missing_count,
        )

    def list_unregistered_isos(self) -> List[Path]:
        """List ISO files in MVX_DATA/ISO/ that have not yet been registered.

        Architectural Invariant: Directory scanning is strictly limited to candidate
        file selection in the user interface. It is NEVER used for automatic registration.
        """
        if not self.iso_directory.is_dir():
            return []

        # Collect all registered canonical relative paths
        registered_paths = set()
        for env in self.list_environments():
            if env.type == "iso" and env.iso_path:
                registered_paths.add(env.iso_path.replace("\\", "/"))

        unregistered: List[Path] = []
        for root, _, files in os.walk(self.iso_directory):
            for file in files:
                if file.lower().endswith(".iso"):
                    full_p = Path(root) / file
                    try:
                        rel = full_p.relative_to(self.storage_manager.data_mount.resolve())
                        rel_str = str(rel).replace("\\", "/")
                        if rel_str not in registered_paths:
                            unregistered.append(full_p)
                    except ValueError:
                        continue

        return sorted(unregistered)
