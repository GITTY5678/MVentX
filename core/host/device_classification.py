"""MVentX Device Identification and Classification Engine.

Implements rigorous multi-attribute identification of MVentX storage devices.
Distinguishes between valid MVentX devices, incomplete installations, corrupted
configurations, ordinary storage drives, and inaccessible devices.

Architectural Rule: Classification is read-only. It NEVER automatically mounts,
repairs, formats, or alters the device under inspection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.storage.device_info import DeviceInfo, PartitionInfo


class MVentXStatus(str, Enum):
    """Structured classification status for storage devices."""
    VALID_MVENTX = "valid_mventx"                      # Fully valid, compliant MVentX installation
    INCOMPLETE_INSTALLATION = "incomplete_installation"  # Missing partition, bootloader, or skeleton
    CORRUPTED_INSTALLATION = "corrupted_installation"    # Schema failure, bad JSON, or broken table
    ORDINARY_STORAGE = "ordinary_storage"              # Ordinary disk without MVentX markings
    INACCESSIBLE = "inaccessible"                      # Locked, permission denied, or unmounted


@dataclass
class MVentXClassification:
    """Comprehensive classification report for an inspected device."""
    status: MVentXStatus
    device_id: str
    is_valid: bool = False
    data_mount: Optional[Path] = None
    boot_mount: Optional[Path] = None
    data_partition: Optional[PartitionInfo] = None
    boot_partition: Optional[PartitionInfo] = None
    environment_count: int = 0
    config_version: str = ""
    menu_title: str = ""
    menu_timeout: int = 0
    diagnostics: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Human-readable status summary."""
        if self.status == MVentXStatus.VALID_MVENTX:
            return f"Valid MVentX Device ({self.environment_count} environments registered)"
        elif self.status == MVentXStatus.INCOMPLETE_INSTALLATION:
            return "Incomplete MVentX Installation"
        elif self.status == MVentXStatus.CORRUPTED_INSTALLATION:
            return "Corrupted MVentX Installation"
        elif self.status == MVentXStatus.ORDINARY_STORAGE:
            return "Standard Storage Device (Not MVentX)"
        elif self.status == MVentXStatus.INACCESSIBLE:
            return "Inaccessible Device (Unmounted or Permission Denied)"
        return "Unknown Device Status"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "device_id": self.device_id,
            "is_valid": self.is_valid,
            "data_mount": str(self.data_mount) if self.data_mount else None,
            "boot_mount": str(self.boot_mount) if self.boot_mount else None,
            "environment_count": self.environment_count,
            "config_version": self.config_version,
            "menu_title": self.menu_title,
            "menu_timeout": self.menu_timeout,
            "diagnostics": self.diagnostics,
        }


class DeviceClassifier:
    """Evaluates device architecture and filesystems to classify MVentX media."""

    # Standard Partition GUIDs
    ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
    BASIC_DATA_GUID = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"

    @classmethod
    def classify_device(
        cls,
        device: Optional[DeviceInfo] = None,
        data_mount_override: Optional[Path | str] = None,
        boot_mount_override: Optional[Path | str] = None,
    ) -> MVentXClassification:
        """Perform comprehensive, non-destructive classification of a device.

        Never alters the device. Returns an MVentXClassification model with detailed diagnostics.
        """
        if device is None:
            from core.storage.device_info import DeviceType
            device = DeviceInfo(
                device_id=str(data_mount_override or "virtual_device"),
                name="MVentX Staging Target",
                size_bytes=1024 * 1024 * 1024,
                device_type=DeviceType.VIRTUAL_DISK,
                is_removable=True,
            )

        diagnostics: List[str] = []

        # Find boot and data partitions
        boot_part: Optional[PartitionInfo] = None
        data_part: Optional[PartitionInfo] = None

        for p in device.partitions:
            p_label = (p.label or "").strip().upper()
            p_guid = (p.type_guid or "").strip().lower()

            # Boot partition match (MVX_BOOT label or ESP GUID)
            if p.is_mventx_boot or p_label == "MVX_BOOT" or p_guid == cls.ESP_GUID:
                if not boot_part:
                    boot_part = p
                elif p_label == "MVX_BOOT":
                    boot_part = p

            # Data partition match (MVX_DATA label or Basic Data GUID)
            if p.is_mventx_data or p_label == "MVX_DATA" or (p_guid == cls.BASIC_DATA_GUID and p_label == "MVX_DATA"):
                if not data_part:
                    data_part = p
                elif p_label == "MVX_DATA":
                    data_part = p

        # Check if this looks like MVentX at all
        has_any_mventx_mark = (
            boot_part is not None
            or data_part is not None
            or any("MVX" in (p.label or "").upper() for p in device.partitions)
        )

        if not has_any_mventx_mark:
            return MVentXClassification(
                status=MVentXStatus.ORDINARY_STORAGE,
                device_id=device.device_id,
                is_valid=False,
                diagnostics=["Device does not contain MVentX partition labels or signatures."],
            )

        # Check for missing partitions
        if not boot_part:
            diagnostics.append("Missing EFI boot partition (MVX_BOOT).")
        if not data_part:
            diagnostics.append("Missing data partition (MVX_DATA).")

        if not boot_part or not data_part:
            return MVentXClassification(
                status=MVentXStatus.INCOMPLETE_INSTALLATION,
                device_id=device.device_id,
                is_valid=False,
                boot_partition=boot_part,
                data_partition=data_part,
                diagnostics=diagnostics,
            )

        # Determine mount paths
        data_mount: Optional[Path] = None
        if data_mount_override:
            data_mount = Path(data_mount_override).resolve()
        elif data_part.mount_point:
            data_mount = Path(data_part.mount_point).resolve()

        boot_mount: Optional[Path] = None
        if boot_mount_override:
            boot_mount = Path(boot_mount_override).resolve()
        elif boot_part.mount_point:
            boot_mount = Path(boot_part.mount_point).resolve()

        # If partitions exist but data mount is not accessible
        if not data_mount or not data_mount.exists():
            diagnostics.append(
                f"Data partition ({data_part.name or 'Partition 2'}) is not currently mounted or accessible."
            )
            return MVentXClassification(
                status=MVentXStatus.INACCESSIBLE,
                device_id=device.device_id,
                is_valid=False,
                boot_partition=boot_part,
                data_partition=data_part,
                boot_mount=boot_mount,
                diagnostics=diagnostics,
            )

        # Inspect data partition filesystem structure
        iso_dir = data_mount / "ISO"
        cfg_dir = data_mount / "config"
        cfg_file = cfg_dir / "mventx.json"
        meta_dir = data_mount / "metadata"
        backups_dir = data_mount / "backups"

        missing_dirs = []
        if not iso_dir.is_dir():
            missing_dirs.append("ISO/")
        if not cfg_dir.is_dir():
            missing_dirs.append("config/")
        if not meta_dir.is_dir():
            missing_dirs.append("metadata/")
        if not backups_dir.is_dir():
            missing_dirs.append("backups/")

        if missing_dirs:
            diagnostics.append(f"Missing standard directories: {', '.join(missing_dirs)}")
            return MVentXClassification(
                status=MVentXStatus.INCOMPLETE_INSTALLATION,
                device_id=device.device_id,
                is_valid=False,
                data_mount=data_mount,
                boot_mount=boot_mount,
                boot_partition=boot_part,
                data_partition=data_part,
                diagnostics=diagnostics,
            )

        if not cfg_file.is_file():
            diagnostics.append("Missing authoritative configuration file: config/mventx.json")
            return MVentXClassification(
                status=MVentXStatus.INCOMPLETE_INSTALLATION,
                device_id=device.device_id,
                is_valid=False,
                data_mount=data_mount,
                boot_mount=boot_mount,
                boot_partition=boot_part,
                data_partition=data_part,
                diagnostics=diagnostics,
            )

        # Validate config/mventx.json syntax and schema
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as exc:
            diagnostics.append(f"Corrupted configuration JSON syntax in {cfg_file}: {exc}")
            return MVentXClassification(
                status=MVentXStatus.CORRUPTED_INSTALLATION,
                device_id=device.device_id,
                is_valid=False,
                data_mount=data_mount,
                boot_mount=boot_mount,
                boot_partition=boot_part,
                data_partition=data_part,
                diagnostics=diagnostics,
            )

        # Schema validation
        from core.config.validator import ConfigValidator
        is_schema_valid, schema_errors = ConfigValidator.validate_config(config_data)
        if not is_schema_valid:
            diagnostics.append(f"Configuration schema validation failed: {'; '.join(schema_errors)}")
            return MVentXClassification(
                status=MVentXStatus.CORRUPTED_INSTALLATION,
                device_id=device.device_id,
                is_valid=False,
                data_mount=data_mount,
                boot_mount=boot_mount,
                boot_partition=boot_part,
                data_partition=data_part,
                diagnostics=diagnostics,
            )

        # Extract configuration attributes
        env_list = config_data.get("environments", [])
        env_count = len(env_list) if isinstance(env_list, list) else 0
        cfg_ver = config_data.get("version", "")
        meta = config_data.get("meta", {})
        title = meta.get("title", "")
        timeout = meta.get("timeout", 10)

        # Check boot partition files if boot mount is accessible
        if boot_mount and boot_mount.exists():
            bootx64 = boot_mount / "EFI" / "BOOT" / "BOOTX64.EFI"
            grub_cfg = boot_mount / "boot" / "grub" / "grub.cfg"
            menu_cfg = boot_mount / "boot" / "grub" / "mventx_menu.cfg"
            if not bootx64.is_file():
                diagnostics.append("Missing EFI/BOOT/BOOTX64.EFI on boot partition.")
            if not grub_cfg.is_file():
                diagnostics.append("Missing boot/grub/grub.cfg on boot partition.")
            if not menu_cfg.is_file():
                diagnostics.append("Warning: Initial boot/grub/mventx_menu.cfg not found on boot partition.")

            if not bootx64.is_file() or not grub_cfg.is_file():
                return MVentXClassification(
                    status=MVentXStatus.INCOMPLETE_INSTALLATION,
                    device_id=device.device_id,
                    is_valid=False,
                    data_mount=data_mount,
                    boot_mount=boot_mount,
                    boot_partition=boot_part,
                    data_partition=data_part,
                    environment_count=env_count,
                    config_version=cfg_ver,
                    menu_title=title,
                    menu_timeout=timeout,
                    diagnostics=diagnostics,
                )

        # Everything is valid!
        return MVentXClassification(
            status=MVentXStatus.VALID_MVENTX,
            device_id=device.device_id,
            is_valid=True,
            data_mount=data_mount,
            boot_mount=boot_mount,
            boot_partition=boot_part,
            data_partition=data_part,
            environment_count=env_count,
            config_version=cfg_ver,
            menu_title=title,
            menu_timeout=timeout,
            diagnostics=diagnostics,
        )
