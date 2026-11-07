"""Installation Recovery and Failure Cleanup Subsystem for MVentX."""

from __future__ import annotations

from dataclasses import dataclass, field
import enum
import logging
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class USBInstallationState(enum.Enum):
    """Recognized hardware and filesystem preparation states for MVentX USBs."""
    ORIGINAL_FILESYSTEM = "ORIGINAL_FILESYSTEM"        # 1. Original USB with an existing filesystem
    EMPTY_GPT = "EMPTY_GPT"                            # 2. Empty GPT disk with zero partitions
    GPT_ESP_ONLY = "GPT_ESP_ONLY"                      # 3. GPT disk with only the EFI partition
    GPT_UNFORMATTED = "GPT_UNFORMATTED"                # 4. GPT disk with both partitions but missing filesystems
    GPT_FORMATTED_NO_GRUB = "GPT_FORMATTED_NO_GRUB"    # 5. GPT disk with both correctly formatted partitions
    PARTIAL_GRUB = "PARTIAL_GRUB"                      # 6. Partially deployed GRUB files
    COMPLETE_MVENTX = "COMPLETE_MVENTX"                # 7. Complete MVentX installation


@dataclass
class InstallationStateDiagnostic:
    """Comprehensive diagnostic of current USB storage state and recovery path."""
    state: USBInstallationState
    state_name: str
    description: str
    completed_stages: List[str]
    remaining_stages: List[str]
    can_recover_without_wipe: bool
    recommended_action: str
    requires_explicit_wipe_confirmation: bool
    partition_count: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RecoveryReport:
    """Summary of cleanup and recovery operations executed upon failure."""
    stage_at_failure: Optional[str] = None
    completed_stages: List[str] = field(default_factory=list)
    remaining_stages: List[str] = field(default_factory=list)
    recovered_dirs: List[str] = field(default_factory=list)
    recovered_files: List[str] = field(default_factory=list)
    target_disk_cleaned: bool = False
    errors: List[str] = field(default_factory=list)


class InstallationRecoveryService:
    """Safely cleans up temporary staging artifacts when installation fails or is cancelled."""

    def __init__(self) -> None:
        self._tracked_temp_dirs: List[Path] = []
        self._tracked_temp_files: List[Path] = []
        self._current_stage: Optional[str] = None
        self._completed_stages: List[str] = []
        self._all_stages: List[str] = []

    def set_pipeline_stages(self, stages: List[str]) -> None:
        """Register the planned pipeline stages."""
        self._all_stages = list(stages)
        self._completed_stages.clear()

    def track_stage(self, stage_number: int, stage_name: str) -> None:
        """Record the active installation pipeline stage."""
        stage_desc = f"Step {stage_number}: {stage_name}"
        if self._current_stage and self._current_stage not in self._completed_stages:
            self._completed_stages.append(self._current_stage)
        self._current_stage = stage_desc
        logger.debug("Active installation stage: %s", self._current_stage)

    def mark_stage_completed(self, stage_number: int, stage_name: str) -> None:
        """Explicitly record a stage as successfully completed."""
        stage_desc = f"Step {stage_number}: {stage_name}"
        if stage_desc not in self._completed_stages:
            self._completed_stages.append(stage_desc)

    @property
    def current_stage(self) -> Optional[str]:
        return self._current_stage

    @property
    def completed_stages(self) -> List[str]:
        return list(self._completed_stages)

    @property
    def remaining_stages(self) -> List[str]:
        if not self._all_stages:
            return []
        return [s for s in self._all_stages if s not in self._completed_stages and s != self._current_stage]

    def track_temp_dir(self, directory: Path | str) -> None:
        """Register a temporary directory for cleanup upon error."""
        p = Path(directory).resolve()
        if p not in self._tracked_temp_dirs:
            self._tracked_temp_dirs.append(p)

    def track_temp_file(self, file_path: Path | str) -> None:
        """Register a temporary file for deletion upon error."""
        p = Path(file_path).resolve()
        if p not in self._tracked_temp_files:
            self._tracked_temp_files.append(p)

    def cleanup_on_error(self, remove_target_file: Optional[Path | str] = None) -> RecoveryReport:
        """Clean up registered temporary paths and optionally the partially written target."""
        report = RecoveryReport(
            stage_at_failure=self._current_stage,
            completed_stages=list(self._completed_stages),
            remaining_stages=self.remaining_stages,
        )

        for temp_dir in list(self._tracked_temp_dirs):
            if temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    report.recovered_dirs.append(str(temp_dir))
                except OSError as exc:
                    err_msg = f"Failed to remove staging directory {temp_dir}: {exc}"
                    logger.warning(err_msg)
                    report.errors.append(err_msg)

        for temp_file in list(self._tracked_temp_files):
            if temp_file.exists():
                try:
                    temp_file.unlink()
                    report.recovered_files.append(str(temp_file))
                except OSError as exc:
                    err_msg = f"Failed to remove temporary file {temp_file}: {exc}"
                    report.errors.append(err_msg)

        if remove_target_file:
            t_path = Path(remove_target_file).resolve()
            if t_path.exists() and t_path.is_file():
                try:
                    t_path.unlink()
                    report.target_disk_cleaned = True
                except OSError as exc:
                    err_msg = f"Failed to clean target file {t_path}: {exc}"
                    report.errors.append(err_msg)

        self._tracked_temp_dirs.clear()
        self._tracked_temp_files.clear()
        return report

    def recover_interrupted_partitioning(self, target_disk_path: Path | str) -> bool:
        """Recover from interrupted partition creation by sanitizing corrupted GPT structures.

        For virtual disks: wipes LBA 0-33 (Protective MBR and Primary GPT) and removes file.
        """
        p = Path(target_disk_path).resolve()
        if not p.exists():
            return True

        try:
            if p.is_file():
                # Zero out first 34 sectors (17 KiB) to destroy corrupted GPT headers
                with open(p, "r+b") as f:
                    f.seek(0)
                    f.write(b"\x00" * (34 * 512))
                    f.flush()
                # Remove file safely
                p.unlink()
            return True
        except OSError as exc:
            logger.error("Failed to recover interrupted partitioning on %s: %s", p, exc)
            return False

    def recover_interrupted_filesystem(self, staging_path: Path | str) -> bool:
        """Recover from interrupted filesystem formatting by removing partial staging directory."""
        p = Path(staging_path).resolve()
        if p.exists() and p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            return True
        return False

    def recover_failed_bootloader(self, esp_staging_path: Path | str) -> bool:
        """Recover from failed bootloader deployment by purging corrupt EFI directories."""
        p = Path(esp_staging_path).resolve()
        efi_dir = p / "EFI"
        grub_dir = p / "boot"
        success = True
        for d in (efi_dir, grub_dir):
            if d.exists() and d.is_dir():
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except OSError:
                    success = False
        return success

    def clear(self) -> None:
        """Clear tracking when operation succeeds."""
        self._tracked_temp_dirs.clear()
        self._tracked_temp_files.clear()
        self._current_stage = None

    @classmethod
    def diagnose_installation_state(
        cls,
        partition_style: str,
        partitions: List[Dict[str, Any]],
        boot_mount: Optional[Path | str] = None,
        data_mount: Optional[Path | str] = None,
    ) -> InstallationStateDiagnostic:
        """Classify USB storage into one of seven distinct states and define safe recovery paths.

        States:
        1. ORIGINAL_FILESYSTEM: MBR or foreign partition scheme with active user data.
        2. EMPTY_GPT: GPT partition style with 0 partitions (cleared state).
        3. GPT_ESP_ONLY: GPT partition style with 1 partition (ESP allocated, data missing).
        4. GPT_UNFORMATTED: Both partitions allocated, but lacking valid FAT32/exFAT filesystems or labels.
        5. GPT_FORMATTED_NO_GRUB: Both partitions formatted (MVX_BOOT + MVX_DATA), but GRUB bootloader missing.
        6. PARTIAL_GRUB: Both partitions formatted and bootloader binary present, but modules/config incomplete.
        7. COMPLETE_MVENTX: All 12 installation milestones fully verified.
        """
        p_style = str(partition_style or "").upper().strip()
        part_cnt = len(partitions)

        # State 1: Original filesystem or non-GPT
        if p_style != "GPT":
            return InstallationStateDiagnostic(
                state=USBInstallationState.ORIGINAL_FILESYSTEM,
                state_name="Original Storage with Existing Filesystem",
                description="Storage contains legacy MBR or RAW partition structures from prior usage.",
                completed_stages=["Step 1: Device Validation", "Step 2: Partition Planning"],
                remaining_stages=["Step 3 through 12: Full Initialization Pipeline"],
                can_recover_without_wipe=False,
                recommended_action="Requires explicit two-phase confirmation before wiping and creating GPT layout.",
                requires_explicit_wipe_confirmation=True,
                partition_count=part_cnt,
            )

        # State 2: Empty GPT disk with zero partitions
        if part_cnt == 0:
            return InstallationStateDiagnostic(
                state=USBInstallationState.EMPTY_GPT,
                state_name="Empty GPT Storage (0 Partitions)",
                description="Disk is initialized with GUID Partition Table (GPT) and contains 0 partitions.",
                completed_stages=[
                    "Step 1: Device Validation",
                    "Step 2: Partition Planning",
                    "Step 3: Identity Verification",
                    "Step 4: Volume Dismount",
                    "Step 5: Pre-Destructive Gate",
                    "Step 6: Clear-Disk & GPT Initialization",
                ],
                remaining_stages=[
                    "Step 7: Allocate & Format MVX_BOOT (FAT32 ESP) + MVX_DATA (exFAT)",
                    "Step 8: Deploy GRUB 2.12 Bootloader",
                    "Step 9: Initialize MVentX Directories",
                    "Step 10: Write Initial Configuration",
                    "Step 11: Compile Initial Menu",
                    "Step 12: Run Structural Verification",
                ],
                can_recover_without_wipe=True,
                recommended_action="Safe to allocate Partition 1 and Partition 2 directly without redundant Clear-Disk.",
                requires_explicit_wipe_confirmation=False,
                partition_count=0,
            )

        # State 3: GPT with only 1 partition
        if part_cnt == 1:
            return InstallationStateDiagnostic(
                state=USBInstallationState.GPT_ESP_ONLY,
                state_name="GPT with EFI Partition Only (1 Partition)",
                description="Disk contains Partition 1 (ESP) but Partition 2 (MVX_DATA) was not allocated.",
                completed_stages=[
                    "Step 1: Device Validation",
                    "Step 2: Partition Planning",
                    "Step 6: GPT Initialization",
                    "Step 7a: Partition 1 Allocation",
                ],
                remaining_stages=[
                    "Step 7b: Partition 2 Allocation & Formatting",
                    "Step 8: Deploy GRUB 2.12 Bootloader",
                    "Step 9 through 12: MVentX Finalization",
                ],
                can_recover_without_wipe=True,
                recommended_action="Allocate Partition 2 in remaining capacity, or repartition if size is mismatched.",
                requires_explicit_wipe_confirmation=True,
                partition_count=1,
            )

        # State 4 through 7: Disk has 2 partitions
        p1 = partitions[0] if len(partitions) > 0 else {}
        p2 = partitions[1] if len(partitions) > 1 else {}

        p1_label = str(p1.get("label") or p1.get("FileSystemLabel") or "").upper()
        p2_label = str(p2.get("label") or p2.get("FileSystemLabel") or "").upper()
        p1_fs = str(p1.get("filesystem") or p1.get("FileSystem") or "").upper()
        p2_fs = str(p2.get("filesystem") or p2.get("FileSystem") or "").upper()

        has_formatted_fs = ("FAT" in p1_fs and "MVX_BOOT" in p1_label) and ("EXFAT" in p2_fs and "MVX_DATA" in p2_label)

        # State 4: Unformatted partitions
        if not has_formatted_fs:
            return InstallationStateDiagnostic(
                state=USBInstallationState.GPT_UNFORMATTED,
                state_name="GPT with Unformatted Partitions (2 Partitions)",
                description="Partitions are allocated but lack FAT32 (MVX_BOOT) or exFAT (MVX_DATA) formatting.",
                completed_stages=[
                    "Step 1: Device Validation",
                    "Step 2: Partition Planning",
                    "Step 6: GPT Initialization",
                    "Step 7a: Partition Allocation",
                ],
                remaining_stages=[
                    "Step 7b: Format Filesystems",
                    "Step 8: Deploy GRUB 2.12 Bootloader",
                    "Step 9 through 12: MVentX Finalization",
                ],
                can_recover_without_wipe=True,
                recommended_action="Format existing partitions directly without re-partitioning.",
                requires_explicit_wipe_confirmation=False,
                partition_count=part_cnt,
            )

        # Check bootloader files if mount paths are available
        bp = Path(boot_mount) if boot_mount else None
        dp = Path(data_mount) if data_mount else None

        has_efi_bin = False
        has_grub_modules = False
        has_mventx_json = False

        if bp and bp.exists():
            efi_file = bp / "EFI" / "BOOT" / "BOOTX64.EFI"
            has_efi_bin = efi_file.is_file()
            grub_mod_dir = bp / "boot" / "grub" / "x86_64-efi"
            has_grub_modules = grub_mod_dir.is_dir() and any(grub_mod_dir.iterdir())

        if dp and dp.exists():
            cfg_file = dp / "config" / "mventx.json"
            has_mventx_json = cfg_file.is_file()

        # State 5: Formatted partitions, missing GRUB bootloader
        if not has_efi_bin:
            return InstallationStateDiagnostic(
                state=USBInstallationState.GPT_FORMATTED_NO_GRUB,
                state_name="Formatted Partitions (Missing Bootloader)",
                description="Partitions are formatted (FAT32 MVX_BOOT + exFAT MVX_DATA), but GRUB is not yet deployed.",
                completed_stages=[
                    "Step 1 through 7: Device Validation, Partitioning, and Formatting",
                ],
                remaining_stages=[
                    "Step 8: Deploy GRUB 2.12 Bootloader",
                    "Step 9: Initialize MVentX Directory Skeleton",
                    "Step 10: Write Initial Configuration",
                    "Step 11: Compile Initial Menu",
                    "Step 12: Structural Verification",
                ],
                can_recover_without_wipe=True,
                recommended_action="Deploy GRUB 2.12 EFI bootloader and configure directories without reformatting.",
                requires_explicit_wipe_confirmation=False,
                partition_count=part_cnt,
            )

        # State 6: Partial GRUB deployment
        if not (has_grub_modules and has_mventx_json):
            return InstallationStateDiagnostic(
                state=USBInstallationState.PARTIAL_GRUB,
                state_name="Partially Deployed Bootloader",
                description="BOOTX64.EFI exists, but GRUB modules or MVentX configuration is missing or incomplete.",
                completed_stages=[
                    "Step 1 through 7: Partitioning and Formatting",
                    "Step 8a: Bootloader Executable",
                ],
                remaining_stages=[
                    "Step 8b: Deploy GRUB Modules & Fonts",
                    "Step 9: Initialize MVentX Directory Skeleton",
                    "Step 10: Write Initial Configuration",
                    "Step 11: Compile Initial Menu",
                    "Step 12: Structural Verification",
                ],
                can_recover_without_wipe=True,
                recommended_action="Re-deploy GRUB runtime modules and generate initial configuration.",
                requires_explicit_wipe_confirmation=False,
                partition_count=part_cnt,
            )

        # State 7: Complete MVentX installation
        return InstallationStateDiagnostic(
            state=USBInstallationState.COMPLETE_MVENTX,
            state_name="Complete MVentX Installation",
            description="All partition, filesystem, bootloader, configuration, and verification requirements met.",
            completed_stages=[
                f"Step {i}: Complete" for i in range(1, 13)
            ],
            remaining_stages=[],
            can_recover_without_wipe=True,
            recommended_action="Device is ready for booting and registering ISO environments.",
            requires_explicit_wipe_confirmation=True,
            partition_count=part_cnt,
        )

