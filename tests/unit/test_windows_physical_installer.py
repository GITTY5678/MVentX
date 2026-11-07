"""Unit tests for Windows Physical USB Storage Installation Backend and UI Integration."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from core.host.device_identity import DeviceIdentity
from core.host.host_platform import HostPlatform
from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo
from core.storage.device_manager import DeviceManager, DeviceSafetyError
from installer.installation_service import InstallationResult
from installer.installation_verifier import CheckResult, InstallationVerificationReport
from installer.physical_device_workflow import DestructiveOperationPlan, PhysicalDeviceWorkflow
from installer.windows_physical_installer import (
    WindowsPhysicalInstallationError,
    WindowsPhysicalInstaller,
    WindowsVolumeLockedError,
)
from manager.services.device_service import DeviceService
from manager.ui.usb_installer import (
    InstallationWorker,
    PhysicalConfirmationDialog,
    USBInstallerScreen,
)

# Ensure single QApplication
APP = QApplication.instance() or QApplication([])


class TestWindowsPhysicalInstaller(unittest.TestCase):
    """Test suite for low-level Windows physical USB preparation backend."""

    def setUp(self) -> None:
        self.mock_dev_mgr = MagicMock(spec=DeviceManager)
        self.mock_dev_mgr.allow_physical_formatting = True
        self.installer = WindowsPhysicalInstaller(
            device_manager=self.mock_dev_mgr,
            allow_physical_formatting=True,
        )

        self.sample_usb = DeviceInfo(
            device_id="\\\\.\\PhysicalDrive1",
            name="SanDisk Cruzer Blade",
            size_bytes=63065554944,  # ~58.73 GiB
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            is_system_disk=False,
            vendor="SanDisk",
            model="Cruzer Blade",
            serial_number="03026216101525041338",
            partitions=[
                PartitionInfo(
                    partition_number=1,
                    name="Partition 1",
                    size_bytes=63065554944,
                    filesystem="fat32",
                    mount_point="E:\\",
                    label="OLD_DATA",
                )
            ],
        )

    def test_extract_disk_number(self) -> None:
        """Verify regex extraction of Windows physical disk indices."""
        self.assertEqual(WindowsPhysicalInstaller.extract_disk_number("\\\\.\\PHYSICALDRIVE1"), 1)
        self.assertEqual(WindowsPhysicalInstaller.extract_disk_number("\\\\.\\PhysicalDrive3"), 3)
        self.assertEqual(WindowsPhysicalInstaller.extract_disk_number("PhysicalDrive0"), 0)
        self.assertEqual(WindowsPhysicalInstaller.extract_disk_number("Disk 4"), 4)
        self.assertEqual(WindowsPhysicalInstaller.extract_disk_number("12"), 12)

        with self.assertRaises(ValueError):
            WindowsPhysicalInstaller.extract_disk_number("invalid_device_path")

    @patch.object(HostPlatform, "is_windows")
    @patch.object(HostPlatform, "is_elevated")
    def test_validate_host_environment_checks_windows_and_elevation(
        self, mock_elevated: MagicMock, mock_win: MagicMock
    ) -> None:
        """Verify operating system and Administrator elevation assertions."""
        # Non-Windows rejected
        mock_win.return_value = False
        with self.assertRaises(WindowsPhysicalInstallationError):
            WindowsPhysicalInstaller.validate_host_environment()

        # Windows but not elevated rejected
        mock_win.return_value = True
        mock_elevated.return_value = False
        with self.assertRaises(PermissionError) as ctx:
            WindowsPhysicalInstaller.validate_host_environment()
        self.assertIn("Administrator privileges", str(ctx.exception))

        # Windows and elevated succeeds
        mock_elevated.return_value = True
        WindowsPhysicalInstaller.validate_host_environment()

    @patch.object(WindowsPhysicalInstaller, "validate_host_environment")
    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_revalidate_physical_disk_rejects_system_and_non_usb(
        self, mock_ps: MagicMock, mock_val_env: MagicMock
    ) -> None:
        """Verify pre-write safety shields against system, boot, and fixed media."""
        # 1. System disk flag in PowerShell output
        sys_disk_json = json.dumps({
            "Number": 1,
            "FriendlyName": "Cruzer Blade",
            "BusType": "USB",
            "IsSystem": True,
            "IsBoot": False,
            "IsReadOnly": False,
        })
        mock_ps.return_value = sys_disk_json
        with self.assertRaises(DeviceSafetyError) as ctx:
            self.installer.revalidate_physical_disk(self.sample_usb, "dummy_fingerprint")
        self.assertIn("active Windows system/boot disk", str(ctx.exception))

        # 2. Non-USB BusType (e.g. NVMe or SATA)
        nvme_json = json.dumps({
            "Number": 1,
            "FriendlyName": "Samsung NVMe 980",
            "BusType": "NVMe",
            "IsSystem": False,
            "IsBoot": False,
            "IsReadOnly": False,
        })
        mock_ps.return_value = nvme_json
        fixed_usb = DeviceInfo(
            device_id="\\\\.\\PhysicalDrive1",
            name="Samsung NVMe",
            size_bytes=500 * 1024 * 1024 * 1024,
            device_type=DeviceType.FIXED_DISK,
            is_removable=False,
            is_system_disk=False,
        )
        with self.assertRaises(DeviceSafetyError) as ctx:
            self.installer.revalidate_physical_disk(fixed_usb, "dummy_fingerprint")
        self.assertIn("Only USB storage devices can be formatted", str(ctx.exception))

        # 3. Read-only media
        ro_json = json.dumps({
            "Number": 1,
            "FriendlyName": "Cruzer Blade",
            "BusType": "USB",
            "IsSystem": False,
            "IsBoot": False,
            "IsReadOnly": True,
        })
        mock_ps.return_value = ro_json
        with self.assertRaises(DeviceSafetyError) as ctx:
            self.installer.revalidate_physical_disk(self.sample_usb, "dummy_fingerprint")
        self.assertIn("read-only", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "validate_host_environment")
    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_revalidate_physical_disk_rejects_system_drive_c(
        self, mock_ps: MagicMock, mock_val_env: MagicMock
    ) -> None:
        """Verify rejection if any partition is mounted as C:."""
        disk_json = json.dumps({
            "Number": 1,
            "FriendlyName": "Cruzer Blade",
            "BusType": "USB",
            "IsSystem": False,
            "IsBoot": False,
            "IsReadOnly": False,
        })
        part_json = json.dumps([
            {"PartitionNumber": 1, "DriveLetter": "C", "IsSystem": True, "IsBoot": True}
        ])
        mock_ps.side_effect = [disk_json, part_json]

        with self.assertRaises(DeviceSafetyError) as ctx:
            self.installer.revalidate_physical_disk(self.sample_usb, "dummy_fingerprint")
        self.assertIn("system drive C:", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "validate_host_environment")
    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_revalidate_physical_disk_detects_fingerprint_mismatch(
        self, mock_ps: MagicMock, mock_val_env: MagicMock
    ) -> None:
        """Verify abortion if device identity shifted between confirmation and writing."""
        disk_json = json.dumps({
            "Number": 1,
            "FriendlyName": "Cruzer Blade",
            "BusType": "USB",
            "IsSystem": False,
            "IsBoot": False,
            "IsReadOnly": False,
        })
        mock_ps.side_effect = [disk_json, "[]"]

        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.revalidate_physical_disk(self.sample_usb, "EXPECTED_DIFFERENT_HASH")
        self.assertIn("identity changed between confirmation and execution", str(ctx.exception))

    def test_prepare_physical_partitions_enforces_policy_lock(self) -> None:
        """Verify prepare_physical_partitions raises PermissionError if allow_physical_formatting is False."""
        locked_installer = WindowsPhysicalInstaller(
            device_manager=self.mock_dev_mgr,
            allow_physical_formatting=False,
        )
        with self.assertRaises(PermissionError) as ctx:
            locked_installer.prepare_physical_partitions(disk_number=1)
        self.assertIn("Physical formatting is disabled by policy", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_prepare_physical_partitions_success(self, mock_ps: MagicMock) -> None:
        """Verify partition creation, formatting, and drive letter resolution."""
        mock_ps.return_value = json.dumps({
            "boot_letter": "E",
            "data_letter": "F",
        })

        b_mount, d_mount = self.installer.prepare_physical_partitions(disk_number=1, esp_size_mb=64)
        self.assertEqual(b_mount, "E:\\")
        self.assertEqual(d_mount, "F:\\")
        self.assertTrue(mock_ps.called)

    @patch.object(WindowsPhysicalInstaller, "validate_host_environment")
    @patch.object(WindowsPhysicalInstaller, "revalidate_physical_disk")
    @patch.object(WindowsPhysicalInstaller, "dismount_disk_volumes")
    @patch.object(WindowsPhysicalInstaller, "revalidate_disk_identity_strict")
    @patch.object(WindowsPhysicalInstaller, "prepare_physical_partitions")
    @patch("installer.windows_physical_installer.BootloaderInstaller")
    @patch("installer.windows_physical_installer.FilesystemManager")
    @patch("installer.windows_physical_installer.InstallationVerifier")
    def test_install_to_physical_disk_full_workflow(
        self,
        mock_verifier_cls: MagicMock,
        mock_fs_cls: MagicMock,
        mock_boot_cls: MagicMock,
        mock_prep_parts: MagicMock,
        mock_strict_reval: MagicMock,
        mock_dismount: MagicMock,
        mock_reval: MagicMock,
        mock_val_env: MagicMock,
    ) -> None:
        """Verify complete 12-step pipeline execution on physical USB device."""
        mock_reval.return_value = (1, {"FriendlyName": "Cruzer Blade"})
        mock_dismount.return_value = ["E:"]
        mock_strict_reval.return_value = {"FriendlyName": "Cruzer Blade"}
        mock_prep_parts.return_value = ("E:\\", "F:\\")

        # Mock successful verification
        mock_verifier = MagicMock()
        mock_report = InstallationVerificationReport(
            target_path="F:\\",
            is_valid=True,
            checks=[
                CheckResult(name="ESP Structure", passed=True, message="Valid"),
                CheckResult(name="GRUB Binary", passed=True, message="Present"),
            ],
            errors=[],
        )
        mock_verifier.verify_mounted_partitions.return_value = mock_report
        mock_verifier_cls.return_value = mock_verifier

        steps_called = []
        def on_progress(step: int, total: int, msg: str) -> None:
            steps_called.append((step, msg))

        fp = DeviceIdentity.from_device_info(self.sample_usb).compute_fingerprint()
        res = self.installer.install_to_physical_disk(
            device=self.sample_usb,
            expected_fingerprint=fp,
            esp_size_mb=64,
            progress_callback=on_progress,
        )

        self.assertTrue(res.success)
        self.assertIsNotNone(res.verification_report)
        self.assertTrue(res.verification_report.is_valid)
        self.assertEqual(len(steps_called), 12)
        self.assertEqual(steps_called[0][0], 1)
        self.assertEqual(steps_called[-1][0], 12)
        mock_dismount.assert_called_once_with(1)
        mock_strict_reval.assert_called_once()

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_powershell_5_1_script_contains_no_dismount_volume(self, mock_ps: MagicMock) -> None:
        """Verify prepare_physical_partitions PowerShell script does not use unsupported Dismount-Volume cmdlet."""
        mock_ps.return_value = json.dumps({"boot_letter": "E", "data_letter": "F"})
        self.installer.prepare_physical_partitions(disk_number=1, esp_size_mb=64, skip_dismount=True)

        self.assertTrue(mock_ps.called)
        script_executed = mock_ps.call_args[0][0]

        # Must NOT contain Dismount-Volume cmdlet (incompatible with PS 5.1 Storage module)
        self.assertNotIn("Dismount-Volume", script_executed)

        # Must contain Write-VolumeCache for flushing dirty filesystem buffers in PowerShell 5.1
        self.assertIn("Write-VolumeCache", script_executed)

        # Must contain standard disk partition preparation cmdlets
        self.assertIn("Clear-Disk", script_executed)
        self.assertIn("Initialize-Disk", script_executed)
        self.assertIn("New-Partition", script_executed)
        self.assertIn("Format-Volume", script_executed)

    @patch("subprocess.run")
    def test_run_powershell_empty_stderr_includes_command_snippet(self, mock_subproc: MagicMock) -> None:
        """Verify run_powershell includes command snippet and return code when stderr and stdout are empty."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "   "
        mock_proc.stderr = ""
        mock_subproc.return_value = mock_proc

        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.run_powershell("Get-Disk -Number 1; exit 1", timeout=5)

        err_text = str(ctx.exception)
        self.assertIn("PowerShell disk operation failed", err_text)
        self.assertIn("PowerShell exited with code 1", err_text)
        self.assertIn("Get-Disk -Number 1", err_text)

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_prepare_physical_partitions_script_formats_and_sets_esp_type(self, mock_ps: MagicMock) -> None:
        """Verify prepare_physical_partitions formats FAT32 and sets ESP GptType afterwards."""
        mock_ps.return_value = json.dumps({"boot_letter": "E", "data_letter": "F"})
        self.installer.prepare_physical_partitions(disk_number=1, esp_size_mb=64, skip_dismount=True)

        self.assertTrue(mock_ps.called)
        script = mock_ps.call_args[0][0]

        # Verify FAT32 MVX_BOOT and exFAT MVX_DATA
        self.assertIn("FileSystem FAT32", script)
        self.assertIn("MVX_BOOT", script)
        self.assertIn("FileSystem exFAT", script)
        self.assertIn("MVX_DATA", script)

        # Verify Set-Partition applies ESP GPT Type to partition 1
        self.assertIn("Set-Partition -DiskNumber $diskNum -PartitionNumber $p1.PartitionNumber -GptType $espGptType", script)

        # Verify comprehensive try / catch error trap
        self.assertIn("try {", script)
        self.assertIn("} catch {", script)
        self.assertIn("=== POWERSHELL EXECUTION ERROR ===", script)
        self.assertIn("Failing Command :", script)
        self.assertIn("Script Line     :", script)
        self.assertIn("Exception Type  :", script)
        self.assertIn("Stack Trace     :", script)
        self.assertIn("[Console]::Error.WriteLine", script)

    @patch("subprocess.run")
    def test_run_powershell_surfaces_powershell_diagnostic_block(self, mock_subproc: MagicMock) -> None:
        """Verify run_powershell preserves and surfaces structured PowerShell execution diagnostics."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = (
            "=== POWERSHELL EXECUTION ERROR ===\n"
            "Failing Command : New-Partition\n"
            "Script Line     : 62\n"
            "Exception Type  : Microsoft.Management.Infrastructure.CimException\n"
            "HResult         : 0x80338000\n"
            "Error Message   : Drive letter cannot be assigned\n"
            "Stack Trace     : at New-Partition, line 62\n"
            "=================================="
        )
        mock_subproc.return_value = mock_proc

        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.run_powershell("dummy script", timeout=5)

        err_text = str(ctx.exception)
        self.assertIn("=== POWERSHELL EXECUTION ERROR ===", err_text)
        self.assertIn("Failing Command : New-Partition", err_text)
        self.assertIn("Script Line     : 62", err_text)
        self.assertIn("CimException", err_text)
        self.assertIn("0x80338000", err_text)

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_disk_query_contains_settling_retry_loop_and_no_silent_exit(self, mock_ps: MagicMock) -> None:
        """Verify disk revalidation and GPT verification scripts include settling retry loops and no bare exit 1."""
        mock_ps.return_value = json.dumps({
            "PartitionStyle": "GPT",
            "OperationalStatus": "Online",
            "PartitionCount": 0,
            "IsBoot": False,
            "IsSystem": False,
        })
        self.installer.initialize_or_verify_gpt(disk_number=1)

        self.assertTrue(mock_ps.called)
        script = mock_ps.call_args[0][0]
        self.assertIn("for ($i = 0; $i -lt 4; $i++)", script)
        self.assertIn("Start-Sleep -Milliseconds 400", script)
        self.assertNotIn("exit 1", script)
        self.assertIn("throw", script)

    def test_parse_partition_json_variants(self) -> None:
        """Verify parse_partition_json handles zero, one, and multiple partition results in PS 5.1."""
        # 1. Zero partitions (empty string, whitespace, null, empty array)
        self.assertEqual(WindowsPhysicalInstaller.parse_partition_json(""), [])
        self.assertEqual(WindowsPhysicalInstaller.parse_partition_json("   \n"), [])
        self.assertEqual(WindowsPhysicalInstaller.parse_partition_json("null"), [])
        self.assertEqual(WindowsPhysicalInstaller.parse_partition_json("[]"), [])

        # 2. Single partition object (PS 5.1 ConvertTo-Json single item)
        single_obj = '{"PartitionNumber": 1, "DriveLetter": "E", "IsSystem": false, "IsBoot": false}'
        parsed_single = WindowsPhysicalInstaller.parse_partition_json(single_obj)
        self.assertEqual(len(parsed_single), 1)
        self.assertEqual(parsed_single[0]["PartitionNumber"], 1)
        self.assertEqual(parsed_single[0]["DriveLetter"], "E")

        # 3. Multiple partitions (PS 5.1 ConvertTo-Json array)
        multi_obj = '[{"PartitionNumber": 1, "DriveLetter": "E"}, {"PartitionNumber": 2, "DriveLetter": "F"}]'
        parsed_multi = WindowsPhysicalInstaller.parse_partition_json(multi_obj)
        self.assertEqual(len(parsed_multi), 2)
        self.assertEqual(parsed_multi[0]["DriveLetter"], "E")
        self.assertEqual(parsed_multi[1]["DriveLetter"], "F")

        # 4. Malformed JSON safely degrades to empty list
        self.assertEqual(WindowsPhysicalInstaller.parse_partition_json("Invalid JSON {{"), [])

    @patch.object(HostPlatform, "is_windows", return_value=True)
    @patch.object(HostPlatform, "is_elevated", return_value=True)
    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_revalidate_physical_disk_cleared_disk_zero_partitions_succeeds(
        self, mock_ps: MagicMock, mock_elev: MagicMock, mock_win: MagicMock
    ) -> None:
        """Verify revalidate_physical_disk treats empty partition collection as valid for cleared disk."""
        disk_data = {
            "Number": 1,
            "FriendlyName": "SanDisk Cruzer Blade",
            "Manufacturer": "SanDisk",
            "Model": "Cruzer Blade",
            "SerialNumber": "03026216101525041338",
            "BusType": "USB",
            "Size": 63065554944,
            "LogicalSectorSize": 512,
            "PartitionStyle": "GPT",
            "IsBoot": False,
            "IsSystem": False,
            "IsReadOnly": False,
        }

        def mock_ps_side_effect(script: str, timeout: int = 120) -> str:
            if "Get-Disk" in script:
                return json.dumps(disk_data)
            if "Get-Partition" in script:
                # Returns empty partition set representing cleared GPT disk
                return "[]"
            return ""

        mock_ps.side_effect = mock_ps_side_effect

        fp = DeviceIdentity.from_device_info(self.sample_usb).compute_fingerprint()
        disk_num, returned_data = self.installer.revalidate_physical_disk(self.sample_usb, fp)

        self.assertEqual(disk_num, 1)
        self.assertEqual(returned_data["SerialNumber"], "03026216101525041338")

    @patch.object(HostPlatform, "is_windows", return_value=True)
    @patch.object(HostPlatform, "is_elevated", return_value=True)
    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_revalidate_physical_disk_system_drive_partition_rejected(
        self, mock_ps: MagicMock, mock_elev: MagicMock, mock_win: MagicMock
    ) -> None:
        """Verify revalidate_physical_disk rejects a disk containing the active system drive or boot flag."""
        disk_data = {
            "Number": 1,
            "FriendlyName": "SanDisk Cruzer Blade",
            "Model": "Cruzer Blade",
            "SerialNumber": "03026216101525041338",
            "BusType": "USB",
            "Size": 63065554944,
            "IsBoot": False,
            "IsSystem": False,
            "IsReadOnly": False,
        }

        # Case 1: Partition has DriveLetter C:
        mock_ps.side_effect = [
            json.dumps(disk_data),
            json.dumps({"PartitionNumber": 1, "DriveLetter": "C", "IsSystem": False, "IsBoot": False}),
        ]
        fp = DeviceIdentity.from_device_info(self.sample_usb).compute_fingerprint()
        with self.assertRaises(DeviceSafetyError) as ctx:
            self.installer.revalidate_physical_disk(self.sample_usb, fp)
        self.assertIn("mounted as system drive C:", str(ctx.exception))

        # Case 2: Partition has IsSystem = True
        mock_ps.side_effect = [
            json.dumps(disk_data),
            json.dumps({"PartitionNumber": 1, "DriveLetter": "E", "IsSystem": True, "IsBoot": False}),
        ]
        with self.assertRaises(DeviceSafetyError) as ctx:
            self.installer.revalidate_physical_disk(self.sample_usb, fp)
        self.assertIn("active Windows boot/system partition", str(ctx.exception))

    @patch.object(HostPlatform, "is_windows", return_value=True)
    @patch.object(HostPlatform, "is_elevated", return_value=True)
    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_revalidate_physical_disk_genuine_storage_failure_propagates(
        self, mock_ps: MagicMock, mock_elev: MagicMock, mock_win: MagicMock
    ) -> None:
        """Verify revalidate_physical_disk does not suppress genuine storage-provider errors on Get-Partition."""
        disk_data = {
            "Number": 1,
            "FriendlyName": "SanDisk Cruzer Blade",
            "Model": "Cruzer Blade",
            "SerialNumber": "03026216101525041338",
            "BusType": "USB",
            "Size": 63065554944,
            "IsBoot": False,
            "IsSystem": False,
            "IsReadOnly": False,
        }
        mock_ps.side_effect = [
            json.dumps(disk_data),
            WindowsPhysicalInstallationError("PowerShell disk operation failed: Access Denied to storage provider."),
        ]
        fp = DeviceIdentity.from_device_info(self.sample_usb).compute_fingerprint()
        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.revalidate_physical_disk(self.sample_usb, fp)
        self.assertIn("Access Denied", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_enumerate_disk_volume_targets_zero_partitions_returns_empty(self, mock_ps: MagicMock) -> None:
        """Verify enumerate_disk_volume_targets returns empty list on cleared disk with zero partitions."""
        mock_ps.return_value = "[]"
        targets = self.installer.enumerate_disk_volume_targets(disk_number=1)
        self.assertEqual(targets, [])

    @patch.object(WindowsPhysicalInstaller, "enumerate_disk_volume_targets")
    @patch.object(WindowsPhysicalInstaller, "dismount_volume_win32")
    def test_successful_volume_dismount(
        self, mock_dismount_win32: MagicMock, mock_enum: MagicMock
    ) -> None:
        """Verify dismount_disk_volumes iterates all targets and dismounts each without hardcoding drive letters."""
        mock_enum.return_value = [
            {"display_name": "E:", "target_path": "\\\\.\\E:", "partition_number": 1},
            {"display_name": "F:", "target_path": "\\\\.\\F:", "partition_number": 2},
        ]

        dismounted = self.installer.dismount_disk_volumes(disk_number=1)
        self.assertEqual(dismounted, ["E:", "F:"])
        self.assertEqual(mock_dismount_win32.call_count, 2)
        mock_dismount_win32.assert_any_call(target_path="\\\\.\\E:", display_name="E:")
        mock_dismount_win32.assert_any_call(target_path="\\\\.\\F:", display_name="F:")

    @patch.object(WindowsPhysicalInstaller, "enumerate_disk_volume_targets")
    @patch.object(WindowsPhysicalInstaller, "dismount_volume_win32")
    def test_volume_locked_by_another_process_raises_explicit_error(
        self, mock_dismount_win32: MagicMock, mock_enum: MagicMock
    ) -> None:
        """Verify that locked volumes raise WindowsVolumeLockedError and do not kill processes."""
        mock_enum.return_value = [
            {"display_name": "E:", "target_path": "\\\\.\\E:", "partition_number": 1}
        ]
        mock_dismount_win32.side_effect = WindowsVolumeLockedError(
            "Volume 'E:' (\\\\.\\E:) is locked or in use by another process (Win32 error 32). "
            "Please close all open files, File Explorer windows, or applications using this drive before proceeding."
        )

        with self.assertRaises(WindowsVolumeLockedError) as ctx:
            self.installer.dismount_disk_volumes(disk_number=1)

        self.assertIn("locked or in use by another process", str(ctx.exception))
        self.assertIn("File Explorer", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_usb_disconnection_before_write_raises_error(self, mock_ps: MagicMock) -> None:
        """Verify revalidate_disk_identity_strict aborts if USB device is unplugged or vanishes before write."""
        mock_ps.return_value = ""  # Device vanished / Get-Disk exited 1
        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.revalidate_disk_identity_strict(
                device=self.sample_usb,
                expected_fingerprint="dummy_fp",
                disk_number=1,
            )
        self.assertIn("disconnected or vanished", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_identity_mismatch_aborts(self, mock_ps: MagicMock) -> None:
        """Verify revalidate_disk_identity_strict aborts on any serial, model, capacity, or fingerprint change."""
        fp = DeviceIdentity.from_device_info(self.sample_usb).compute_fingerprint()

        # 1. Hardware serial number mismatch
        disk_diff_serial = {
            "Number": 1,
            "FriendlyName": "Cruzer Blade",
            "Model": "Cruzer Blade",
            "SerialNumber": "9999999999999999",
            "BusType": "USB",
            "Size": 63065554944,
            "IsBoot": False,
            "IsSystem": False,
            "IsReadOnly": False,
        }
        mock_ps.return_value = json.dumps(disk_diff_serial)
        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.revalidate_disk_identity_strict(self.sample_usb, fp, 1)
        self.assertIn("hardware serial changed before write", str(ctx.exception))

        # 2. Hardware model mismatch
        disk_diff_model = {
            "Number": 1,
            "FriendlyName": "Kingston DataTraveler",
            "Model": "Kingston DataTraveler",
            "SerialNumber": "03026216101525041338",
            "BusType": "USB",
            "Size": 63065554944,
            "IsBoot": False,
            "IsSystem": False,
            "IsReadOnly": False,
        }
        mock_ps.return_value = json.dumps(disk_diff_model)
        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.revalidate_disk_identity_strict(self.sample_usb, fp, 1)
        self.assertIn("model mismatch", str(ctx.exception))

        # 3. Capacity mismatch (> 1 MiB tolerance)
        disk_diff_size = {
            "Number": 1,
            "FriendlyName": "Cruzer Blade",
            "Model": "Cruzer Blade",
            "SerialNumber": "03026216101525041338",
            "BusType": "USB",
            "Size": 128000000000,
            "IsBoot": False,
            "IsSystem": False,
            "IsReadOnly": False,
        }
        mock_ps.return_value = json.dumps(disk_diff_size)
        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.revalidate_disk_identity_strict(self.sample_usb, fp, 1)
        self.assertIn("capacity mismatch", str(ctx.exception))

        # 4. Fingerprint mismatch
        disk_ok = {
            "Number": 1,
            "FriendlyName": "Cruzer Blade",
            "Model": "Cruzer Blade",
            "SerialNumber": "03026216101525041338",
            "BusType": "USB",
            "Size": 63065554944,
            "IsBoot": False,
            "IsSystem": False,
            "IsReadOnly": False,
        }
        mock_ps.return_value = json.dumps(disk_ok)
        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.revalidate_disk_identity_strict(self.sample_usb, "MISMATCHED_FP_HASH", 1)
        self.assertIn("identity changed between confirmation and execution", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "validate_host_environment")
    @patch.object(WindowsPhysicalInstaller, "revalidate_physical_disk")
    @patch.object(WindowsPhysicalInstaller, "dismount_disk_volumes")
    @patch.object(WindowsPhysicalInstaller, "revalidate_disk_identity_strict")
    @patch.object(WindowsPhysicalInstaller, "prepare_physical_partitions")
    def test_dismount_failure_prevents_destructive_commands(
        self,
        mock_prep_parts: MagicMock,
        mock_strict_reval: MagicMock,
        mock_dismount: MagicMock,
        mock_reval: MagicMock,
        mock_val_env: MagicMock,
    ) -> None:
        """Verify that a dismount failure immediately halts installation before any destructive writes occur."""
        mock_reval.return_value = (1, {"FriendlyName": "Cruzer Blade"})
        mock_dismount.side_effect = WindowsVolumeLockedError("Volume 'E:' is locked by another process.")

        fp = DeviceIdentity.from_device_info(self.sample_usb).compute_fingerprint()
        res = self.installer.install_to_physical_disk(
            device=self.sample_usb,
            expected_fingerprint=fp,
            esp_size_mb=64,
        )

        self.assertFalse(res.success)
        self.assertIn("Volume 'E:' is locked", res.error_message)
        # Verify prepare_physical_partitions (which contains Clear-Disk and Format-Volume) was NEVER called
        mock_prep_parts.assert_not_called()
        # Verify strict revalidation was not reached
        mock_strict_reval.assert_not_called()

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_raw_disk_initialization(self, mock_ps: MagicMock) -> None:
        """Verify initialize_or_verify_gpt initializes GPT when disk is RAW."""
        # Query returns RAW, then init script returns GPT
        query_json = json.dumps({
            "PartitionStyle": "RAW",
            "OperationalStatus": "Online",
            "PartitionCount": 0,
        })
        init_json = json.dumps({
            "PartitionStyle": "GPT",
            "OperationalStatus": "Online",
        })
        mock_ps.side_effect = [query_json, init_json]

        res = self.installer.initialize_or_verify_gpt(disk_number=1)
        self.assertEqual(res["status"], "initialized")
        self.assertEqual(res["partition_style"], "GPT")
        self.assertTrue(res["initialized_called"])
        self.assertEqual(mock_ps.call_count, 2)
        self.assertIn("Initialize-Disk", mock_ps.call_args_list[1][0][0])

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_already_gpt_empty_disk(self, mock_ps: MagicMock) -> None:
        """Verify initialize_or_verify_gpt detects empty GPT disk and does NOT call Initialize-Disk."""
        query_json = json.dumps({
            "PartitionStyle": "GPT",
            "OperationalStatus": "Online",
            "PartitionCount": 0,
        })
        mock_ps.return_value = query_json

        res = self.installer.initialize_or_verify_gpt(disk_number=1)
        self.assertEqual(res["status"], "already_gpt")
        self.assertEqual(res["partition_style"], "GPT")
        self.assertFalse(res["initialized_called"])
        # Exactly one call (query only), no Initialize-Disk invoked
        self.assertEqual(mock_ps.call_count, 1)
        self.assertNotIn("Initialize-Disk", mock_ps.call_args[0][0])

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_gpt_disk_with_unexpected_partitions_aborts(self, mock_ps: MagicMock) -> None:
        """Verify initialize_or_verify_gpt aborts if GPT disk still has partitions after clearing."""
        query_json = json.dumps({
            "PartitionStyle": "GPT",
            "OperationalStatus": "Online",
            "PartitionCount": 2,
        })
        mock_ps.return_value = query_json

        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.initialize_or_verify_gpt(disk_number=1)
        self.assertIn("still contains 2 partition(s) after Clear-Disk", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_mbr_disk_rejection_aborts(self, mock_ps: MagicMock) -> None:
        """Verify initialize_or_verify_gpt rejects MBR disks."""
        query_json = json.dumps({
            "PartitionStyle": "MBR",
            "OperationalStatus": "Online",
            "PartitionCount": 0,
        })
        mock_ps.return_value = query_json

        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.initialize_or_verify_gpt(disk_number=1)
        self.assertIn("reports MBR partition style after Clear-Disk", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "revalidate_disk_identity_strict")
    def test_usb_disconnection_between_clearing_and_partitioning_aborts(
        self, mock_strict_reval: MagicMock
    ) -> None:
        """Verify prepare_physical_partitions aborts if USB disconnects before destructive writes."""
        mock_strict_reval.side_effect = WindowsPhysicalInstallationError(
            "Target storage device 'SanDisk Cruzer Blade' (Disk 1) disconnected or vanished from storage bus!"
        )

        fp = DeviceIdentity.from_device_info(self.sample_usb).compute_fingerprint()
        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.prepare_physical_partitions(
                disk_number=1,
                esp_size_mb=64,
                skip_dismount=True,
                device=self.sample_usb,
                expected_fingerprint=fp,
            )
        self.assertIn("disconnected or vanished", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_identity_mismatch_between_clearing_and_partitioning_aborts(
        self, mock_ps: MagicMock
    ) -> None:
        """Verify pre-partitioning identity revalidation aborts if device attributes shift."""
        fp = DeviceIdentity.from_device_info(self.sample_usb).compute_fingerprint()
        disk_mismatch = {
            "Number": 1,
            "FriendlyName": "Cruzer Blade",
            "Model": "Cruzer Blade",
            "SerialNumber": "DIFFERENT_SERIAL_AFTER_CLEAR",
            "BusType": "USB",
            "Size": 63065554944,
            "IsBoot": False,
            "IsSystem": False,
            "IsReadOnly": False,
        }
        mock_ps.return_value = json.dumps(disk_mismatch)

        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.revalidate_disk_identity_strict(
                device=self.sample_usb,
                expected_fingerprint=fp,
                disk_number=1,
            )
        self.assertIn("hardware serial changed before write", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_no_duplicate_initialize_disk_invocation(self, mock_ps: MagicMock) -> None:
        """Explicit assertion that an empty GPT disk triggers 0 Initialize-Disk commands."""
        query_json = json.dumps({
            "PartitionStyle": "GPT",
            "OperationalStatus": "Online",
            "PartitionCount": 0,
        })
        mock_ps.return_value = query_json

        self.installer.initialize_or_verify_gpt(disk_number=1)

        # Iterate all run_powershell calls and assert Initialize-Disk was never invoked
        for call_args in mock_ps.call_args_list:
            cmd_script = call_args[0][0]
            self.assertNotIn("Initialize-Disk", cmd_script)

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_successful_partition_creation_after_gpt_verification(self, mock_ps: MagicMock) -> None:
        """Verify partition layout verification confirms GUIDs, 1 MiB alignment, and labels."""
        verify_data = {
            "p1_number": 1,
            "p1_guid": "{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}",
            "p1_offset": 1048576,
            "p1_size": 67108864,
            "p1_letter": "E",
            "p1_label": "MVX_BOOT",
            "p1_fs": "FAT32",
            "p2_number": 2,
            "p2_guid": "{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}",
            "p2_offset": 68157440,
            "p2_size": 62997397504,
            "p2_letter": "F",
            "p2_label": "MVX_DATA",
            "p2_fs": "exFAT",
            "verified": True,
        }
        mock_ps.return_value = json.dumps(verify_data)

        res = self.installer.verify_partition_layout(disk_number=1, esp_size_mb=64)
        self.assertTrue(res.get("verified"))
        self.assertEqual(res.get("p1_label"), "MVX_BOOT")
        self.assertEqual(res.get("p2_label"), "MVX_DATA")
        self.assertEqual(res.get("p1_guid"), "{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}")
        self.assertEqual(res.get("p2_guid"), "{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}")
        self.assertEqual(res.get("p1_offset") % 1048576, 0)
        self.assertEqual(res.get("p2_offset") % 1048576, 0)

    def test_validate_powershell_syntax_valid_script(self) -> None:
        """Verify validate_powershell_syntax recognizes clean PowerShell scripts."""
        valid_script = 'Get-Disk -Number 1 | Select-Object -Property Number, FriendlyName'
        is_valid, errors = WindowsPhysicalInstaller.validate_powershell_syntax(valid_script)
        self.assertTrue(is_valid)
        self.assertEqual(errors, [])

    def test_validate_powershell_syntax_colon_reference_error(self) -> None:
        """Verify validate_powershell_syntax catches illegal $var: colon syntax."""
        bad_script = '$bootLetter = "E"; Write-Host "$bootLetter:"'
        is_valid, errors = WindowsPhysicalInstaller.validate_powershell_syntax(bad_script)
        self.assertFalse(is_valid)
        self.assertTrue(any("Invalid variable reference" in e or "colon" in e.lower() for e in errors))

    def test_validate_powershell_syntax_unclosed_block_error(self) -> None:
        """Verify validate_powershell_syntax catches AST parser errors such as unclosed blocks."""
        bad_script = 'if ($true) { Write-Host "missing closing brace"'
        is_valid, errors = WindowsPhysicalInstaller.validate_powershell_syntax(bad_script)
        self.assertFalse(is_valid)
        self.assertTrue(len(errors) > 0)

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_diagnose_physical_disk_empty_gpt(self, mock_ps: MagicMock) -> None:
        """Verify diagnose_physical_disk classifies empty GPT disk as EMPTY_GPT."""
        mock_data = {
            "found": True,
            "PartitionStyle": "GPT",
            "OperationalStatus": "Online",
            "Size": 63065554944,
            "Model": "Cruzer Blade",
            "FriendlyName": "SanDisk Cruzer Blade",
            "Partitions": [],
        }
        mock_ps.return_value = json.dumps(mock_data)
        diag = self.installer.diagnose_physical_disk(disk_number=1)
        self.assertEqual(diag.state.value, "EMPTY_GPT")
        self.assertTrue(diag.can_recover_without_wipe)
        self.assertIn("Safe to allocate Partition 1 and Partition 2 directly", diag.recommended_action)

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_diagnose_physical_disk_gpt_unformatted(self, mock_ps: MagicMock) -> None:
        """Verify diagnose_physical_disk classifies partitions without FAT32/exFAT as GPT_UNFORMATTED."""
        mock_data = {
            "found": True,
            "PartitionStyle": "GPT",
            "OperationalStatus": "Online",
            "Size": 63065554944,
            "Model": "Cruzer Blade",
            "FriendlyName": "SanDisk Cruzer Blade",
            "Partitions": [
                {"PartitionNumber": 1, "DriveLetter": "E", "FileSystem": "", "FileSystemLabel": ""},
                {"PartitionNumber": 2, "DriveLetter": "F", "FileSystem": "", "FileSystemLabel": ""},
            ],
        }
        mock_ps.return_value = json.dumps(mock_data)
        diag = self.installer.diagnose_physical_disk(disk_number=1)
        self.assertEqual(diag.state.value, "GPT_UNFORMATTED")
        self.assertTrue(diag.can_recover_without_wipe)

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_prepare_physical_partitions_empty_gpt_skips_clear_disk(self, mock_ps: MagicMock) -> None:
        """Verify prepare_physical_partitions on EMPTY_GPT state uses EmptyGptAllocate and skips Clear-Disk."""
        diag_data = {
            "found": True,
            "PartitionStyle": "GPT",
            "OperationalStatus": "Online",
            "Size": 63065554944,
            "Model": "Cruzer Blade",
            "FriendlyName": "SanDisk Cruzer Blade",
            "Partitions": [],
        }
        prep_data = {
            "boot_letter": "E",
            "data_letter": "F",
            "mode_executed": "EmptyGptAllocate",
            "verified": True,
        }
        mock_ps.side_effect = [json.dumps(diag_data), json.dumps(prep_data)]

        b_mount, d_mount = self.installer.prepare_physical_partitions(disk_number=1, esp_size_mb=256, skip_dismount=True)
        self.assertEqual(b_mount, "E:\\")
        self.assertEqual(d_mount, "F:\\")

        # Second call to run_powershell is the partitioning script
        script = mock_ps.call_args_list[1][0][0]
        self.assertIn("$mode = 'EmptyGptAllocate'", script)
        self.assertIn("New-Partition", script)
        self.assertIn("Format-Volume", script)

    def test_extract_json_from_output_clean_json(self) -> None:
        """Verify extract_json_from_output parses clean JSON dict."""
        payload = '{"boot_letter": "E", "data_letter": "F", "verified": true}'
        res = WindowsPhysicalInstaller.extract_json_from_output(payload)
        self.assertEqual(res["boot_letter"], "E")
        self.assertEqual(res["data_letter"], "F")
        self.assertTrue(res["verified"])

    def test_extract_json_from_output_with_preceding_powershell_table(self) -> None:
        """Verify extract_json_from_output extracts JSON when preceded by Format-Volume table output."""
        powershell_output = """
DriveLetter FileSystemLabel FileSystem DriveType HealthStatus OperationalStatus SizeRemaining     Size
----------- --------------- ---------- --------- ------------ ----------------- -------------     ----
E           MVX_BOOT        FAT32      Removable Healthy      OK                    255.94 MB 255.97 MB

DriveLetter FileSystemLabel FileSystem DriveType HealthStatus OperationalStatus SizeRemaining     Size
----------- --------------- ---------- --------- ------------ ----------------- -------------     ----
F           MVX_DATA        exFAT      Removable Healthy      OK                     58.48 GB  58.48 GB

{"boot_letter": "E", "data_letter": "F", "mode_executed": "EmptyGptAllocate", "verified": true}
"""
        res = WindowsPhysicalInstaller.extract_json_from_output(powershell_output)
        self.assertEqual(res["boot_letter"], "E")
        self.assertEqual(res["data_letter"], "F")
        self.assertEqual(res["mode_executed"], "EmptyGptAllocate")
        self.assertTrue(res["verified"])

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_resolve_disk_number_by_serial_success(self, mock_ps: MagicMock) -> None:
        """Verify resolve_disk_number_by_serial finds disk index dynamically by serial."""
        disks = [
            {"Number": 0, "FriendlyName": "Samsung SSD", "SerialNumber": "NVME12345", "Size": 512110190592},
            {"Number": 2, "FriendlyName": "SanDisk Cruzer Blade", "SerialNumber": "03026216101525041338", "Size": 63065554944},
        ]
        mock_ps.return_value = json.dumps(disks)

        disk_num, data = self.installer.resolve_disk_number_by_serial("03026216101525041338")
        self.assertEqual(disk_num, 2)
        self.assertEqual(data["FriendlyName"], "SanDisk Cruzer Blade")

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_resolve_disk_number_by_serial_not_found(self, mock_ps: MagicMock) -> None:
        """Verify resolve_disk_number_by_serial raises error if serial is not on bus."""
        disks = [{"Number": 0, "FriendlyName": "Samsung SSD", "SerialNumber": "NVME12345"}]
        mock_ps.return_value = json.dumps(disks)

        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.resolve_disk_number_by_serial("UNKNOWN_SERIAL")
        self.assertIn("not found on Windows storage bus", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_revalidate_formatted_partition_layout_success(self, mock_ps: MagicMock) -> None:
        """Verify revalidate_formatted_partition_layout confirms FAT32 MVX_BOOT and exFAT MVX_DATA."""
        layout_mock = {
            "disk_number": 1,
            "boot_letter": "E",
            "data_letter": "F",
            "boot_label": "MVX_BOOT",
            "boot_fs": "FAT32",
            "data_label": "MVX_DATA",
            "data_fs": "exFAT",
            "serial": "03026216101525041338",
            "model": "Cruzer Blade",
            "verified": True,
        }
        mock_ps.return_value = json.dumps(layout_mock)

        b_mount, d_mount, data = self.installer.revalidate_formatted_partition_layout(
            disk_number=1,
            expected_serial="03026216101525041338",
        )
        self.assertEqual(b_mount, "E:\\")
        self.assertEqual(d_mount, "F:\\")
        self.assertEqual(data["boot_label"], "MVX_BOOT")
        self.assertEqual(data["data_label"], "MVX_DATA")

    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_revalidate_formatted_partition_layout_mismatched_serial_aborts(self, mock_ps: MagicMock) -> None:
        """Verify revalidate_formatted_partition_layout aborts immediately if serial shifts."""
        layout_mock = {
            "disk_number": 1,
            "boot_letter": "E",
            "data_letter": "F",
            "serial": "WRONG_SERIAL",
        }
        mock_ps.return_value = json.dumps(layout_mock)

        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            self.installer.revalidate_formatted_partition_layout(
                disk_number=1,
                expected_serial="03026216101525041338",
            )
        self.assertIn("Disk serial mismatch", str(ctx.exception))

    @patch.object(WindowsPhysicalInstaller, "validate_host_environment")
    @patch.object(WindowsPhysicalInstaller, "resolve_disk_number_by_serial")
    @patch.object(WindowsPhysicalInstaller, "revalidate_formatted_partition_layout")
    @patch("installer.windows_physical_installer.BootloaderInstaller")
    @patch("installer.windows_physical_installer.FilesystemManager")
    @patch("installer.windows_physical_installer.InstallationVerifier")
    def test_resume_deployment_on_formatted_disk_never_calls_partitioning(
        self,
        mock_verifier_cls: MagicMock,
        mock_fs_cls: MagicMock,
        mock_boot_cls: MagicMock,
        mock_reval_layout: MagicMock,
        mock_resolve: MagicMock,
        mock_val_env: MagicMock,
    ) -> None:
        """Verify resume_deployment_on_formatted_disk preserves partitions and deploys files without reformatting."""
        mock_resolve.return_value = (1, {"FriendlyName": "SanDisk Cruzer Blade"})
        mock_reval_layout.return_value = ("E:\\", "F:\\", {"verified": True})

        mock_verifier = MagicMock()
        mock_verifier.verify_mounted_partitions.return_value = InstallationVerificationReport(
            target_path="F:\\",
            is_valid=True,
            checks=[CheckResult(name="Test", passed=True, message="OK")],
        )
        mock_verifier_cls.return_value = mock_verifier

        res = self.installer.resume_deployment_on_formatted_disk(device=self.sample_usb)

        self.assertTrue(res.success)
        mock_resolve.assert_called_once_with(self.sample_usb.serial_number)
        mock_reval_layout.assert_called_once_with(disk_number=1, expected_serial=self.sample_usb.serial_number)
        mock_boot_cls.return_value.deploy_all.assert_called_once()
        mock_fs_cls.initialize_data_partition.assert_called_once()
        mock_boot_cls.return_value.deploy_initial_menu.assert_called_once()



class TestPhysicalGUIIntegration(unittest.TestCase):
    """Test suite verifying UI interactions, two-phase confirmation dialog, and worker."""

    def setUp(self) -> None:
        self.device_service = DeviceService()
        self.patcher = patch.object(DeviceManager, "list_devices", return_value=[])
        self.mock_list_devices = self.patcher.start()
        self.sample_usb = DeviceInfo(
            device_id="\\\\.\\PhysicalDrive1",
            name="SanDisk Cruzer Blade",
            size_bytes=63065554944,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            is_system_disk=False,
            vendor="SanDisk",
            model="Cruzer Blade",
            serial_number="03026216101525041338",
        )

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_confirmation_dialog_typing_guard(self) -> None:
        """Verify Phase 2 modal dialog requires exact device name or YES before enabling confirm button."""
        plan = PhysicalDeviceWorkflow(allow_physical_formatting=True).generate_plan(self.sample_usb)
        dialog = PhysicalConfirmationDialog(plan)

        # Initially disabled
        self.assertFalse(dialog.btn_confirm.isEnabled())

        # Random text keeps it disabled
        dialog.input_confirm.setText("Random Text")
        self.assertFalse(dialog.btn_confirm.isEnabled())

        # Typing 'YES' enables it
        dialog.input_confirm.setText("YES")
        self.assertTrue(dialog.btn_confirm.isEnabled())

        # Typing exact drive name enables it
        dialog.input_confirm.setText("SanDisk Cruzer Blade")
        self.assertTrue(dialog.btn_confirm.isEnabled())

    def test_screen_unlock_physical_formatting(self) -> None:
        """Verify checking unlock policy enables radio_physical and controls."""
        screen = USBInstallerScreen(self.device_service, allow_physical_formatting=False)
        screen.device_manager.list_devices = MagicMock(return_value=[])
        self.assertFalse(screen.radio_physical.isEnabled())

        # Unlock policy
        screen.set_allow_physical_formatting(True)
        self.assertTrue(screen.radio_physical.isEnabled())
        self.assertTrue(screen.allow_physical_formatting)

        # Relock policy
        screen.set_allow_physical_formatting(False)
        self.assertFalse(screen.radio_physical.isEnabled())
        self.assertTrue(screen.radio_virtual.isChecked())

    def test_physical_device_selection_updates_preview(self) -> None:
        """Verify selecting physical device populates details card and updates preview layout."""
        self.mock_list_devices.return_value = [self.sample_usb]
        screen = USBInstallerScreen(self.device_service, allow_physical_formatting=True)
        screen.radio_physical.setChecked(True)

        # Index 0 is placeholder
        self.assertEqual(screen.physical_device_combo.count(), 2)
        self.assertIsNone(screen.physical_device_combo.itemData(0))

        # Select detected device
        screen.physical_device_combo.setCurrentIndex(1)
        self.assertFalse(screen.physical_details_card.isHidden())
        self.assertIn("SanDisk Cruzer Blade", screen.pdc_details.text())
        self.assertIn("58.73 GB", screen.pdc_details.text())
        self.assertIn("MVX_BOOT", screen.preview_details.text())
        self.assertIn("MVX_DATA", screen.preview_details.text())


if __name__ == "__main__":
    unittest.main()
