"""MVentX USB Provisioning and Installer Subsystem.

Responsible for initial disk preparation, dual-partition layout creation,
and GRUB 2 bootloader installation.

IMPORTANT: Destructive formatting routines must remain isolated in this module
and strictly guarded behind explicit user confirmations.
"""

from typing import List

from installer.bootloader_installer import BootloaderInstaller, BootloaderInstallError
from installer.device_preparation import DevicePreparationService, DevicePreparationError
from installer.filesystem_manager import FilesystemManager
from installer.installation_recovery import InstallationRecoveryService
from installer.installation_service import InstallationService, InstallationResult, InstallationError
from installer.installation_verifier import InstallationVerifier, InstallationVerificationReport, CheckResult
from installer.partition_manager import PartitionManager, PartitionManagerError
from installer.partition_planner import PartitionPlanner, PartitionPlan, PlannedPartition, PartitionPlanError

__all__: List[str] = [
    "bootloader_installer",
    "device_preparation",
    "filesystem_manager",
    "installation_recovery",
    "installation_service",
    "installation_verifier",
    "partition_manager",
    "partition_planner",
    "BootloaderInstaller",
    "BootloaderInstallError",
    "DevicePreparationService",
    "DevicePreparationError",
    "FilesystemManager",
    "InstallationRecoveryService",
    "InstallationService",
    "InstallationResult",
    "InstallationError",
    "InstallationVerifier",
    "InstallationVerificationReport",
    "CheckResult",
    "PartitionManager",
    "PartitionManagerError",
    "PartitionPlanner",
    "PartitionPlan",
    "PlannedPartition",
    "PartitionPlanError",
]

