"""Installation Service Coordinator for MVentX.

Orchestrates the complete 12-step installation pipeline:
1. Validate selected device.
2. Generate installation plan.
3. Verify device capacity and permissions.
4. Create GPT partition table.
5. Create EFI partition.
6. Create data partition.
7. Format partitions.
8. Install GRUB EFI bootloader.
9. Initialize MVentX directory structure.
10. Create initial configuration.
11. Generate initial GRUB menu.
12. Verify the completed installation.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import tempfile
from typing import Callable, Optional

from core.storage.device_info import DeviceInfo
from core.storage.device_manager import DeviceManager, DeviceSafetyError
from installer.bootloader_installer import BootloaderInstaller
from installer.device_preparation import DevicePreparationService
from installer.filesystem_manager import FilesystemManager
from installer.installation_recovery import InstallationRecoveryService
from installer.installation_verifier import InstallationVerificationReport, InstallationVerifier
from installer.partition_manager import PartitionManager
from installer.partition_planner import DEFAULT_ESP_SIZE_MB, PartitionPlan, PartitionPlanner

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]


class InstallationError(Exception):
    """Raised when the installation pipeline fails."""


@dataclass
class InstallationResult:
    """Outcome of an installation execution."""
    success: bool
    target_path: str
    verification_report: Optional[InstallationVerificationReport] = None
    error_message: Optional[str] = None
    partition_plan: Optional[PartitionPlan] = None


class InstallationService:
    """Orchestrates end-to-end device preparation and bootloader installation."""

    def __init__(
        self,
        device_manager: Optional[DeviceManager] = None,
        allow_physical_formatting: bool = False,
    ) -> None:
        self.device_manager = device_manager or DeviceManager(allow_physical_formatting=allow_physical_formatting)
        self.prep_service = DevicePreparationService(self.device_manager)
        self.partition_manager = PartitionManager(allow_physical_formatting=allow_physical_formatting)
        self.recovery_service = InstallationRecoveryService()

    def install_to_virtual_disk(
        self,
        target_image_path: Path | str,
        disk_size_mb: int = 256,
        esp_size_mb: int = DEFAULT_ESP_SIZE_MB,
        menu_title: str = "MVentX Multiboot System",
        menu_timeout: int = 10,
        progress_callback: Optional[ProgressCallback] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> InstallationResult:
        """Execute full 12-step installation workflow to a virtual disk image."""
        total_steps = 12
        out_image = Path(target_image_path).resolve()

        def report(step: int, msg: str) -> None:
            logger.info("Installation Step %d/%d: %s", step, total_steps, msg)
            self.recovery_service.track_stage(step, msg)
            if progress_callback:
                progress_callback(step, total_steps, msg)
            if is_cancelled and is_cancelled():
                raise InstallationError("Installation was cancelled by user.")

        temp_staging_dir = Path(tempfile.mkdtemp(prefix="mventx_install_staging_"))
        self.recovery_service.track_temp_dir(temp_staging_dir)

        try:
            # Step 1: Validate selected device target
            report(1, "Validating target storage device...")
            # Virtual disk target: ensure valid path and host storage
            device = self.prep_service.prepare_virtual_disk_file(
                target_path=out_image,
                size_bytes=disk_size_mb * 1024 * 1024,
            )

            # Step 2: Generate installation plan
            report(2, "Generating partition plan...")
            plan = PartitionPlanner.calculate_plan(
                disk_size_bytes=disk_size_mb * 1024 * 1024,
                esp_size_mb=esp_size_mb,
            )

            # Step 3: Verify device capacity and permissions
            report(3, "Verifying device capacity and safety permissions...")
            self.prep_service.validate_device_for_installation(device, required_size_bytes=disk_size_mb * 1024 * 1024)

            # Prepare staging directories for boot and data partitions
            esp_stage = temp_staging_dir / "esp"
            data_stage = temp_staging_dir / "data"
            esp_stage.mkdir(parents=True, exist_ok=True)
            data_stage.mkdir(parents=True, exist_ok=True)

            # Step 4: Create GPT partition table (Planned)
            report(4, "Creating GPT partition table structures...")

            # Step 5: Create EFI partition (Planned)
            report(5, f"Allocating EFI System Partition ({esp_size_mb} MiB)...")

            # Step 6: Create data partition (Planned)
            report(6, f"Allocating MVX_DATA partition ({plan.data_partition.size_mb:.1f} MiB)...")

            # Step 7: Format partitions (Staged filesystem layout)
            report(7, "Initializing FAT32 and exFAT filesystem structures...")
            FilesystemManager.initialize_boot_partition(esp_stage)

            # Step 8: Install GRUB EFI bootloader
            report(8, "Deploying GRUB 2.12 EFI bootloader and runtime modules...")
            boot_installer = BootloaderInstaller(esp_stage)
            boot_installer.deploy_all(title=menu_title, timeout=menu_timeout)

            # Step 9: Initialize MVentX directory structure
            report(9, "Initializing MVentX directory structure (ISO, config, metadata, backups)...")
            FilesystemManager.initialize_data_partition(
                data_mount_path=data_stage,
                initial_title=menu_title,
                initial_timeout=menu_timeout,
            )

            # Step 10: Create initial configuration
            report(10, "Writing initial mventx.json configuration (0 registered environments)...")
            # Already created by initialize_data_partition with 0 environments

            # Step 11: Generate initial GRUB menu
            report(11, "Compiling initial fallback mventx_menu.cfg...")
            # Already generated by boot_installer.deploy_all

            # Assemble virtual disk with complete GPT, FAT32, and exFAT structures
            self.partition_manager.create_and_format_virtual_disk(
                output_image_path=out_image,
                esp_staging_dir=esp_stage,
                data_staging_dir=data_stage,
                disk_size_mb=disk_size_mb,
                esp_size_mb=esp_size_mb,
            )

            # Step 12: Verify the completed installation
            report(12, "Verifying structural integrity and file readability of completed installation...")
            verifier = InstallationVerifier(out_image)
            verification_report = verifier.verify_virtual_disk()

            if not verification_report.is_valid:
                errors = "; ".join(verification_report.errors)
                raise InstallationError(f"Installation verification failed: {errors}")

            # Success: clean up temporary staging
            self.recovery_service.cleanup_on_error(remove_target_file=None)

            return InstallationResult(
                success=True,
                target_path=str(out_image),
                verification_report=verification_report,
                partition_plan=plan,
            )

        except Exception as exc:
            logger.error("Installation failed: %s", exc)
            self.recovery_service.cleanup_on_error(remove_target_file=out_image)
            return InstallationResult(
                success=False,
                target_path=str(out_image),
                error_message=str(exc),
            )

    def install_to_physical_device(
        self,
        device: DeviceInfo,
        expected_fingerprint: str,
        esp_size_mb: int = DEFAULT_ESP_SIZE_MB,
        menu_title: str = "MVentX Multiboot System",
        menu_timeout: int = 10,
        progress_callback: Optional[ProgressCallback] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> InstallationResult:
        """Execute physical USB installation with pre-write revalidation and full 12-step verification."""
        if not self.partition_manager.allow_physical_formatting:
            raise PermissionError(
                "CRITICAL SAFETY SHIELD: Physical storage device formatting is disabled by policy. "
                "allow_physical_formatting=True required."
            )

        from core.host.host_platform import HostPlatform
        if HostPlatform.is_windows():
            from installer.windows_physical_installer import WindowsPhysicalInstaller
            win_installer = WindowsPhysicalInstaller(
                device_manager=self.device_manager,
                allow_physical_formatting=self.partition_manager.allow_physical_formatting,
            )
            return win_installer.install_to_physical_disk(
                device=device,
                expected_fingerprint=expected_fingerprint,
                esp_size_mb=esp_size_mb,
                menu_title=menu_title,
                menu_timeout=menu_timeout,
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
            )

        raise NotImplementedError("Physical device installation is currently supported on Windows.")


