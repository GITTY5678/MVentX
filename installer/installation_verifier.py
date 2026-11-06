"""Installation Verification Subsystem for MVentX.

Performs comprehensive validation on installed virtual and physical storage media:
1. GPT partition table integrity and CRC32 verification.
2. 1 MiB boundary alignment and partition type GUIDs.
3. FAT32 and exFAT filesystem structures and volume labels.
4. GRUB 2.12 EFI bootloader binaries and dynamic modules.
5. Primary grub.cfg and dynamic mventx_menu.cfg fallback scripts.
6. Authoritative mventx.json configuration with 0 registered environments.
7. Directory skeletons (ISO/, config/, metadata/, backups/).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.boot_config.generator import GrubMenuGenerator
from core.storage.disk_validator import DiskValidator, DiskValidationError
from installer.partition_planner import (
    ALIGNMENT_SECTORS_1MB,
    GUID_BASIC_DATA,
    GUID_EFI_SYSTEM,
)


@dataclass
class CheckResult:
    """Outcome of an individual verification check."""
    name: str
    passed: bool
    message: str
    details: Optional[str] = None


@dataclass
class InstallationVerificationReport:
    """Structured report returned upon completion of verification."""
    is_valid: bool
    target_path: str
    checks: List[CheckResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total_count(self) -> int:
        return len(self.checks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "target_path": self.target_path,
            "passed_count": self.passed_count,
            "total_count": self.total_count,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "details": c.details,
                }
                for c in self.checks
            ],
            "errors": self.errors,
            "warnings": self.warnings,
        }


class InstallationVerifier:
    """Validates an installed MVentX disk or image file."""

    def __init__(self, target_disk_path: Path | str) -> None:
        self.target_path = Path(target_disk_path).resolve()

    def verify_virtual_disk(self) -> InstallationVerificationReport:
        """Execute all structural and content checks on a raw virtual disk image."""
        checks: List[CheckResult] = []
        errors: List[str] = []
        warnings: List[str] = []

        if not self.target_path.is_file():
            err = f"Target disk image does not exist: {self.target_path}"
            return InstallationVerificationReport(
                is_valid=False,
                target_path=str(self.target_path),
                checks=[CheckResult(name="File Existence", passed=False, message=err)],
                errors=[err],
            )

        checks.append(CheckResult(name="File Existence", passed=True, message="Virtual disk image file exists."))

        validator = DiskValidator(self.target_path)

        # 1. Protective MBR Check
        try:
            with open(self.target_path, "rb") as f:
                validator.validate_pmbr(f)
            checks.append(CheckResult(name="Protective MBR", passed=True, message="Valid Protective MBR with 0xEE partition."))
        except DiskValidationError as exc:
            checks.append(CheckResult(name="Protective MBR", passed=False, message=str(exc)))
            errors.append(f"Protective MBR validation failed: {exc}")

        # 2. GPT Headers, Checksums, and Partition Array
        partitions = []
        try:
            with open(self.target_path, "rb") as f:
                partitions = validator.validate_gpt(f)
            checks.append(CheckResult(name="GPT Headers & CRC32", passed=True, message="Primary and Backup GPT headers and CRC32 checksums verified."))
        except DiskValidationError as exc:
            checks.append(CheckResult(name="GPT Headers & CRC32", passed=False, message=str(exc)))
            errors.append(f"GPT header validation failed: {exc}")

        # 3. Partition Types & 1 MiB Alignment
        try:
            if len(partitions) < 2:
                raise DiskValidationError(f"Expected at least 2 partitions, found {len(partitions)}")
            esp = partitions[0]
            data = partitions[1]
            if str(esp.type_guid).upper() != GUID_EFI_SYSTEM:
                raise DiskValidationError(f"Partition 1 GUID is {esp.type_guid}, expected EFI System {GUID_EFI_SYSTEM}")
            if str(data.type_guid).upper() != GUID_BASIC_DATA:
                raise DiskValidationError(f"Partition 2 GUID is {data.type_guid}, expected Basic Data {GUID_BASIC_DATA}")
            if esp.start_lba % ALIGNMENT_SECTORS_1MB != 0 or data.start_lba % ALIGNMENT_SECTORS_1MB != 0:
                raise DiskValidationError("Partitions are not aligned to 1 MiB boundaries.")
            checks.append(CheckResult(name="Partition Table & Alignment", passed=True, message="Dual partition layout (ESP + Basic Data) verified with 1 MiB alignment."))
        except DiskValidationError as exc:
            checks.append(CheckResult(name="Partition Table & Alignment", passed=False, message=str(exc)))
            errors.append(f"Partition layout validation failed: {exc}")

        # 4. FAT32 Filesystem on Partition 1 (MVX_BOOT)
        try:
            if partitions:
                with open(self.target_path, "rb") as f:
                    validator.validate_fat32_mvx_boot(f, partitions[0])
                checks.append(CheckResult(name="FAT32 EFI Filesystem", passed=True, message="MVX_BOOT FAT32 VBR, FSInfo, and cluster geometry verified."))
        except DiskValidationError as exc:
            checks.append(CheckResult(name="FAT32 EFI Filesystem", passed=False, message=str(exc)))
            errors.append(f"FAT32 validation failed: {exc}")

        # 5. exFAT Filesystem on Partition 2 (MVX_DATA)
        try:
            if len(partitions) >= 2:
                with open(self.target_path, "rb") as f:
                    validator.validate_exfat_mvx_data(f, partitions[1])
                checks.append(CheckResult(name="exFAT Data Filesystem", passed=True, message="MVX_DATA exFAT VBR, Sector 11 rolling checksum, and root directory verified."))
        except DiskValidationError as exc:
            checks.append(CheckResult(name="exFAT Data Filesystem", passed=False, message=str(exc)))
            errors.append(f"exFAT validation failed: {exc}")

        # 6. Bootloader Binary Presence
        try:
            efi_content = validator.read_file_from_fat32("EFI/BOOT/BOOTX64.EFI")
            if len(efi_content) < 10000:
                raise DiskValidationError("BOOTX64.EFI is too small or truncated.")
            checks.append(CheckResult(name="GRUB EFI Binary", passed=True, message=f"BOOTX64.EFI present and readable ({len(efi_content):,} bytes)."))
        except DiskValidationError as exc:
            checks.append(CheckResult(name="GRUB EFI Binary", passed=False, message=str(exc)))
            errors.append(f"BOOTX64.EFI check failed: {exc}")

        # 7. Static GRUB Configuration Presence
        try:
            grub_cfg = validator.read_file_from_fat32("boot/grub/grub.cfg")
            text = grub_cfg.decode("utf-8", errors="replace")
            if "mventx_data_dev" not in text or "mventx_menu.cfg" not in text:
                raise DiskValidationError("grub.cfg missing mventx_data_dev search logic.")
            checks.append(CheckResult(name="Primary GRUB Configuration", passed=True, message="boot/grub/grub.cfg verified with data partition search logic."))
        except DiskValidationError as exc:
            checks.append(CheckResult(name="Primary GRUB Configuration", passed=False, message=str(exc)))
            errors.append(f"grub.cfg check failed: {exc}")

        # 8. Dynamic Fallback Menu
        try:
            menu_cfg = validator.read_file_from_fat32("boot/grub/mventx_menu.cfg")
            mtext = menu_cfg.decode("utf-8", errors="replace")
            if "mventx-empty" not in mtext and "No environments registered" not in mtext:
                raise DiskValidationError("mventx_menu.cfg does not contain valid empty fallback menu.")
            checks.append(CheckResult(name="Dynamic Fallback Menu", passed=True, message="mventx_menu.cfg contains valid fallback menu for unconfigured environments."))
        except DiskValidationError as exc:
            checks.append(CheckResult(name="Dynamic Fallback Menu", passed=False, message=str(exc)))
            errors.append(f"mventx_menu.cfg check failed: {exc}")

        # 9. Configuration File Structure (config/mventx.json)
        try:
            cfg_bytes = validator.read_file_from_exfat("config/mventx.json")
            cfg_json = json.loads(cfg_bytes.decode("utf-8"))
            if not isinstance(cfg_json.get("environments"), list):
                raise DiskValidationError("mventx.json missing environments array.")
            if len(cfg_json.get("environments")) != 0:
                raise DiskValidationError(f"Expected 0 environments, found {len(cfg_json['environments'])}")
            checks.append(CheckResult(name="Authoritative mventx.json", passed=True, message="config/mventx.json verified with 0 initial registered environments."))
        except DiskValidationError as exc:
            checks.append(CheckResult(name="Authoritative mventx.json", passed=False, message=str(exc)))
            errors.append(f"mventx.json check failed: {exc}")

        # 10. Required Directories
        try:
            # Check presence of directories in exFAT root
            dirs = validator.list_exfat_root_files()
            # dirs returns set of entry names
            lower_dirs = {d.lower() for d in dirs}
            for req in ("iso", "config", "metadata", "backups"):
                if req not in lower_dirs:
                    raise DiskValidationError(f"Required directory '{req}' not found in exFAT root: {dirs}")
            checks.append(CheckResult(name="Directory Structure", passed=True, message="All required directory skeletons (ISO/, config/, metadata/, backups/) verified."))
        except DiskValidationError as exc:
            checks.append(CheckResult(name="Directory Structure", passed=False, message=str(exc)))
            errors.append(f"Directory structure check failed: {exc}")

        is_valid = len(errors) == 0
        return InstallationVerificationReport(
            is_valid=is_valid,
            target_path=str(self.target_path),
            checks=checks,
            errors=errors,
            warnings=warnings,
        )

    def verify_mounted_partitions(
        self,
        boot_mount: Path | str,
        data_mount: Path | str,
    ) -> InstallationVerificationReport:
        """Verify an installation where partitions are already mounted."""
        b_mount = Path(boot_mount).resolve()
        d_mount = Path(data_mount).resolve()
        checks: List[CheckResult] = []
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Mount existence
        if not b_mount.is_dir():
            errors.append(f"Boot mount directory does not exist: {b_mount}")
        if not d_mount.is_dir():
            errors.append(f"Data mount directory does not exist: {d_mount}")

        if errors:
            return InstallationVerificationReport(
                is_valid=False,
                target_path=f"Boot: {b_mount}, Data: {d_mount}",
                checks=[CheckResult(name="Mount Verification", passed=False, message="; ".join(errors))],
                errors=errors,
            )

        # 2. Check BOOTX64.EFI
        efi_file = b_mount / "EFI" / "BOOT" / "BOOTX64.EFI"
        if efi_file.is_file() and efi_file.stat().st_size > 10000:
            checks.append(CheckResult(name="GRUB EFI Binary", passed=True, message="BOOTX64.EFI present on boot partition."))
        else:
            checks.append(CheckResult(name="GRUB EFI Binary", passed=False, message="BOOTX64.EFI missing or invalid."))
            errors.append("BOOTX64.EFI missing on boot partition.")

        # 3. Check grub.cfg and mventx_menu.cfg
        cfg_file = b_mount / "boot" / "grub" / "grub.cfg"
        menu_file = b_mount / "boot" / "grub" / "mventx_menu.cfg"
        if cfg_file.is_file() and menu_file.is_file():
            checks.append(CheckResult(name="GRUB Configurations", passed=True, message="grub.cfg and mventx_menu.cfg present."))
        else:
            checks.append(CheckResult(name="GRUB Configurations", passed=False, message="grub.cfg or mventx_menu.cfg missing."))
            errors.append("GRUB configuration files missing on boot partition.")

        # 4. Check modules
        mod_dir = b_mount / "boot" / "grub" / "x86_64-efi"
        if mod_dir.is_dir() and len(list(mod_dir.glob("*.mod"))) > 10:
            checks.append(CheckResult(name="GRUB Dynamic Modules", passed=True, message=f"GRUB modules verified ({len(list(mod_dir.glob('*.mod')))} modules)."))
        else:
            checks.append(CheckResult(name="GRUB Dynamic Modules", passed=False, message="GRUB modules missing in x86_64-efi/."))
            errors.append("GRUB modules missing in boot/grub/x86_64-efi/.")

        # 5. Check data partition structures
        for d in ("ISO", "config", "metadata", "backups"):
            target_d = d_mount / d
            if target_d.is_dir():
                checks.append(CheckResult(name=f"Directory {d}/", passed=True, message=f"{d}/ directory present."))
            else:
                checks.append(CheckResult(name=f"Directory {d}/", passed=False, message=f"{d}/ directory missing."))
                errors.append(f"{d}/ directory missing on data partition.")

        # 6. Check mventx.json
        json_file = d_mount / "config" / "mventx.json"
        if json_file.is_file():
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    cfg_data = json.load(f)
                if len(cfg_data.get("environments", [])) == 0:
                    checks.append(CheckResult(name="Authoritative mventx.json", passed=True, message="mventx.json contains 0 registered environments."))
                else:
                    warnings.append(f"mventx.json contains {len(cfg_data['environments'])} environments.")
            except Exception as exc:
                errors.append(f"Invalid mventx.json: {exc}")
                checks.append(CheckResult(name="Authoritative mventx.json", passed=False, message=str(exc)))
        else:
            errors.append("config/mventx.json missing on data partition.")
            checks.append(CheckResult(name="Authoritative mventx.json", passed=False, message="File missing."))

        is_valid = len(errors) == 0
        return InstallationVerificationReport(
            is_valid=is_valid,
            target_path=f"Boot: {b_mount}, Data: {d_mount}",
            checks=checks,
            errors=errors,
            warnings=warnings,
        )
