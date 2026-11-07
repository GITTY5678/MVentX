"""Integration tests for Windows Physical USB Pipeline and Recovery Classification (Milestone 17.1).

Tests the 7-state diagnostic recovery engine, PowerShell syntax validator,
and full 12-stage installation pipeline using disposable virtual disks.
Ensures zero physical disk modifications occur during test execution.
"""

from pathlib import Path
import shutil
import tempfile
import unittest

from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo
from installer.bootloader_installer import BootloaderInstaller
from installer.filesystem_manager import FilesystemManager
from installer.installation_recovery import (
    InstallationRecoveryService,
    InstallationStateDiagnostic,
    USBInstallationState,
)
from installer.installation_service import InstallationService
from installer.installation_verifier import InstallationVerifier
from installer.partition_planner import (
    DEFAULT_ESP_SIZE_MB,
    PartitionPlan,
    PartitionPlanner,
)
from installer.windows_physical_installer import (
    DEFAULT_PHYSICAL_ESP_SIZE_MB,
    WindowsPhysicalInstallationError,
    WindowsPhysicalInstaller,
)


class TestWindowsPhysicalPipelineVirtual(unittest.TestCase):
    """Integration test suite exercising the 7-state diagnostic engine and pipeline components."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_pipe_virtual_"))
        self.installer_service = InstallationService()

    def tearDown(self) -> None:
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # 1. 7-State Recovery Diagnostic Model Integration
    # --------------------------------------------------------------------------

    def test_state_1_original_filesystem_diagnosis(self) -> None:
        """Verify MBR or non-GPT disk is classified as ORIGINAL_FILESYSTEM requiring wipe."""
        partitions = [
            {"PartitionNumber": 1, "FileSystem": "NTFS", "FileSystemLabel": "OLD_BACKUP", "DriveLetter": "E"}
        ]
        diag = InstallationRecoveryService.diagnose_installation_state(
            partition_style="MBR",
            partitions=partitions,
        )
        self.assertEqual(diag.state, USBInstallationState.ORIGINAL_FILESYSTEM)
        self.assertFalse(diag.can_recover_without_wipe)
        self.assertTrue(diag.requires_explicit_wipe_confirmation)
        self.assertIn("two-phase confirmation", diag.recommended_action)

    def test_state_2_empty_gpt_diagnosis(self) -> None:
        """Verify GPT disk with 0 partitions is classified as EMPTY_GPT (skip Clear-Disk)."""
        diag = InstallationRecoveryService.diagnose_installation_state(
            partition_style="GPT",
            partitions=[],
        )
        self.assertEqual(diag.state, USBInstallationState.EMPTY_GPT)
        self.assertTrue(diag.can_recover_without_wipe)
        self.assertFalse(diag.requires_explicit_wipe_confirmation)
        self.assertIn("redundant Clear-Disk", diag.recommended_action)

    def test_state_3_gpt_esp_only_diagnosis(self) -> None:
        """Verify GPT disk with only 1 partition is classified as GPT_ESP_ONLY."""
        partitions = [
            {"PartitionNumber": 1, "FileSystem": "FAT32", "FileSystemLabel": "MVX_BOOT", "DriveLetter": "E"}
        ]
        diag = InstallationRecoveryService.diagnose_installation_state(
            partition_style="GPT",
            partitions=partitions,
        )
        self.assertEqual(diag.state, USBInstallationState.GPT_ESP_ONLY)
        self.assertTrue(diag.can_recover_without_wipe)
        self.assertIn("Allocate Partition 2", diag.recommended_action)

    def test_state_4_gpt_unformatted_diagnosis(self) -> None:
        """Verify GPT disk with 2 unformatted partitions is classified as GPT_UNFORMATTED."""
        partitions = [
            {"PartitionNumber": 1, "FileSystem": "", "FileSystemLabel": "", "DriveLetter": "E"},
            {"PartitionNumber": 2, "FileSystem": "", "FileSystemLabel": "", "DriveLetter": "F"},
        ]
        diag = InstallationRecoveryService.diagnose_installation_state(
            partition_style="GPT",
            partitions=partitions,
        )
        self.assertEqual(diag.state, USBInstallationState.GPT_UNFORMATTED)
        self.assertTrue(diag.can_recover_without_wipe)
        self.assertIn("Format existing partitions directly", diag.recommended_action)

    def test_state_5_gpt_formatted_no_grub_diagnosis(self) -> None:
        """Verify formatted partitions without GRUB BOOTX64.EFI are classified as GPT_FORMATTED_NO_GRUB."""
        esp_dir = self.temp_dir / "esp_empty"
        data_dir = self.temp_dir / "data_empty"
        esp_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        partitions = [
            {"PartitionNumber": 1, "FileSystem": "FAT32", "FileSystemLabel": "MVX_BOOT", "DriveLetter": "E"},
            {"PartitionNumber": 2, "FileSystem": "exFAT", "FileSystemLabel": "MVX_DATA", "DriveLetter": "F"},
        ]
        diag = InstallationRecoveryService.diagnose_installation_state(
            partition_style="GPT",
            partitions=partitions,
            boot_mount=esp_dir,
            data_mount=data_dir,
        )
        self.assertEqual(diag.state, USBInstallationState.GPT_FORMATTED_NO_GRUB)
        self.assertTrue(diag.can_recover_without_wipe)
        self.assertIn("Deploy GRUB 2.12 EFI bootloader", diag.recommended_action)

    def test_state_6_partial_grub_diagnosis(self) -> None:
        """Verify presence of BOOTX64.EFI without modules or mventx.json is classified as PARTIAL_GRUB."""
        esp_dir = self.temp_dir / "esp_partial"
        data_dir = self.temp_dir / "data_partial"
        (esp_dir / "EFI" / "BOOT").mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        # Place dummy BOOTX64.EFI
        (esp_dir / "EFI" / "BOOT" / "BOOTX64.EFI").write_bytes(b"\x00" * 1024)

        partitions = [
            {"PartitionNumber": 1, "FileSystem": "FAT32", "FileSystemLabel": "MVX_BOOT", "DriveLetter": "E"},
            {"PartitionNumber": 2, "FileSystem": "exFAT", "FileSystemLabel": "MVX_DATA", "DriveLetter": "F"},
        ]
        diag = InstallationRecoveryService.diagnose_installation_state(
            partition_style="GPT",
            partitions=partitions,
            boot_mount=esp_dir,
            data_mount=data_dir,
        )
        self.assertEqual(diag.state, USBInstallationState.PARTIAL_GRUB)
        self.assertTrue(diag.can_recover_without_wipe)
        self.assertIn("Re-deploy GRUB runtime modules", diag.recommended_action)

    def test_state_7_complete_mventx_diagnosis(self) -> None:
        """Verify fully populated MVX_BOOT and MVX_DATA are classified as COMPLETE_MVENTX."""
        esp_dir = self.temp_dir / "esp_complete"
        data_dir = self.temp_dir / "data_complete"

        # Deploy bootloader & modules
        boot_installer = BootloaderInstaller(esp_dir)
        boot_installer.deploy_all(title="MVentX Test", timeout=10)

        # Initialize data partition skeleton & config
        FilesystemManager.initialize_data_partition(data_dir, initial_title="MVentX Test")

        partitions = [
            {"PartitionNumber": 1, "FileSystem": "FAT32", "FileSystemLabel": "MVX_BOOT", "DriveLetter": "E"},
            {"PartitionNumber": 2, "FileSystem": "exFAT", "FileSystemLabel": "MVX_DATA", "DriveLetter": "F"},
        ]
        diag = InstallationRecoveryService.diagnose_installation_state(
            partition_style="GPT",
            partitions=partitions,
            boot_mount=esp_dir,
            data_mount=data_dir,
        )
        self.assertEqual(diag.state, USBInstallationState.COMPLETE_MVENTX)
        self.assertTrue(diag.can_recover_without_wipe)
        self.assertIn("ready for booting", diag.recommended_action)

    # --------------------------------------------------------------------------
    # 2. PowerShell Syntax Validation Integration
    # --------------------------------------------------------------------------

    def test_powershell_syntax_validator_catches_colon_and_parses_cleanly(self) -> None:
        """Verify PowerShell syntax validator catches colon interpolations and validates clean scripts."""
        valid_code = (
            "$disk = Get-Disk -Number 1; "
            "$parts = Get-Partition -DiskNumber 1; "
            "Write-Output \"Disk 1 has $($parts.Count) partitions.\""
        )
        is_valid, errors = WindowsPhysicalInstaller.validate_powershell_syntax(valid_code)
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)

        invalid_code = "Write-Host \"Drive is $bootLetter:\""
        is_valid, errors = WindowsPhysicalInstaller.validate_powershell_syntax(invalid_code)
        self.assertFalse(is_valid)
        self.assertTrue(len(errors) > 0)

    # --------------------------------------------------------------------------
    # 3. Virtual Disk Installation with 256 MiB ESP Plan
    # --------------------------------------------------------------------------

    def test_virtual_disk_full_installation_with_256mb_esp(self) -> None:
        """Execute complete 12-stage virtual disk preparation with 256 MiB ESP and verify all files."""
        vdisk_path = self.temp_dir / "virtual_test_disk.img"
        disk_size_mb = 512
        esp_size_mb = 256

        plan = PartitionPlanner.calculate_plan(
            disk_size_bytes=disk_size_mb * 1024 * 1024,
            esp_size_mb=esp_size_mb,
        )
        self.assertEqual(plan.esp_partition.size_mb, 256)
        self.assertGreater(plan.data_partition.size_mb, 200)

        res = self.installer_service.install_to_virtual_disk(
            target_image_path=vdisk_path,
            disk_size_mb=disk_size_mb,
            esp_size_mb=esp_size_mb,
            menu_title="MVentX M17.1 Test Installation",
            menu_timeout=10,
        )

        self.assertTrue(res.success)
        self.assertIsNotNone(res.verification_report)
        self.assertTrue(res.verification_report.is_valid)
        self.assertEqual(len(res.verification_report.errors), 0)
        self.assertGreaterEqual(res.verification_report.passed_count, 8)
        self.assertTrue(vdisk_path.exists())
        self.assertEqual(vdisk_path.stat().st_size, disk_size_mb * 1024 * 1024)

    def test_resume_deployment_on_gpt_formatted_no_grub_virtual(self) -> None:
        """Verify seamless resume from GPT_FORMATTED_NO_GRUB on virtual mounts without formatting."""
        esp_dir = self.temp_dir / "resume_esp"
        data_dir = self.temp_dir / "resume_data"
        esp_dir.mkdir(parents=True, exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True)

        partitions = [
            {"PartitionNumber": 1, "FileSystem": "FAT32", "FileSystemLabel": "MVX_BOOT", "DriveLetter": "E"},
            {"PartitionNumber": 2, "FileSystem": "exFAT", "FileSystemLabel": "MVX_DATA", "DriveLetter": "F"},
        ]

        # 1. State before resume
        diag_before = InstallationRecoveryService.diagnose_installation_state(
            partition_style="GPT",
            partitions=partitions,
            boot_mount=esp_dir,
            data_mount=data_dir,
        )
        self.assertEqual(diag_before.state, USBInstallationState.GPT_FORMATTED_NO_GRUB)
        self.assertTrue(diag_before.can_recover_without_wipe)

        # 2. Execute deployment stages (Steps 8-11)
        boot_installer = BootloaderInstaller(esp_dir)
        boot_installer.deploy_all(title="MVentX Resume Test", timeout=10)

        FilesystemManager.initialize_data_partition(data_dir, initial_title="MVentX Resume Test")
        boot_installer.deploy_initial_menu(title="MVentX Resume Test", timeout=10)

        # 3. Verification stage (Step 12)
        verifier = InstallationVerifier(data_dir)
        report = verifier.verify_mounted_partitions(boot_mount=esp_dir, data_mount=data_dir)
        self.assertTrue(report.is_valid)
        self.assertEqual(len(report.errors), 0)
        self.assertGreaterEqual(report.passed_count, 8)

        # 4. State after resume must transition to COMPLETE_MVENTX
        diag_after = InstallationRecoveryService.diagnose_installation_state(
            partition_style="GPT",
            partitions=partitions,
            boot_mount=esp_dir,
            data_mount=data_dir,
        )
        self.assertEqual(diag_after.state, USBInstallationState.COMPLETE_MVENTX)
        self.assertEqual(len(diag_after.remaining_stages), 0)


if __name__ == "__main__":
    unittest.main()
