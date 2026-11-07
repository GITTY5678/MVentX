"""Multi-Attribute Diagnostic Report Generator for MVentX (Milestone 15).

Collects and formats comprehensive system diagnostics for troubleshooting:
- Host OS environment, kernel, architecture, and administrative privilege level
- Connected physical and virtual storage devices, partitions, and mount points
- MVentX partition architecture (FAT32 MVX_BOOT + exFAT MVX_DATA) and permissions
- Directory structure, configuration schema validity, and backup archives
- Environment library inventory with file existence, sizes, and integrity
- Actionable recommendations and troubleshooting steps
"""

from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass, field
import datetime
import json
import logging
import os
from pathlib import Path
import platform
import sys
from typing import Any, Dict, List, Optional

from core.config.validator import ConfigValidator
from core.host.device_classification import DeviceClassifier, MVentXClassification, MVentXStatus
from core.storage.device_info import DeviceInfo
from core.storage.device_manager import DeviceManager
from core.storage.storage_manager import StorageManager

logger = logging.getLogger(__name__)


def _is_host_admin() -> bool:
    """Check whether process has administrative / root privileges."""
    try:
        if sys.platform == "win32":
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        else:
            return os.geteuid() == 0
    except Exception:
        return False


def _fmt_bytes(num_bytes: Optional[int]) -> str:
    if num_bytes is None:
        return "Unknown"
    b = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(b) < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} PiB"


@dataclass
class DiagnosticReport:
    """Structured container for all MVentX diagnostic information."""

    timestamp: str
    host_info: Dict[str, Any] = field(default_factory=dict)
    target_info: Dict[str, Any] = field(default_factory=dict)
    partition_health: Dict[str, Any] = field(default_factory=dict)
    config_health: Dict[str, Any] = field(default_factory=dict)
    environments_inventory: List[Dict[str, Any]] = field(default_factory=list)
    unregistered_isos: List[str] = field(default_factory=list)
    backups_inventory: Dict[str, Any] = field(default_factory=dict)
    bootloader_files: Dict[str, Any] = field(default_factory=dict)
    issues_detected: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_markdown(self) -> str:
        """Render diagnostic report as human-readable Markdown for bug reports."""
        lines = [
            "# MVentX Diagnostic & Troubleshooting Report",
            f"**Generated:** {self.timestamp}",
            "",
            "## 1. Host System Environment",
            f"- **OS:** {self.host_info.get('os_platform')} ({self.host_info.get('os_release')})",
            f"- **OS Version:** {self.host_info.get('os_version')}",
            f"- **Architecture:** {self.host_info.get('architecture')}",
            f"- **Python Version:** {self.host_info.get('python_version')}",
            f"- **Elevated (Admin / Root):** {'YES' if self.host_info.get('is_admin') else 'NO'}",
            "",
            "## 2. Target Device & Classification",
            f"- **Device ID:** {self.target_info.get('device_id')}",
            f"- **Name / Model:** {self.target_info.get('name')}",
            f"- **Capacity:** {self.target_info.get('capacity_formatted')} ({self.target_info.get('capacity_bytes')} bytes)",
            f"- **Bus Type:** {self.target_info.get('bus_type')}",
            f"- **Removable Media:** {'YES' if self.target_info.get('is_removable') else 'NO'}",
            f"- **MVentX Classification:** `{self.target_info.get('classification_status')}`",
            "",
            "## 3. Partition Architecture & Health",
            f"- **MVX_BOOT (ESP):** Mount: `{self.partition_health.get('boot_mount')}`, Access: `{self.partition_health.get('boot_access')}`",
            f"- **MVX_DATA:** Mount: `{self.partition_health.get('data_mount')}`, Access: `{self.partition_health.get('data_access')}`",
            f"- **Required Directories:** {self.partition_health.get('directory_structure_status')}",
            "",
            "## 4. Bootloader & Configuration Status",
            f"- **Config Schema Valid:** {'YES' if self.config_health.get('is_valid') else 'NO'}",
            f"- **Config Version:** `{self.config_health.get('version')}`",
            f"- **Boot Menu Title:** \"{self.config_health.get('menu_title')}\"",
            f"- **GRUB Fallback (BOOTX64.EFI):** {'PRESENT' if self.bootloader_files.get('has_bootx64') else 'MISSING'}",
            f"- **Synchronized Menu (mventx_menu.cfg):** {'PRESENT' if self.bootloader_files.get('has_menu_cfg') else 'MISSING'}",
            "",
            "## 5. Registered Environments Inventory",
        ]

        if not self.environments_inventory:
            lines.append("*(No registered environments found)*")
        else:
            lines.append("| Order | Name | Method | File Status | Size | Checksum |")
            lines.append("| :---: | :--- | :---: | :---: | :---: | :---: |")
            for env in self.environments_inventory:
                f_status = "FOUND" if env.get("file_exists") else "MISSING"
                lines.append(
                    f"| #{env.get('order')} | {env.get('display_name')} | `{env.get('boot_method')}` | {f_status} | {env.get('size_formatted')} | `{env.get('sha256') or 'None'}` |"
                )

        if self.unregistered_isos:
            lines.extend([
                "",
                "### Unregistered ISOs in `MVX_DATA/ISO/`:",
                *[f"- `{iso}`" for iso in self.unregistered_isos],
            ])

        lines.extend([
            "",
            "## 6. Backups Archive Status",
            f"- **Total Backups:** {self.backups_inventory.get('total_count', 0)}",
            f"- **Intact Backups:** {self.backups_inventory.get('valid_count', 0)}",
            f"- **Corrupt Backups:** {self.backups_inventory.get('corrupt_count', 0)}",
            "",
            "## 7. Diagnostic Findings & Recommendations",
        ])

        if self.issues_detected:
            lines.append("### Issues Detected:")
            for issue in self.issues_detected:
                lines.append(f"- ⚠️ {issue}")
            lines.append("")
            lines.append("### Recommended Actions:")
            for rec in self.recommendations:
                lines.append(f"- 💡 {rec}")
        else:
            lines.append("✅ **All MVentX health checks passed. Storage partition and bootloader are healthy.**")

        return "\n".join(lines)


class DiagnosticReportGenerator:
    """Collects and produces diagnostic reports."""

    def __init__(
        self,
        data_mount: Path | str,
        boot_mount: Optional[Path | str] = None,
        device_manager: Optional[DeviceManager] = None,
    ) -> None:
        self.data_mount = Path(data_mount).resolve()
        self.boot_mount = Path(boot_mount).resolve() if boot_mount else None
        self.device_manager = device_manager or DeviceManager(allow_physical_formatting=False)
        self.storage_manager = StorageManager(data_mount=self.data_mount, boot_mount=self.boot_mount)

    def generate_report(self) -> DiagnosticReport:
        """Execute diagnostic survey and compile report."""
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        issues: List[str] = []
        recommendations: List[str] = []

        # 1. Host Info
        is_admin = _is_host_admin()
        host_info = {
            "os_platform": sys.platform,
            "os_release": platform.release(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "processor": platform.processor(),
            "python_version": sys.version.split()[0],
            "is_admin": is_admin,
        }

        # 2. Target & Classification
        classification = DeviceClassifier.classify_device(
            data_mount_override=self.data_mount,
            boot_mount_override=self.boot_mount,
        )

        target_info = {
            "device_id": classification.device_id or str(self.data_mount),
            "name": self.data_mount.name,
            "capacity_bytes": classification.data_partition.size_bytes if classification.data_partition else 0,
            "capacity_formatted": _fmt_bytes(classification.data_partition.size_bytes) if classification.data_partition else "Unknown",
            "bus_type": "Virtual / Mount" if not self.boot_mount else "Removable USB",
            "is_removable": True,
            "classification_status": classification.status.value,
        }

        if classification.status != MVentXStatus.VALID_MVENTX:
            issues.append(f"Target classification is '{classification.status.value}'.")
            if classification.status == MVentXStatus.CORRUPTED_INSTALLATION:
                recommendations.append("Run ConfigRepairer or restore configuration from backups/.")
            elif classification.status == MVentXStatus.INCOMPLETE_INSTALLATION:
                recommendations.append("Reinitialize the USB drive using the USB Installer.")

        # 3. Partition Health
        p1_access = "Not Mounted"
        if self.boot_mount:
            p1_access = "Read-Write" if os.access(self.boot_mount, os.W_OK) else "Read-Only"
            if p1_access == "Read-Only":
                issues.append("MVX_BOOT partition is mounted Read-Only. Menu synchronization may fail.")
                recommendations.append("Check physical write-protect switch or repair filesystem dirty flag.")

        p2_access = "Read-Write" if os.access(self.data_mount, os.W_OK) else "Read-Only"
        if p2_access == "Read-Only":
            issues.append("MVX_DATA partition is mounted Read-Only. Cannot add or remove ISOs.")
            recommendations.append("Run chkdsk / fsck on the data partition to clear read-only lock.")

        struct_valid, struct_errs = self.storage_manager.validate_data_partition_structure()
        if not struct_valid:
            issues.extend(struct_errs)
            recommendations.append("Create missing directories (ISO/, config/, metadata/, backups/).")

        partition_health = {
            "boot_mount": str(self.boot_mount) if self.boot_mount else "None",
            "boot_access": p1_access,
            "data_mount": str(self.data_mount),
            "data_access": p2_access,
            "directory_structure_valid": struct_valid,
            "directory_structure_status": "All directories present" if struct_valid else f"Missing: {'; '.join(struct_errs)}",
        }

        # 4. Config Health
        config_data = {}
        config_valid = False
        config_errs: List[str] = []
        try:
            config_data = self.storage_manager.load_configuration(auto_repair=False)
            config_valid, config_errs = ConfigValidator.validate_config(config_data)
        except Exception as exc:
            config_errs.append(str(exc))
            issues.append(f"Configuration load failure: {exc}")
            recommendations.append("Repair mventx.json or restore from backups/.")

        if not config_valid and config_errs:
            issues.append(f"mventx.json schema violations: {'; '.join(config_errs[:3])}")

        config_health = {
            "is_valid": config_valid,
            "version": config_data.get("version", "Unknown"),
            "menu_title": config_data.get("meta", {}).get("title", "MVentX Multiboot System"),
            "menu_timeout": config_data.get("meta", {}).get("timeout", 10),
            "validation_errors": config_errs,
        }

        # 5. Environments Inventory
        envs_list = []
        raw_envs = config_data.get("environments", [])
        for env in raw_envs:
            iso_rel = env.get("iso_path")
            file_exists = False
            file_size = 0
            if iso_rel:
                full_iso_p = self.data_mount / iso_rel
                if full_iso_p.is_file():
                    file_exists = True
                    try:
                        file_size = full_iso_p.stat().st_size
                    except Exception:
                        pass
                else:
                    issues.append(f"Registered ISO file missing: {iso_rel}")
                    recommendations.append(f"Copy {Path(iso_rel).name} into MVX_DATA/ISO/ or remove registration.")

            envs_list.append({
                "id": env.get("id"),
                "display_name": env.get("display_name"),
                "boot_method": env.get("boot_method"),
                "enabled": env.get("enabled", True),
                "order": env.get("order"),
                "iso_path": iso_rel,
                "file_exists": file_exists,
                "size_bytes": file_size,
                "size_formatted": _fmt_bytes(file_size) if file_exists else "N/A",
                "sha256": env.get("sha256"),
            })

        # Unregistered ISOs
        unreg: List[str] = []
        if self.storage_manager.iso_directory_path.is_dir():
            for p in self.storage_manager.iso_directory_path.glob("*.iso"):
                rel = f"ISO/{p.name}"
                if not any(e.get("iso_path") == rel for e in raw_envs):
                    unreg.append(p.name)

        # 6. Backups Inventory
        backups_files = self.storage_manager.list_backups()
        valid_backups = 0
        corrupt_backups = 0
        for bf in backups_files:
            try:
                with open(bf, "r", encoding="utf-8") as f:
                    b_data = json.load(f)
                ok, _ = ConfigValidator.validate_config(b_data)
                if ok:
                    valid_backups += 1
                else:
                    corrupt_backups += 1
            except Exception:
                corrupt_backups += 1

        backups_inventory = {
            "total_count": len(backups_files),
            "valid_count": valid_backups,
            "corrupt_count": corrupt_backups,
            "newest_backup": backups_files[0].name if backups_files else None,
        }

        # 7. Bootloader Files on ESP
        has_bootx64 = False
        has_menu_cfg = False
        if self.boot_mount:
            has_bootx64 = (self.boot_mount / "EFI" / "BOOT" / "BOOTX64.EFI").is_file()
            has_menu_cfg = (self.boot_mount / "boot" / "grub" / "mventx_menu.cfg").is_file()
            if not has_bootx64:
                issues.append("Missing /EFI/BOOT/BOOTX64.EFI on MVX_BOOT. Drive will not boot on UEFI.")
                recommendations.append("Reinstall GRUB bootloader using the USB Installer.")
            if not has_menu_cfg:
                issues.append("Missing mventx_menu.cfg on MVX_BOOT.")
                recommendations.append("Synchronize boot menu via MVentX Manager.")

        bootloader_files = {
            "has_bootx64": has_bootx64,
            "has_menu_cfg": has_menu_cfg,
        }

        return DiagnosticReport(
            timestamp=timestamp,
            host_info=host_info,
            target_info=target_info,
            partition_health=partition_health,
            config_health=config_health,
            environments_inventory=envs_list,
            unregistered_isos=unreg,
            backups_inventory=backups_inventory,
            bootloader_files=bootloader_files,
            issues_detected=issues,
            recommendations=recommendations,
        )

