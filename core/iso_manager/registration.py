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
from core.boot_config.recipes import REGISTERED_RECIPES
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
    overlay_path: Optional[str] = None
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
        if self.overlay_path is not None:
            data["overlay_path"] = self.overlay_path
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

    @property
    def sha256(self) -> Optional[str]:
        """Return the SHA-256 hex digest if calculated and stored in checksum."""
        if isinstance(self.checksum, dict):
            return self.checksum.get("value") or self.checksum.get("sha256")
        return None

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
            overlay_path=data.get("overlay_path"),
            target_path=data.get("target_path"),
            target_hint=data.get("target_hint"),
            checksum=data.get("checksum"),
            staged_subfolder=data.get("staged_subfolder"),
            bootloader_path=data.get("bootloader_path"),
        )


@dataclass
class OverlayStatus:
    """Represents the state of an environment's dynamic initramfs overlay."""
    requires_overlay: bool
    overlay_exists: bool
    needs_regeneration: bool
    state: str  # "not_required", "ready", "missing", "needs_regeneration"
    status_label: str
    detail_message: str
    overlay_file_path: Optional[Path] = None
    overlay_size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.overlay_file_path is not None:
            data["overlay_file_path"] = str(self.overlay_file_path)
        return data


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
        overlay_to_cleanup_on_error: Optional[str] = None,
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

            # Cleanup newly created overlay if applicable
            if overlay_to_cleanup_on_error:
                try:
                    overlay_file = self.storage_manager.overlays_directory_path / f"{overlay_to_cleanup_on_error}.cpio"
                    if overlay_file.is_file():
                        overlay_file.unlink()
                except OSError:
                    pass

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
        boot_method: Optional[str] = None,
        custom_args: Optional[str] = None,
        description: Optional[str] = None,
        enabled: bool = True,
        calculate_hash: bool = False,
        hash_algorithm: str = "sha256",
        custom_id: Optional[str] = None,
        recipe: Optional[str] = None,
        kernel_path: Optional[str] = None,
        initrd_path: Optional[str] = None,
        generate_overlay: Optional[bool] = None,
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
        chosen_boot_method = boot_method
        if not chosen_boot_method:
            chosen_boot_method = meta.suggested_boot_method or meta.detected_recipe or "linux_generic_loopback"

        if not recipe:
            if chosen_boot_method in REGISTERED_RECIPES:
                recipe = REGISTERED_RECIPES[chosen_boot_method].recipe_id
            elif meta.detected_recipe:
                recipe = meta.detected_recipe
            else:
                from core.boot_config.recipes import detect_recipe_from_hints
                recipe = detect_recipe_from_hints(abs_iso_path.name, meta.volume_identifier).recipe_id

        # Use discovered kernel and initrd paths if user did not provide explicit overrides
        final_kernel_path = kernel_path or meta.discovered_kernel_path
        final_initrd_path = initrd_path or meta.discovered_initrd_path

        # 8. Check if dynamic initramfs overlay is needed (e.g. Ubuntu / Casper boots from exFAT MVX_DATA)
        overlay_rel_path: Optional[str] = None
        if recipe == "casper" or chosen_boot_method == "casper":
            from core.iso_manager.overlay_generator import InitramfsOverlayGenerator
            should_generate = False
            if generate_overlay is True:
                should_generate = True
            elif generate_overlay is None:
                should_generate = InitramfsOverlayGenerator.can_generate_overlay(abs_iso_path)

            if should_generate:
                try:
                    overlays_dir = self.storage_manager.overlays_directory_path
                    overlays_dir.mkdir(parents=True, exist_ok=True)
                    overlay_filename = f"{env_id}.cpio"
                    target_overlay_file = overlays_dir / overlay_filename

                    res = InitramfsOverlayGenerator.generate_exfat_overlay(
                        iso_path=abs_iso_path,
                        output_cpio_path=target_overlay_file,
                        target_kernel_version=None,
                    )
                    if res.success:
                        overlay_rel_path = f"boot/mventx/overlays/{overlay_filename}"
                except Exception as exc:
                    # Clean up partial overlay file
                    try:
                        partial = self.storage_manager.overlays_directory_path / f"{env_id}.cpio"
                        if partial.is_file():
                            partial.unlink()
                    except OSError:
                        pass
                    raise RegistrationError(
                        f"Dynamic initramfs overlay generation failed for '{abs_iso_path.name}': {exc}"
                    ) from exc

        next_order = len(environments) + 1
        record = EnvironmentRecord(
            id=env_id,
            display_name=safe_name,
            type="iso",
            boot_method=chosen_boot_method,
            enabled=enabled,
            order=next_order,
            iso_path=canonical_rel_path,
            description=description,
            custom_args=custom_args,
            recipe=recipe,
            kernel_path=final_kernel_path,
            initrd_path=final_initrd_path,
            overlay_path=overlay_rel_path,
            checksum=meta.checksum,
            file_size_bytes=file_size,
            registered_at=meta.registered_at,
        )

        # 9. Append to environments list and update configuration
        environments.append(record.to_dict())
        config["environments"] = environments

        # 10. Save and sync with automatic rollback on failure
        self._save_and_sync_with_rollback(
            config,
            metadata_to_cleanup_on_error=env_id,
            overlay_to_cleanup_on_error=env_id if overlay_rel_path else None,
        )

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
        regenerate_overlay: bool = False,
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

        # Handle overlay updates or regeneration if needed
        overlay_cleanup_id: Optional[str] = None
        new_recipe = target_env.get("recipe")
        new_method = target_env.get("boot_method")
        is_casper = (new_recipe == "casper" or new_method == "casper")

        if is_casper:
            if regenerate_overlay or not target_env.get("overlay_path"):
                rel_iso = target_env.get("iso_path")
                if rel_iso:
                    abs_iso = (self.storage_manager.data_mount / rel_iso.lstrip("/\\")).resolve()
                    from core.iso_manager.overlay_generator import InitramfsOverlayGenerator
                    should_gen = True if regenerate_overlay else InitramfsOverlayGenerator.can_generate_overlay(abs_iso)
                    if should_gen:
                        try:
                            overlays_dir = self.storage_manager.overlays_directory_path
                            if not overlays_dir:
                                raise RegistrationError("MVX_BOOT overlays directory is not available.")
                            overlays_dir.mkdir(parents=True, exist_ok=True)
                            overlay_file = overlays_dir / f"{env_id}.cpio"
                            res = InitramfsOverlayGenerator.generate_exfat_overlay(
                                iso_path=abs_iso,
                                output_cpio_path=overlay_file,
                            )
                            if res.success:
                                target_env["overlay_path"] = f"boot/mventx/overlays/{env_id}.cpio"
                                overlay_cleanup_id = env_id
                        except Exception as exc:
                            raise RegistrationError(
                                f"Dynamic initramfs overlay generation failed for '{abs_iso.name}': {exc}"
                            ) from exc
        else:
            if target_env.get("overlay_path"):
                try:
                    if self.storage_manager.boot_mount:
                        old_path = (self.storage_manager.boot_mount / target_env["overlay_path"].lstrip("/\\")).resolve()
                        if old_path.is_file():
                            old_path.unlink()
                except OSError:
                    pass
                target_env["overlay_path"] = None

        self._save_and_sync_with_rollback(config, overlay_to_cleanup_on_error=overlay_cleanup_id)

        updated = self.get_environment(env_id)
        assert updated is not None
        return updated

    def get_overlay_status(self, env_or_id: Union[EnvironmentRecord, str]) -> OverlayStatus:
        """Inspect whether an environment requires an initramfs overlay, whether one exists, and whether it needs regeneration."""
        if isinstance(env_or_id, str):
            env = self.get_environment(env_or_id)
            if env is None:
                raise EnvironmentNotFoundError(f"Environment '{env_or_id}' not found.")
        else:
            env = env_or_id

        requires_overlay = bool(
            env.type == "iso" and (env.recipe == "casper" or env.boot_method == "casper")
        )
        if not requires_overlay:
            return OverlayStatus(
                requires_overlay=False,
                overlay_exists=False,
                needs_regeneration=False,
                state="not_required",
                status_label="Not Required",
                detail_message="This boot method does not require a supplementary initramfs overlay.",
            )

        if not self.storage_manager.boot_mount:
            return OverlayStatus(
                requires_overlay=True,
                overlay_exists=False,
                needs_regeneration=True,
                state="missing",
                status_label="Required • Missing (No Boot Mount)",
                detail_message="Requires initramfs overlay, but MVX_BOOT partition is not mounted.",
            )

        rel_overlay = env.overlay_path
        if not rel_overlay:
            return OverlayStatus(
                requires_overlay=True,
                overlay_exists=False,
                needs_regeneration=True,
                state="missing",
                status_label="Required • Missing (Needs Regen)",
                detail_message=(
                    "Requires supplementary exFAT initramfs overlay for Casper boot, "
                    "but no overlay is configured. Use 'Repair / Regenerate' to build it."
                ),
            )

        abs_overlay = (self.storage_manager.boot_mount / rel_overlay.lstrip("/\\")).resolve()
        if not abs_overlay.is_file():
            return OverlayStatus(
                requires_overlay=True,
                overlay_exists=False,
                needs_regeneration=True,
                state="missing",
                status_label="Required • Missing (Needs Regen)",
                detail_message=(
                    f"Requires supplementary exFAT initramfs overlay, but '{rel_overlay}' "
                    f"was not found on MVX_BOOT. Regeneration is required."
                ),
                overlay_file_path=abs_overlay,
            )

        try:
            st = abs_overlay.stat()
            size_bytes = st.st_size
            overlay_mtime = st.st_mtime
            with open(abs_overlay, "rb") as f:
                header_magic = f.read(6)
        except OSError as exc:
            return OverlayStatus(
                requires_overlay=True,
                overlay_exists=True,
                needs_regeneration=True,
                state="needs_regeneration",
                status_label="Required • Unreadable (Needs Regen)",
                detail_message=f"Overlay file exists on MVX_BOOT but could not be read: {exc}",
                overlay_file_path=abs_overlay,
            )

        if size_bytes < 512 or header_magic != b"070701":
            return OverlayStatus(
                requires_overlay=True,
                overlay_exists=True,
                needs_regeneration=True,
                state="needs_regeneration",
                status_label="Required • Corrupted (Needs Regen)",
                detail_message=(
                    f"Overlay file '{rel_overlay}' exists on MVX_BOOT ({size_bytes} B) "
                    f"but is not a valid newc CPIO archive. Regeneration is required."
                ),
                overlay_file_path=abs_overlay,
                overlay_size_bytes=size_bytes,
            )

        if env.iso_path:
            iso_candidate = (self.storage_manager.data_mount / env.iso_path.lstrip("/\\")).resolve()
            if iso_candidate.is_file():
                try:
                    iso_mtime = iso_candidate.stat().st_mtime
                    if iso_mtime > overlay_mtime + 2.0:
                        return OverlayStatus(
                            requires_overlay=True,
                            overlay_exists=True,
                            needs_regeneration=True,
                            state="needs_regeneration",
                            status_label="Required • Outdated (Needs Regen)",
                            detail_message=(
                                f"Overlay exists on MVX_BOOT ({rel_overlay}), but the source ISO "
                                f"was modified after the overlay was generated. Regeneration is required."
                            ),
                            overlay_file_path=abs_overlay,
                            overlay_size_bytes=size_bytes,
                        )
                except OSError:
                    pass

        menu_file = self.storage_manager.generated_menu_path
        if env.enabled and menu_file and menu_file.is_file():
            try:
                menu_text = menu_file.read_text(encoding="utf-8", errors="ignore")
                norm_rel = rel_overlay.replace("\\", "/").lstrip("/")
                if norm_rel not in menu_text:
                    return OverlayStatus(
                        requires_overlay=True,
                        overlay_exists=True,
                        needs_regeneration=True,
                        state="needs_regeneration",
                        status_label="Required • Menu Unsynced (Needs Regen)",
                        detail_message=(
                            f"Overlay file exists on MVX_BOOT ({rel_overlay}), but the GRUB menu "
                            f"does not currently reference it. Regeneration/repair is required."
                        ),
                        overlay_file_path=abs_overlay,
                        overlay_size_bytes=size_bytes,
                    )
            except OSError:
                pass

        return OverlayStatus(
            requires_overlay=True,
            overlay_exists=True,
            needs_regeneration=False,
            state="ready",
            status_label="Required • Ready",
            detail_message=(
                f"Dynamic exFAT initramfs overlay exists on MVX_BOOT ({rel_overlay}, "
                f"{size_bytes:,} bytes) and is synchronized with the GRUB boot menu."
            ),
            overlay_file_path=abs_overlay,
            overlay_size_bytes=size_bytes,
        )

    def regenerate_boot_support(
        self,
        env_id: str,
        progress_callback: Optional[Any] = None,
    ) -> EnvironmentRecord:
        """Regenerate or repair an environment's initramfs overlay and synchronize the GRUB boot menu atomically.

        Architectural Invariants:
        1. Never reformats or repartitions the USB drive.
        2. Strictly preserves the registered ISO file on MVX_DATA.
        3. Updates MVX_BOOT and mventx.json atomically with full rollback on error.
        """
        config = self._load_config()
        environments = config.get("environments", [])

        target_env = None
        for env in environments:
            if env.get("id") == env_id:
                target_env = env
                break

        if not target_env:
            raise EnvironmentNotFoundError(f"Environment '{env_id}' not found.")

        display_name = target_env.get("display_name", env_id)
        if progress_callback:
            progress_callback(10, f"Validating environment '{display_name}'...")

        rel_iso = target_env.get("iso_path")
        abs_iso: Optional[Path] = None
        if target_env.get("type") == "iso":
            if not rel_iso:
                raise RegistrationError(f"Environment '{display_name}' has no configured ISO path.")
            abs_iso = (self.storage_manager.data_mount / rel_iso.lstrip("/\\")).resolve()
            if not abs_iso.is_file():
                raise FileNotFoundError(f"Registered ISO file not found on MVX_DATA: {rel_iso}")

        is_casper = (
            target_env.get("recipe") == "casper" or target_env.get("boot_method") == "casper"
        )

        prior_overlay_bytes: Optional[bytes] = None
        target_overlay_file: Optional[Path] = None

        if is_casper and abs_iso:
            overlays_dir = self.storage_manager.overlays_directory_path
            if not overlays_dir:
                raise RegistrationError("MVX_BOOT partition is not mounted; cannot write initramfs overlay.")
            overlays_dir.mkdir(parents=True, exist_ok=True)
            overlay_filename = f"{env_id}.cpio"
            target_overlay_file = overlays_dir / overlay_filename

            if target_overlay_file.is_file():
                try:
                    prior_overlay_bytes = target_overlay_file.read_bytes()
                except OSError:
                    prior_overlay_bytes = None

            if progress_callback:
                progress_callback(35, f"Extracting kernel driver and building initramfs overlay for '{abs_iso.name}'...")

            try:
                from core.iso_manager.overlay_generator import InitramfsOverlayGenerator
                res = InitramfsOverlayGenerator.generate_exfat_overlay(
                    iso_path=abs_iso,
                    output_cpio_path=target_overlay_file,
                )
                if not res.success:
                    raise RegistrationError("; ".join(res.errors) or "Overlay generation reported failure.")
                target_env["overlay_path"] = f"boot/mventx/overlays/{overlay_filename}"
            except Exception as exc:
                # Restore prior overlay if it existed, or remove partial file
                if target_overlay_file:
                    try:
                        if prior_overlay_bytes is not None:
                            target_overlay_file.write_bytes(prior_overlay_bytes)
                        elif target_overlay_file.is_file():
                            target_overlay_file.unlink()
                    except OSError:
                        pass
                raise RegistrationError(
                    f"Failed to regenerate initramfs overlay for '{display_name}': {exc}"
                ) from exc

        if progress_callback:
            progress_callback(80, "Synchronizing configuration and GRUB boot menu on MVX_BOOT...")

        try:
            self._save_and_sync_with_rollback(
                config,
                overlay_to_cleanup_on_error=env_id if (is_casper and prior_overlay_bytes is None) else None,
            )
        except Exception:
            if target_overlay_file and prior_overlay_bytes is not None:
                try:
                    target_overlay_file.write_bytes(prior_overlay_bytes)
                except OSError:
                    pass
            raise

        if progress_callback:
            progress_callback(100, f"Boot support repaired and synchronized for '{display_name}'.")

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

        # Check for associated overlay file to remove on MVX_BOOT
        overlay_file_to_delete: Optional[Path] = None
        rel_overlay = target_env.get("overlay_path")
        if rel_overlay:
            candidate_overlay = (self.storage_manager.boot_mount / rel_overlay).resolve()
            if candidate_overlay.is_file():
                overlay_file_to_delete = candidate_overlay
        elif (self.storage_manager.overlays_directory_path / f"{env_id}.cpio").is_file():
            overlay_file_to_delete = self.storage_manager.overlays_directory_path / f"{env_id}.cpio"

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

        # Remove supplementary overlay file if one was generated
        if overlay_file_to_delete and overlay_file_to_delete.exists():
            try:
                overlay_file_to_delete.unlink()
            except OSError:
                pass

        # Safely delete underlying file only after configuration commit succeeds
        if iso_file_to_delete and iso_file_to_delete.exists():
            try:
                iso_file_to_delete.unlink()
            except OSError as exc:
                # File deletion error logged, but registration is already removed
                pass

        return True

    def clean_orphaned_overlays(self) -> int:
        """Scan MVX_BOOT overlays directory and remove any *.cpio files not referenced by any registered environment.

        Returns:
            Number of orphaned overlay files removed.
        """
        overlays_dir = self.storage_manager.overlays_directory_path
        if not overlays_dir.is_dir():
            return 0

        config = self._load_config()
        referenced_files = set()
        for env in config.get("environments", []):
            op = env.get("overlay_path")
            if op:
                referenced_files.add(Path(op).name)
            eid = env.get("id")
            if eid:
                referenced_files.add(f"{eid}.cpio")

        removed = 0
        for item in overlays_dir.glob("*.cpio"):
            if item.is_file() and item.name not in referenced_files:
                try:
                    item.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

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
