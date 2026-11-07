"""Windows Physical USB Storage Installation Backend for MVentX.

Implements real, low-level physical disk preparation on Windows 10/11:
1. Validates host administrative privileges and Windows platform.
2. Revalidates target hardware identity and fingerprint immediately before writing.
3. Enforces system disk safety filters (rejects system, boot, and pagefile disks).
4. Safely dismounts existing volumes via Win32 FSCTL_LOCK_VOLUME / FSCTL_DISMOUNT_VOLUME.
5. Clears existing partition structures and initializes GPT.
6. Allocates Partition 1 (MVX_BOOT, FAT32 ESP) and Partition 2 (MVX_DATA, exFAT Basic Data).
7. Formats filesystems with correct volume labels and cluster alignment.
8. Resolves assigned drive letters / mount points.
9. Deploys GRUB 2.12 EFI binaries, modules, fonts, and configurations.
10. Initializes the MVentX directory skeleton and default mventx.json.
11. Verifies completed installation using InstallationVerifier.
12. Restores accessibility and handles cleanup upon failure.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.host.device_identity import DeviceIdentity
from core.host.host_platform import HostPlatform
from core.host.windows.windows_device_provider import normalize_serial_number
from core.storage.device_info import DeviceInfo, DeviceType
from core.storage.device_manager import DeviceManager, DeviceSafetyError
from installer.bootloader_installer import BootloaderInstaller
from installer.filesystem_manager import FilesystemManager
from installer.installation_recovery import (
    InstallationRecoveryService,
    InstallationStateDiagnostic,
    USBInstallationState,
)
from installer.installation_service import InstallationResult
from installer.installation_verifier import InstallationVerificationReport, InstallationVerifier
from installer.partition_planner import (
    DEFAULT_ESP_SIZE_MB,
    MIN_DISK_CAPACITY_MB,
    PartitionPlan,
    PartitionPlanner,
)

logger = logging.getLogger(__name__)

# Default ESP partition size for physical USB media (256 MiB recommended for diagnostics buffer)
DEFAULT_PHYSICAL_ESP_SIZE_MB: int = 256

ProgressCallback = Callable[[int, int, str], None]


class WindowsPhysicalInstallationError(Exception):
    """Raised when physical disk preparation fails on Windows."""


class WindowsVolumeLockedError(WindowsPhysicalInstallationError):
    """Raised when a disk volume cannot be locked or dismounted because files or processes are using it."""


class WindowsPhysicalInstaller:
    """Manages physical disk preparation, formatting, and bootloader deployment on Windows."""

    def __init__(
        self,
        device_manager: Optional[DeviceManager] = None,
        allow_physical_formatting: bool = False,
    ) -> None:
        self.allow_physical_formatting = allow_physical_formatting
        self.device_manager = device_manager or DeviceManager(allow_physical_formatting=allow_physical_formatting)
        self.recovery_service = InstallationRecoveryService()

    # --------------------------------------------------------------------------
    # 1. Environment & Privilege Verification
    # --------------------------------------------------------------------------

    @classmethod
    def validate_host_environment(cls) -> None:
        """Verify operating system and elevation requirements."""
        if not HostPlatform.is_windows():
            raise WindowsPhysicalInstallationError(
                "WindowsPhysicalInstaller is only supported on Windows host platforms."
            )

        if not HostPlatform.is_elevated():
            raise PermissionError(
                "PHYSICAL DISK ACCESS DENIED: Modifying physical storage devices requires Administrator privileges.\n"
                "Please relaunch MVentX as Administrator (Run as administrator)."
            )

    @classmethod
    def extract_disk_number(cls, device_id: str) -> int:
        """Extract Windows physical disk index from device identifier."""
        # Handles '\\.\PHYSICALDRIVE1', '\\.\PhysicalDrive1', 'PhysicalDrive1', '1', 'Disk 1'
        match = re.search(r"(?:physicaldrive|disk\s*)?(\d+)", device_id, re.IGNORECASE)
        if match:
            return int(match.group(1))
        raise ValueError(f"Cannot resolve Windows physical disk number from device_id: '{device_id}'")

    @classmethod
    def parse_partition_json(cls, raw_json: str) -> List[Dict[str, Any]]:
        """Parse PowerShell ConvertTo-Json output into a standardized list of dicts.

        Handles:
        - Empty string, whitespace, or "null" -> [] (zero partitions)
        - "[]" -> [] (empty array)
        - Single dict {"PartitionNumber": 1} -> [{"PartitionNumber": 1}]
        - Array of dicts [{"PartitionNumber": 1}, ...] -> [{"PartitionNumber": 1}, ...]
        - JSON decoding errors -> logs warning and returns []
        """
        cleaned = (raw_json or "").strip()
        if not cleaned or cleaned == "null":
            return []
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.debug("Failed parsing partition JSON: %s (Raw: '%s')", exc, cleaned)
            return []
        if data is None:
            return []
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    @classmethod
    def extract_json_from_output(cls, raw_output: str) -> Dict[str, Any]:
        """Extract and parse a JSON dictionary from PowerShell output.

        Handles cases where PowerShell cmdlets (such as Format-Volume, Set-Partition,
        or Write-Host) output table headers, status lines, or banner text before or
        after the ConvertTo-Json payload.
        """
        cleaned = (raw_output or "").strip()
        if not cleaned:
            raise WindowsPhysicalInstallationError("PowerShell output was empty.")

        # 1. Direct parse attempt
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        # 2. Extract JSON using outermost boundary braces
        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            candidate = cleaned[start_idx : end_idx + 1]
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        # 3. Line-by-line fallback for single-line JSON records
        for line in reversed(cleaned.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    if isinstance(data, dict):
                        return data
                except json.JSONDecodeError:
                    continue

        raise WindowsPhysicalInstallationError(
            f"Failed parsing JSON dictionary from PowerShell output. Output snippet:\n{cleaned[:300]}"
        )

    # --------------------------------------------------------------------------
    # 2. PowerShell Command Execution Subsystem & AST Syntax Validator
    # --------------------------------------------------------------------------

    @classmethod
    def validate_powershell_syntax(cls, script: str) -> Tuple[bool, List[str]]:
        """Validate PowerShell script syntax using AST parser and regex checks.

        Returns:
            Tuple of (is_valid: bool, errors: List[str]).
        """
        errors: List[str] = []

        # 1. Fast regex pre-check for illegal drive-colon interpolation like "$bootLetter:"
        colon_matches = re.findall(r"\$([a-zA-Z_][a-zA-Z0-9_]*):(?![a-zA-Z_])", script)
        for var_name in colon_matches:
            errors.append(
                f"Invalid variable reference '${var_name}:'. Colon was not followed by a variable name. "
                f"Use '${{{var_name}}}:' instead."
            )

        # 2. Check on Windows with PowerShell Language.Parser
        if HostPlatform.is_windows():
            try:
                import base64
                target_b64 = base64.b64encode(script.encode("utf-16le")).decode("ascii")
                validator_code = (
                    f"$raw = [System.Text.Encoding]::Unicode.GetString([System.Convert]::FromBase64String('{target_b64}')); "
                    f"$toks = $null; $parseErrors = $null; "
                    f"$null = [System.Management.Automation.Language.Parser]::ParseInput($raw, [ref]$toks, [ref]$parseErrors); "
                    f"if ($parseErrors -and $parseErrors.Count -gt 0) {{ "
                    f"    @($parseErrors) | ForEach-Object {{ $_.Message }} | ConvertTo-Json -Compress "
                    f"}} else {{ '[]' }}"
                )
                res = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy", "Bypass",
                        "-Command", validator_code,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if res.returncode == 0 and res.stdout.strip():
                    try:
                        parsed = json.loads(res.stdout.strip())
                        if isinstance(parsed, str) and parsed:
                            errors.append(parsed)
                        elif isinstance(parsed, list):
                            errors.extend([str(m) for m in parsed if m])
                    except json.JSONDecodeError:
                        pass
            except Exception as exc:
                logger.debug("PowerShell AST parser check encountered an exception: %s", exc)

        return (len(errors) == 0, errors)

    def run_powershell(self, script: str, timeout: int = 120) -> str:
        """Execute a PowerShell command string with error checking and logging."""
        logger.debug("Executing PowerShell: %s", script)

        # Immediate regex check to prevent drive-colon errors before running
        colon_matches = re.findall(r"\$([a-zA-Z_][a-zA-Z0-9_]*):(?![a-zA-Z_])", script)
        if colon_matches:
            err = (
                f"Invalid variable reference '${colon_matches[0]}:'. Colon was not followed by a variable name. "
                f"Use '${{{colon_matches[0]}}}:' instead."
            )
            logger.error("PowerShell syntax error: %s", err)
            raise WindowsPhysicalInstallationError(f"PowerShell syntax error: {err}")

        cmd = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command", script,
        ]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if res.returncode != 0:
                raw_err = res.stderr.strip()
                raw_out = res.stdout.strip()
                err_msg = raw_err or raw_out
                if not err_msg:
                    snippet = script.strip().splitlines()[0] if script.strip() else ""
                    err_msg = f"PowerShell exited with code {res.returncode} (Command: '{snippet}')"
                logger.error("PowerShell execution failed: %s", err_msg)
                raise WindowsPhysicalInstallationError(f"PowerShell disk operation failed: {err_msg}")
            return res.stdout.strip()
        except subprocess.TimeoutExpired as exc:
            raise WindowsPhysicalInstallationError(f"PowerShell disk operation timed out after {timeout}s.") from exc
        except Exception as exc:
            if not isinstance(exc, WindowsPhysicalInstallationError):
                raise WindowsPhysicalInstallationError(f"PowerShell invocation error: {exc}") from exc
            raise

    # --------------------------------------------------------------------------
    # 3. Disk Pre-Flight & Fingerprint Revalidation
    # --------------------------------------------------------------------------

    def resolve_disk_number_by_serial(self, serial_number: str) -> Tuple[int, Dict[str, Any]]:
        """Resolve current Windows physical disk number for a given hardware serial number.

        Never assumes a fixed disk number (e.g. Disk 1).
        Queries all disks via Get-Disk and matches normalized serial numbers.
        """
        norm_target = normalize_serial_number(serial_number)
        if not norm_target:
            raise ValueError("Cannot resolve disk without a valid hardware serial number.")

        ps_script = (
            "Get-Disk | Select-Object Number, FriendlyName, Model, SerialNumber, BusType, Size, "
            "PartitionStyle, OperationalStatus, IsBoot, IsSystem, IsReadOnly | ConvertTo-Json -Compress"
        )
        raw = self.run_powershell(ps_script, timeout=15)
        disks_data = self.parse_partition_json(raw)

        for d in disks_data:
            live_sn = normalize_serial_number(d.get("SerialNumber"))
            if live_sn and live_sn == norm_target:
                disk_num = int(d["Number"])
                logger.info(
                    "Resolved target serial '%s' to physical Disk %d (%s, %s bytes)",
                    norm_target,
                    disk_num,
                    d.get("FriendlyName"),
                    d.get("Size"),
                )
                return disk_num, d

        raise WindowsPhysicalInstallationError(
            f"Physical storage device with serial '{serial_number}' not found on Windows storage bus."
        )

    def revalidate_physical_disk(
        self,
        device: DeviceInfo,
        expected_fingerprint: str,
    ) -> Tuple[int, Dict[str, Any]]:
        """Query storage bus to ensure disk is still present, matches identity, and is not system."""
        self.validate_host_environment()

        disk_num = self.extract_disk_number(device.device_id)

        # Enforce system disk protection
        self.device_manager.assert_safe_target(device, for_installation=True)

        # Query disk from Windows storage bus with retry loop for bus settling
        query_script = (
            f"$d = $null; "
            f"for ($i = 0; $i -lt 4; $i++) {{ $d = Get-Disk -Number {disk_num} -ErrorAction SilentlyContinue; if ($d) {{ break }}; Start-Sleep -Milliseconds 400 }}; "
            f"if (-not $d) {{ throw 'Physical disk {disk_num} not found or not responding on Windows storage bus.' }}; "
            "$d | Select-Object Number, FriendlyName, Manufacturer, Model, SerialNumber, BusType, "
            "Size, LogicalSectorSize, PartitionStyle, IsBoot, IsSystem, IsReadOnly | "
            "ConvertTo-Json -Compress"
        )
        output = self.run_powershell(query_script, timeout=15)
        if not output:
            raise WindowsPhysicalInstallationError(
                f"Target storage device '{device.name}' (Disk {disk_num}) vanished from storage bus."
            )

        disk_data = self.extract_json_from_output(output)

        # 1. System disk guard
        if disk_data.get("IsSystem") or disk_data.get("IsBoot"):
            raise DeviceSafetyError(
                f"REJECTED: Disk {disk_num} ({disk_data.get('FriendlyName')}) is marked as an active Windows system/boot disk."
            )

        # 2. BusType guard (must be USB)
        bus_type = str(disk_data.get("BusType", "")).upper()
        friendly = str(disk_data.get("FriendlyName", "")).upper()
        if bus_type != "USB" and "USB" not in friendly and not device.is_removable:
            raise DeviceSafetyError(
                f"REJECTED: Disk {disk_num} BusType is '{bus_type}'. Only USB storage devices can be formatted."
            )

        # 3. Read-only guard
        if disk_data.get("IsReadOnly"):
            raise DeviceSafetyError(
                f"REJECTED: Disk {disk_num} is hardware write-protected or marked read-only."
            )

        # 4. Partition system volume check (reject if hosting C:\; empty partition set is valid)
        part_script = (
            f"$diskNum = {disk_num}; "
            f"$parts = $null; "
            f"try {{ $parts = Get-Partition -DiskNumber $diskNum -ErrorAction Stop }} "
            f"catch {{ if ($_.FullyQualifiedErrorId -match 'CmdletizationQuery_NotFound' -or $_.CategoryInfo.Category -eq 'ObjectNotFound') {{ $parts = $null }} else {{ throw $_ }} }}; "
            f"if ($parts) {{ @($parts) | Select-Object PartitionNumber, DriveLetter, IsSystem, IsBoot | ConvertTo-Json -Compress }} else {{ '[]' }}; "
            f"exit 0"
        )
        part_out = self.run_powershell(part_script, timeout=10)
        parts_list = self.parse_partition_json(part_out)
        sys_drive = os.environ.get("SystemDrive", "C:").upper().rstrip("\\")
        for p in parts_list:
            dl = str(p.get("DriveLetter") or "").upper()
            if dl and f"{dl}:" == sys_drive:
                raise DeviceSafetyError(
                    f"REJECTED: Disk {disk_num} contains partition mounted as system drive {sys_drive}."
                )
            if p.get("IsSystem") or p.get("IsBoot"):
                raise DeviceSafetyError(
                    f"REJECTED: Disk {disk_num} contains an active Windows boot/system partition."
                )

        # 5. Fingerprint & hardware serial verification
        live_serial = normalize_serial_number(disk_data.get("SerialNumber"))
        expected_serial = normalize_serial_number(device.serial_number)

        if expected_serial and live_serial and expected_serial != live_serial:
            raise WindowsPhysicalInstallationError(
                f"Target hardware serial changed before write!\n"
                f"Expected: '{expected_serial}', Current on bus: '{live_serial}'... Aborting."
            )

        if live_serial and not device.serial_number:
            device.serial_number = live_serial

        current_identity = DeviceIdentity.from_device_info(device)
        current_fp = current_identity.compute_fingerprint()
        if expected_fingerprint and current_fp != expected_fingerprint:
            raise WindowsPhysicalInstallationError(
                f"Target device identity changed between confirmation and execution.\n"
                f"Expected: {expected_fingerprint[:16]}..., Current: {current_fp[:16]}... Aborting."
            )

        return disk_num, disk_data

    # --------------------------------------------------------------------------
    # 4. Volume Enumeration & Safe Win32 Dismount Subsystem (PowerShell 5.1 Compatible)
    # --------------------------------------------------------------------------

    def enumerate_disk_volume_targets(self, disk_number: int) -> List[Dict[str, Any]]:
        """Enumerate all active volume targets and drive letters on a physical disk.

        Does not assume drive letter E: or any specific letter.
        Queries the Windows partition table for all drive letters and volume paths.
        """
        targets: List[Dict[str, Any]] = []
        ps_script = (
            f"$diskNum = {disk_number}; "
            f"$parts = $null; "
            f"try {{ $parts = Get-Partition -DiskNumber $diskNum -ErrorAction Stop }} "
            f"catch {{ if ($_.FullyQualifiedErrorId -match 'CmdletizationQuery_NotFound' -or $_.CategoryInfo.Category -eq 'ObjectNotFound') {{ $parts = $null }} else {{ throw $_ }} }}; "
            f"if ($parts) {{ @($parts) | Select-Object PartitionNumber, DriveLetter, AccessPaths | ConvertTo-Json -Compress }} else {{ '[]' }}; "
            f"exit 0"
        )
        try:
            raw_out = self.run_powershell(ps_script, timeout=10)
            part_list = self.parse_partition_json(raw_out)
            for p in part_list:
                letter = p.get("DriveLetter")
                if letter and str(letter).strip():
                    dl = str(letter).strip().upper()
                    targets.append({
                        "display_name": f"{dl}:",
                        "target_path": f"\\\\.\\{dl}:",
                        "partition_number": p.get("PartitionNumber", 1),
                    })
                else:
                    access_paths = p.get("AccessPaths") or []
                    if isinstance(access_paths, str):
                        access_paths = [access_paths]
                    for ap in access_paths:
                        ap_str = str(ap).strip().rstrip("\\")
                        if "Volume{" in ap_str:
                            targets.append({
                                "display_name": ap_str,
                                "target_path": ap_str,
                                "partition_number": p.get("PartitionNumber", 1),
                            })
        except Exception as exc:
            logger.debug("Failed querying partition volume targets on Disk %d: %s", disk_number, exc)

        return targets

    @classmethod
    def dismount_volume_win32(cls, target_path: str, display_name: str) -> None:
        """Lock and dismount a Windows volume handle via Win32 DeviceIoControl.

        Sends:
        1. FSCTL_LOCK_VOLUME (0x00090018)
        2. FSCTL_DISMOUNT_VOLUME (0x00090020)
        3. FSCTL_UNLOCK_VOLUME (0x0009001C)

        Detects locked volumes and open file conflicts explicitly (Win32 errors 5, 32, 33).
        Never forces process termination.
        """
        if not HostPlatform.is_windows():
            # Non-Windows stub for testing / compatibility
            return

        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32

        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        FSCTL_LOCK_VOLUME = 0x00090018
        FSCTL_DISMOUNT_VOLUME = 0x00090020
        FSCTL_UNLOCK_VOLUME = 0x0009001C
        INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

        handle = kernel32.CreateFileW(
            target_path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )

        if handle == INVALID_HANDLE_VALUE or handle == 0:
            err = kernel32.GetLastError()
            if err == 2:  # ERROR_FILE_NOT_FOUND (volume not mounted or already released)
                return
            if err in (5, 32, 33):  # ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
                raise WindowsVolumeLockedError(
                    f"Volume '{display_name}' ({target_path}) is locked or in use by another process "
                    f"(Win32 error {err}). Please close all open files, File Explorer windows, "
                    "or applications using this drive before proceeding."
                )
            raise WindowsPhysicalInstallationError(
                f"Failed to open volume '{display_name}' ({target_path}) for dismount: Win32 error {err}."
            )

        try:
            bytes_returned = wintypes.DWORD(0)

            # 1. Attempt exclusive lock to verify no open handles remain
            lock_ok = kernel32.DeviceIoControl(
                handle,
                FSCTL_LOCK_VOLUME,
                None, 0,
                None, 0,
                ctypes.byref(bytes_returned),
                None,
            )

            if not lock_ok:
                err = kernel32.GetLastError()
                if err in (5, 32, 33):
                    raise WindowsVolumeLockedError(
                        f"Volume '{display_name}' ({target_path}) is locked or in use by another process "
                        f"(Win32 error {err}). Please close all open files, File Explorer windows, "
                        "or applications using this drive before proceeding."
                    )
                raise WindowsPhysicalInstallationError(
                    f"Failed to lock volume '{display_name}' ({target_path}) for dismount: Win32 error {err}."
                )

            # 2. Issue FSCTL_DISMOUNT_VOLUME
            dismount_ok = kernel32.DeviceIoControl(
                handle,
                FSCTL_DISMOUNT_VOLUME,
                None, 0,
                None, 0,
                ctypes.byref(bytes_returned),
                None,
            )

            if not dismount_ok:
                err = kernel32.GetLastError()
                raise WindowsPhysicalInstallationError(
                    f"Failed to dismount volume '{display_name}' ({target_path}): Win32 error {err}."
                )

            # 3. Release lock
            kernel32.DeviceIoControl(
                handle,
                FSCTL_UNLOCK_VOLUME,
                None, 0,
                None, 0,
                ctypes.byref(bytes_returned),
                None,
            )

        finally:
            kernel32.CloseHandle(handle)

    def dismount_disk_volumes(self, disk_number: int) -> List[str]:
        """Enumerate and safely dismount all mounted filesystem volumes on the physical disk.

        Uses Win32 DeviceIoControl with FSCTL_LOCK_VOLUME and FSCTL_DISMOUNT_VOLUME.
        Checks for locked-volume or open-file conflicts explicitly.
        Never terminates unrelated processes.
        Aborts if any volume fails to dismount safely.

        Returns:
            List of successfully dismounted volume identifiers (e.g. ['E:', 'F:']).
        """
        targets = self.enumerate_disk_volume_targets(disk_number)
        dismounted: List[str] = []

        for tgt in targets:
            display_name = tgt["display_name"]
            target_path = tgt["target_path"]
            logger.info("Attempting safe dismount of volume %s (%s) on Disk %d...", display_name, target_path, disk_number)
            self.dismount_volume_win32(target_path=target_path, display_name=display_name)
            dismounted.append(display_name)

        return dismounted

    # --------------------------------------------------------------------------
    # 5. Pre-Destructive Strict Revalidation Gate
    # --------------------------------------------------------------------------

    def revalidate_disk_identity_strict(
        self,
        device: DeviceInfo,
        expected_fingerprint: str,
        disk_number: int,
    ) -> Dict[str, Any]:
        """Perform strict pre-destructive revalidation of all hardware identity fields.

        Verifies immediately before executing destructive commands:
        1. Physical disk path (e.g. '\\\\.\\PHYSICALDRIVE1')
        2. Hardware model
        3. Hardware serial number
        4. Bus type ('USB')
        5. Total capacity (bytes)
        6. Canonical SHA-256 fingerprint
        """
        query_script = (
            f"$d = $null; "
            f"for ($i = 0; $i -lt 4; $i++) {{ $d = Get-Disk -Number {disk_number} -ErrorAction SilentlyContinue; if ($d) {{ break }}; Start-Sleep -Milliseconds 400 }}; "
            f"if (-not $d) {{ throw 'Physical disk {disk_number} disconnected or not responding on Windows storage bus.' }}; "
            "$d | Select-Object Number, FriendlyName, Manufacturer, Model, SerialNumber, BusType, "
            "Size, LogicalSectorSize, PartitionStyle, IsBoot, IsSystem, IsReadOnly | "
            "ConvertTo-Json -Compress"
        )
        output = self.run_powershell(query_script, timeout=15)
        if not output:
            raise WindowsPhysicalInstallationError(
                f"Target storage device '{device.name}' (Disk {disk_number}) disconnected or vanished from storage bus!"
            )

        disk_data = self.extract_json_from_output(output)

        # 1. Physical disk path verification
        expected_path = f"\\\\.\\PHYSICALDRIVE{disk_number}".upper()
        if device.device_id.upper() != expected_path:
            # If disk index resolved dynamically by serial differs from cached ID, log and sync
            logger.info("Syncing device_id from '%s' to active disk path '%s'.", device.device_id, expected_path)
            device.device_id = f"\\\\.\\PHYSICALDRIVE{disk_number}"

        # 2. System disk guard
        if disk_data.get("IsSystem") or disk_data.get("IsBoot"):
            raise DeviceSafetyError(
                f"REJECTED: Disk {disk_number} ({disk_data.get('FriendlyName')}) is marked as an active system/boot disk."
            )

        # 3. Bus type guard (must be USB)
        bus_type = str(disk_data.get("BusType", "")).upper()
        friendly = str(disk_data.get("FriendlyName", "")).upper()
        if bus_type != "USB" and "USB" not in friendly and not device.is_removable:
            raise DeviceSafetyError(
                f"REJECTED: Disk {disk_number} BusType is '{bus_type}'. Only USB storage devices can be formatted."
            )

        # 4. Hardware model verification
        bus_model = str(disk_data.get("Model") or disk_data.get("FriendlyName") or "").strip()
        if device.model and bus_model:
            dev_m = device.model.upper()
            bus_m = bus_model.upper()
            if dev_m not in bus_m and bus_m not in dev_m:
                raise WindowsPhysicalInstallationError(
                    f"Target disk model mismatch! Expected '{device.model}', found '{bus_model}'."
                )

        # 5. Hardware serial verification
        live_serial = normalize_serial_number(disk_data.get("SerialNumber"))
        expected_serial = normalize_serial_number(device.serial_number)
        if expected_serial and live_serial and expected_serial != live_serial:
            raise WindowsPhysicalInstallationError(
                f"Target hardware serial changed before write!\n"
                f"Expected: '{expected_serial}', Current on bus: '{live_serial}'... Aborting."
            )

        # 6. Capacity verification
        bus_size = int(disk_data.get("Size") or 0)
        if device.size_bytes and bus_size:
            if abs(device.size_bytes - bus_size) > (1024 * 1024):  # Tolerates 1 MiB alignment delta
                raise WindowsPhysicalInstallationError(
                    f"Target disk capacity mismatch! Expected {device.size_bytes} bytes, found {bus_size} bytes."
                )

        # 7. Canonical SHA-256 fingerprint verification
        current_identity = DeviceIdentity.from_device_info(device)
        current_fp = current_identity.compute_fingerprint()
        if expected_fingerprint and current_fp != expected_fingerprint:
            raise WindowsPhysicalInstallationError(
                f"Target device identity changed between confirmation and execution.\n"
                f"Expected: {expected_fingerprint[:16]}..., Current: {current_fp[:16]}... Aborting."
            )

        return disk_data

    # --------------------------------------------------------------------------
    # 6. Physical Disk Partitioning, State-Aware GPT, and Layout Verification
    # --------------------------------------------------------------------------

    def initialize_or_verify_gpt(self, disk_number: int) -> Dict[str, Any]:
        """State-aware GPT initialization and validation on physical disk.

        1. Queries disk state and partition count after Clear-Disk.
        2. If RAW: invokes Initialize-Disk -PartitionStyle GPT.
        3. If GPT and 0 partitions: skips Initialize-Disk (avoids 'already initialized' failure).
        4. If GPT and >0 partitions: aborts to protect existing partitions.
        5. If MBR: aborts (automatic conversion not permitted).
        6. Confirms disk is GPT and Online.

        Returns:
            Dict containing 'partition_style', 'partition_count', 'initialized_called', 'status'.
        """
        ps_query = (
            f"$d = $null; "
            f"for ($i = 0; $i -lt 4; $i++) {{ $d = Get-Disk -Number {disk_number} -ErrorAction SilentlyContinue; if ($d) {{ break }}; Start-Sleep -Milliseconds 400 }}; "
            f"if (-not $d) {{ throw 'Physical disk {disk_number} vanished or not responding on Windows storage bus.' }}; "
            f"$p = $null; "
            f"try {{ $p = Get-Partition -DiskNumber {disk_number} -ErrorAction Stop | Where-Object {{ $_.Type -ne 'Reserved' }} }} "
            f"catch {{ if ($_.FullyQualifiedErrorId -notmatch 'CmdletizationQuery_NotFound' -and $_.CategoryInfo.Category -ne 'ObjectNotFound') {{ throw $_ }} }}; "
            "$cnt = if ($p) { @($p).Count } else { 0 }; "
            "[PSCustomObject]@{"
            "PartitionStyle = [string]$d.PartitionStyle; "
            "OperationalStatus = [string]$d.OperationalStatus; "
            "PartitionCount = $cnt; "
            "IsBoot = [bool]$d.IsBoot; "
            "IsSystem = [bool]$d.IsSystem"
            "} | ConvertTo-Json -Compress"
        )
        raw = self.run_powershell(ps_query, timeout=15)
        if not raw:
            raise WindowsPhysicalInstallationError(
                f"Target storage disk (Disk {disk_number}) vanished or disconnected from storage bus."
            )

        data = self.extract_json_from_output(raw)
        part_style = str(data.get("PartitionStyle", "")).upper()
        part_count = int(data.get("PartitionCount", 0))

        if part_style == "RAW":
            # Disk is RAW: initialize as GPT
            init_script = (
                f"$d = Get-Disk -Number {disk_number} -ErrorAction Stop; "
                f"Initialize-Disk -Number {disk_number} -PartitionStyle GPT -ErrorAction Stop; "
                f"$dAfter = Get-Disk -Number {disk_number}; "
                "[PSCustomObject]@{"
                "PartitionStyle = [string]$dAfter.PartitionStyle; "
                "OperationalStatus = [string]$dAfter.OperationalStatus"
                "} | ConvertTo-Json -Compress"
            )
            init_raw = self.run_powershell(init_script, timeout=20)
            init_data = self.extract_json_from_output(init_raw) if init_raw else {}
            final_style = str(init_data.get("PartitionStyle", "")).upper()
            if final_style != "GPT":
                raise WindowsPhysicalInstallationError(
                    f"Failed to initialize Disk {disk_number} as GPT. Current style: '{final_style}'."
                )
            logger.info("Disk %d was RAW. Successfully initialized GPT partition style.", disk_number)
            return {
                "status": "initialized",
                "partition_style": "GPT",
                "partition_count": 0,
                "initialized_called": True,
            }

        elif part_style == "GPT":
            if part_count > 0:
                raise WindowsPhysicalInstallationError(
                    f"Disk {disk_number} reports GPT partition style but still contains {part_count} "
                    "partition(s) after Clear-Disk. Aborting to protect data."
                )
            op_status = str(data.get("OperationalStatus", ""))
            if "ONLINE" not in op_status.upper():
                raise WindowsPhysicalInstallationError(
                    f"Disk {disk_number} is GPT but not Online (Status: '{op_status}')."
                )
            logger.info("Disk %d is already initialized as GPT with 0 partitions. Ready for partition allocation.", disk_number)
            return {
                "status": "already_gpt",
                "partition_style": "GPT",
                "partition_count": 0,
                "initialized_called": False,
            }

        elif part_style == "MBR":
            raise WindowsPhysicalInstallationError(
                f"Disk {disk_number} reports MBR partition style after Clear-Disk. "
                "Aborting: automatic MBR conversion without a verified conversion procedure is not permitted."
            )

        else:
            raise WindowsPhysicalInstallationError(
                f"Disk {disk_number} reports unsupported PartitionStyle '{part_style}' after Clear-Disk. Aborting."
            )

    def verify_partition_layout(
        self,
        disk_number: int,
        esp_size_mb: int = DEFAULT_ESP_SIZE_MB,
    ) -> Dict[str, Any]:
        """Verify partition GUIDs, sizes, alignment, and filesystem labels on Disk."""
        ps_verify = f"""
$ErrorActionPreference = 'Stop'
$diskNum = {disk_number}
$espGptType = '{{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}}'
$dataGptType = '{{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}}'

$parts = Get-Partition -DiskNumber $diskNum -ErrorAction Stop | Where-Object {{ $_.Type -ne 'Reserved' }} | Sort-Object PartitionNumber
$partList = @($parts)
if ($partList.Count -lt 2) {{
    throw "Expected at least 2 partitions on Disk $diskNum, found $($partList.Count)."
}}

$p1 = $partList[0]
$p2 = $partList[1]

# GUID verification
if ($p1.GptType -ne $espGptType) {{
    throw "Partition 1 GptType mismatch: expected $espGptType, got $($p1.GptType)"
}}
if ($p2.GptType -ne $dataGptType) {{
    throw "Partition 2 GptType mismatch: expected $dataGptType, got $($p2.GptType)"
}}

# Alignment check (1 MiB boundary = 1048576 bytes)
if (($p1.Offset % 1048576) -ne 0) {{
    throw "Partition 1 is not aligned to 1 MiB boundary (Offset: $($p1.Offset))"
}}
if (($p2.Offset % 1048576) -ne 0) {{
    throw "Partition 2 is not aligned to 1 MiB boundary (Offset: $($p2.Offset))"
}}

# Size check
$minEspBytes = {esp_size_mb} * 1MB
if ($p1.Size -lt $minEspBytes) {{
    throw "Partition 1 size ($($p1.Size)) is smaller than requested ESP size ($minEspBytes)"
}}

# Volume label check
$bLetter = [string]$p1.DriveLetter
$dLetter = [string]$p2.DriveLetter
$vol1 = if ($bLetter) {{ Get-Volume -DriveLetter $bLetter -ErrorAction SilentlyContinue }} else {{ $null }}
$vol2 = if ($dLetter) {{ Get-Volume -DriveLetter $dLetter -ErrorAction SilentlyContinue }} else {{ $null }}

[PSCustomObject]@{{
    p1_number = $p1.PartitionNumber
    p1_guid = $p1.GptType
    p1_offset = $p1.Offset
    p1_size = $p1.Size
    p1_letter = $bLetter
    p1_label = if ($vol1) {{ $vol1.FileSystemLabel }} else {{ '' }}
    p1_fs = if ($vol1) {{ $vol1.FileSystem }} else {{ '' }}
    p2_number = $p2.PartitionNumber
    p2_guid = $p2.GptType
    p2_offset = $p2.Offset
    p2_size = $p2.Size
    p2_letter = $dLetter
    p2_label = if ($vol2) {{ $vol2.FileSystemLabel }} else {{ '' }}
    p2_fs = if ($vol2) {{ $vol2.FileSystem }} else {{ '' }}
    verified = $true
}} | ConvertTo-Json -Compress
"""
        raw = self.run_powershell(ps_verify, timeout=20)
        return self.extract_json_from_output(raw)

    def diagnose_physical_disk(self, disk_number: int) -> InstallationStateDiagnostic:
        """Query physical disk and classify its installation state according to the 7-state model.

        Inspects:
        - Disk partition style (GPT, MBR, RAW) and operational status.
        - Existing partitions, types, offsets, sizes, and drive letters.
        - Existing filesystem volumes and volume labels (MVX_BOOT, MVX_DATA).
        - Bootloader files and directory skeleton if partitions are mounted.

        Returns an InstallationStateDiagnostic with recommended recovery path.
        """
        ps_query = f"""
$diskNum = {disk_number}
$d = Get-Disk -Number $diskNum -ErrorAction SilentlyContinue
if (-not $d) {{
    [PSCustomObject]@{{
        found = $false
        error = "Disk $diskNum not found"
    }} | ConvertTo-Json -Compress
    exit 0
}}

$parts = $null
try {{
    $parts = Get-Partition -DiskNumber $diskNum -ErrorAction Stop | Where-Object {{ $_.Type -ne 'Reserved' }} | Sort-Object PartitionNumber
}} catch {{
    if ($_.FullyQualifiedErrorId -notmatch 'CmdletizationQuery_NotFound' -and $_.CategoryInfo.Category -ne 'ObjectNotFound') {{
        throw $_
    }}
}}

$partList = @()
if ($parts) {{
    foreach ($p in @($parts)) {{
        $bLetter = if ($p.DriveLetter) {{ [string]$p.DriveLetter }} else {{ '' }}
        $vol = if ($bLetter) {{ Get-Volume -DriveLetter $bLetter -ErrorAction SilentlyContinue }} else {{ $null }}
        $partList += [PSCustomObject]@{{
            PartitionNumber = $p.PartitionNumber
            DriveLetter = $bLetter
            GptType = [string]$p.GptType
            Offset = $p.Offset
            Size = $p.Size
            FileSystem = if ($vol) {{ [string]$vol.FileSystem }} else {{ '' }}
            FileSystemLabel = if ($vol) {{ [string]$vol.FileSystemLabel }} else {{ '' }}
        }}
    }}
}}

[PSCustomObject]@{{
    found = $true
    PartitionStyle = [string]$d.PartitionStyle
    OperationalStatus = [string]$d.OperationalStatus
    Size = $d.Size
    Model = [string]$d.Model
    FriendlyName = [string]$d.FriendlyName
    Partitions = $partList
}} | ConvertTo-Json -Compress -Depth 4
"""
        raw = self.run_powershell(ps_query, timeout=20)
        if not raw:
            raise WindowsPhysicalInstallationError(f"Physical disk {disk_number} did not respond to state query.")

        data = self.extract_json_from_output(raw)
        if data.get("found") is False:
            raise WindowsPhysicalInstallationError(f"Physical disk {disk_number} not found on Windows storage bus.")

        p_style = str(data.get("PartitionStyle", "")).upper()
        raw_parts = data.get("Partitions") or []
        if isinstance(raw_parts, dict):
            raw_parts = [raw_parts]

        # Extract mount paths if available
        boot_mount: Optional[str] = None
        data_mount: Optional[str] = None

        for p in raw_parts:
            dl = p.get("DriveLetter")
            label = str(p.get("FileSystemLabel") or "").upper()
            if dl and str(dl).strip():
                mount = f"{str(dl).strip().upper()}:\\"
                if "MVX_BOOT" in label or p.get("PartitionNumber") == 1:
                    boot_mount = mount
                elif "MVX_DATA" in label or p.get("PartitionNumber") == 2:
                    data_mount = mount

        diag = self.recovery_service.diagnose_installation_state(
            partition_style=p_style,
            partitions=raw_parts,
            boot_mount=boot_mount,
            data_mount=data_mount,
        )
        diag.details["disk_data"] = data
        return diag

    def prepare_physical_partitions(
        self,
        disk_number: int,
        esp_size_mb: int = DEFAULT_PHYSICAL_ESP_SIZE_MB,
        skip_dismount: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
        device: Optional[DeviceInfo] = None,
        expected_fingerprint: Optional[str] = None,
        force_wipe: bool = False,
    ) -> Tuple[str, str]:
        """Wipe, state-aware GPT initialize, format MVX_BOOT (FAT32) + MVX_DATA (exFAT), and verify.

        Returns:
            Tuple of (boot_mount_path, data_mount_path) e.g. ("F:\\", "E:\\")
        """
        if not self.allow_physical_formatting:
            raise PermissionError(
                "CRITICAL SAFETY SHIELD: Physical formatting is disabled by policy. "
                "allow_physical_formatting=True required."
            )

        def check_cancel() -> None:
            if is_cancelled and is_cancelled():
                raise WindowsPhysicalInstallationError("Installation was cancelled by user.")

        check_cancel()

        # If dismount has not already been performed, dismount now and verify
        if not skip_dismount:
            self.dismount_disk_volumes(disk_number)

        check_cancel()

        # Revalidate device identity immediately before destructive operations if provided
        if device and expected_fingerprint:
            self.revalidate_disk_identity_strict(
                device=device,
                expected_fingerprint=expected_fingerprint,
                disk_number=disk_number,
            )

        # Diagnose current physical disk state to determine minimum safe operation
        diag = self.diagnose_physical_disk(disk_number)
        logger.info(
            "Target Disk %d state diagnosed as: %s (%s). can_recover_without_wipe=%s",
            disk_number,
            diag.state.value,
            diag.state_name,
            diag.can_recover_without_wipe,
        )

        # Decide operation mode
        if force_wipe or diag.state in (USBInstallationState.ORIGINAL_FILESYSTEM, USBInstallationState.GPT_ESP_ONLY):
            mode = "FullWipe"
            logger.info("Executing FullWipe mode on Disk %d (State: %s)", disk_number, diag.state.value)
        elif diag.state == USBInstallationState.EMPTY_GPT:
            mode = "EmptyGptAllocate"
            logger.info("Executing EmptyGptAllocate mode on Disk %d (skipping redundant Clear-Disk and Initialize-Disk)", disk_number)
        elif diag.state == USBInstallationState.GPT_UNFORMATTED:
            mode = "FormatExisting"
            logger.info("Executing FormatExisting mode on Disk %d (formatting allocated partitions)", disk_number)
        else:
            mode = "ReuseExisting"
            logger.info("Partitions already exist and formatted on Disk %d (State: %s). Verifying and mounting.", disk_number, diag.state.value)

        ps_script = f"""
$ErrorActionPreference = 'Stop'
$diskNum = {disk_number}
$espSize = {esp_size_mb}
$mode = '{mode}'
$espGptType = '{{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}}'
$dataGptType = '{{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}}'

try {{
    # 1. Double-check disk safety inside PowerShell session
    $disk = Get-Disk -Number $diskNum
    if (-not $disk) {{
        throw "Physical disk $diskNum not found."
    }}
    if ($disk.IsSystem -or $disk.IsBoot) {{
        throw "Disk $diskNum is marked as an active system/boot disk."
    }}
    if ($disk.BusType -ne 'USB' -and $disk.FriendlyName -notmatch 'USB') {{
        throw "Disk $diskNum BusType is not USB ($($disk.BusType))."
    }}

    # 2. Flush dirty filesystem buffers safely (PowerShell 5.1 compatible Write-VolumeCache)
    Get-Partition -DiskNumber $diskNum -ErrorAction SilentlyContinue | ForEach-Object {{
        if ($_.DriveLetter) {{
            Write-VolumeCache -DriveLetter $_.DriveLetter -ErrorAction SilentlyContinue
        }}
    }}

    # 3. Clean all partition structures if FullWipe
    if ($mode -eq 'FullWipe') {{
        Clear-Disk -Number $diskNum -RemoveData -RemoveOEM -Confirm:$false
        Start-Sleep -Milliseconds 600

        # State-Aware GPT Initialization and Verification
        $diskAfter = Get-Disk -Number $diskNum -ErrorAction SilentlyContinue
        if (-not $diskAfter) {{
            throw "Disk $diskNum vanished from storage bus after Clear-Disk."
        }}
        $partStyle = [string]$diskAfter.PartitionStyle
        if ($partStyle -eq 'RAW') {{
            Initialize-Disk -Number $diskNum -PartitionStyle GPT
        }} elseif ($partStyle -ne 'GPT') {{
            throw "Disk $diskNum reports unsupported PartitionStyle '$partStyle' after Clear-Disk. Aborting."
        }}
    }}

    # 4. Partition allocation & formatting
    if ($mode -in @('FullWipe', 'EmptyGptAllocate')) {{
        # Confirm disk is GPT and Online
        $diskReady = Get-Disk -Number $diskNum -ErrorAction SilentlyContinue
        if (-not $diskReady -or $diskReady.PartitionStyle -ne 'GPT') {{
            Initialize-Disk -Number $diskNum -PartitionStyle GPT -ErrorAction SilentlyContinue
        }}
        if ($diskReady.OperationalStatus -notmatch 'Online') {{
            Set-Disk -Number $diskNum -IsOffline $false -ErrorAction SilentlyContinue
        }}

        # Create Partition 1 (FAT32 MVX_BOOT ESP)
        $p1 = New-Partition -DiskNumber $diskNum -Size ($espSize * 1MB) -AssignDriveLetter
        Start-Sleep -Milliseconds 600
        if ($p1.DriveLetter) {{
            Format-Volume -DriveLetter $p1.DriveLetter -FileSystem FAT32 -NewFileSystemLabel 'MVX_BOOT' -Confirm:$false -Force | Out-Null
        }} else {{
            $p1 | Format-Volume -FileSystem FAT32 -NewFileSystemLabel 'MVX_BOOT' -Confirm:$false -Force | Out-Null
        }}
        Set-Partition -DiskNumber $diskNum -PartitionNumber $p1.PartitionNumber -GptType $espGptType | Out-Null
        Start-Sleep -Milliseconds 400

        # Create Partition 2 (exFAT MVX_DATA Basic Data)
        $p2 = New-Partition -DiskNumber $diskNum -UseMaximumSize -GptType $dataGptType -AssignDriveLetter
        Start-Sleep -Milliseconds 600
        if ($p2.DriveLetter) {{
            Format-Volume -DriveLetter $p2.DriveLetter -FileSystem exFAT -NewFileSystemLabel 'MVX_DATA' -Confirm:$false -Force | Out-Null
        }} else {{
            $p2 | Format-Volume -FileSystem exFAT -NewFileSystemLabel 'MVX_DATA' -Confirm:$false -Force | Out-Null
        }}
    }} elseif ($mode -eq 'FormatExisting') {{
        $parts = Get-Partition -DiskNumber $diskNum | Where-Object {{ $_.Type -ne 'Reserved' }} | Sort-Object PartitionNumber
        $p1 = $parts[0]
        $p2 = $parts[1]

        if ($p1.DriveLetter) {{
            Format-Volume -DriveLetter $p1.DriveLetter -FileSystem FAT32 -NewFileSystemLabel 'MVX_BOOT' -Confirm:$false -Force | Out-Null
        }} else {{
            $p1 | Format-Volume -FileSystem FAT32 -NewFileSystemLabel 'MVX_BOOT' -Confirm:$false -Force | Out-Null
        }}
        Set-Partition -DiskNumber $diskNum -PartitionNumber $p1.PartitionNumber -GptType $espGptType | Out-Null

        if ($p2.DriveLetter) {{
            Format-Volume -DriveLetter $p2.DriveLetter -FileSystem exFAT -NewFileSystemLabel 'MVX_DATA' -Confirm:$false -Force | Out-Null
        }} else {{
            $p2 | Format-Volume -FileSystem exFAT -NewFileSystemLabel 'MVX_DATA' -Confirm:$false -Force | Out-Null
        }}
        Set-Partition -DiskNumber $diskNum -PartitionNumber $p2.PartitionNumber -GptType $dataGptType | Out-Null
    }}

    # 5. Verify partition layout, GUIDs, alignment, sizes, and labels
    $createdParts = Get-Partition -DiskNumber $diskNum | Where-Object {{ $_.Type -ne 'Reserved' }} | Sort-Object PartitionNumber
    if (@($createdParts).Count -lt 2) {{
        throw "Expected at least 2 partitions on Disk $diskNum, found $(@($createdParts).Count)."
    }}

    $bootPart = $createdParts[0]
    $dataPart = $createdParts[1]

    if ($bootPart.GptType -ne $espGptType) {{
        Set-Partition -DiskNumber $diskNum -PartitionNumber $bootPart.PartitionNumber -GptType $espGptType | Out-Null
    }}
    if ($dataPart.GptType -ne $dataGptType) {{
        Set-Partition -DiskNumber $diskNum -PartitionNumber $dataPart.PartitionNumber -GptType $dataGptType | Out-Null
    }}

    if (($bootPart.Offset % 1048576) -ne 0) {{
        throw "Partition 1 is not aligned to 1 MiB boundary (Offset: $($bootPart.Offset))"
    }}
    if (($dataPart.Offset % 1048576) -ne 0) {{
        throw "Partition 2 is not aligned to 1 MiB boundary (Offset: $($dataPart.Offset))"
    }}

    # Re-query assigned drive letters
    $bootLetter = if ($bootPart.DriveLetter) {{ [string]$bootPart.DriveLetter }} else {{ '' }}
    $dataLetter = if ($dataPart.DriveLetter) {{ [string]$dataPart.DriveLetter }} else {{ '' }}

    # Auto-assign if not automatically mounted
    $usedLetters = (Get-Volume).DriveLetter
    $avail = [char[]](68..90) | Where-Object {{ $_ -notin $usedLetters }}

    if (-not $bootLetter -and $avail.Count -gt 0) {{
        Set-Partition -DiskNumber $diskNum -PartitionNumber $bootPart.PartitionNumber -NewDriveLetter $avail[0] | Out-Null
        $bootLetter = [string]$avail[0]
        $avail = $avail[1..($avail.Count - 1)]
    }}

    if (-not $dataLetter -and $avail.Count -gt 0) {{
        Set-Partition -DiskNumber $diskNum -PartitionNumber $dataPart.PartitionNumber -NewDriveLetter $avail[0] | Out-Null
        $dataLetter = [string]$avail[0]
    }}

    # Verify volume filesystem labels
    $volBoot = Get-Volume -DriveLetter $bootLetter -ErrorAction SilentlyContinue
    $volData = Get-Volume -DriveLetter $dataLetter -ErrorAction SilentlyContinue
    if ($volBoot -and $volBoot.FileSystemLabel -ne 'MVX_BOOT') {{
        throw "Volume ${{bootLetter}}: label mismatch: expected MVX_BOOT, got $($volBoot.FileSystemLabel)"
    }}
    if ($volData -and $volData.FileSystemLabel -ne 'MVX_DATA') {{
        throw "Volume ${{dataLetter}}: label mismatch: expected MVX_DATA, got $($volData.FileSystemLabel)"
    }}

    [PSCustomObject]@{{
        boot_letter = $bootLetter
        data_letter = $dataLetter
        boot_guid = $bootPart.GptType
        data_guid = $dataPart.GptType
        boot_offset = $bootPart.Offset
        data_offset = $dataPart.Offset
        mode_executed = $mode
        verified = $true
    }} | ConvertTo-Json -Compress
}} catch {{
    $err = $_
    $ex = $err.Exception
    $inv = $err.InvocationInfo
    $cmd = if ($inv -and $inv.MyCommand) {{ $inv.MyCommand.Name }} else {{ '<unknown>' }}
    $line = if ($inv) {{ $inv.ScriptLineNumber }} else {{ 0 }}
    $hres = if ($ex.HResult) {{ '0x{{0:X8}}' -f $ex.HResult }} else {{ 'N/A' }}
    $pos = if ($inv) {{ $inv.PositionMessage }} else {{ '' }}
    $exType = $ex.GetType().FullName
    $exMsg = $ex.Message
    $stack = $err.ScriptStackTrace
    $diag = '=== POWERSHELL EXECUTION ERROR ===' + [Environment]::NewLine + 'Failing Command : ' + $cmd + [Environment]::NewLine + 'Script Line     : ' + $line + [Environment]::NewLine + 'Exception Type  : ' + $exType + [Environment]::NewLine + 'HResult         : ' + $hres + [Environment]::NewLine + 'Error Message   : ' + $exMsg + [Environment]::NewLine + 'Position        : ' + $pos + [Environment]::NewLine + 'Stack Trace     :' + [Environment]::NewLine + $stack + [Environment]::NewLine + '=================================='
    [Console]::Error.WriteLine($diag)
    exit 1
}}
"""
        # Pre-validate PowerShell syntax
        is_valid, syntax_errs = self.validate_powershell_syntax(ps_script)
        if not is_valid:
            err_msg = "; ".join(syntax_errs)
            logger.error("PowerShell syntax validation failed for partitioning script: %s", err_msg)
            raise WindowsPhysicalInstallationError(f"PowerShell syntax error: {err_msg}")

        logger.info("Starting state-aware physical disk partitioning on Disk %d (mode=%s)...", disk_number, mode)
        raw_output = self.run_powershell(ps_script, timeout=180)

        check_cancel()

        try:
            res_data = self.extract_json_from_output(raw_output)
            b_letter = res_data.get("boot_letter", "").strip().upper()
            d_letter = res_data.get("data_letter", "").strip().upper()

            if not b_letter or not d_letter:
                raise WindowsPhysicalInstallationError(
                    f"Windows did not assign drive letters to newly created partitions: {raw_output}"
                )

            boot_mount = f"{b_letter}:\\"
            data_mount = f"{d_letter}:\\"

            logger.info("Partitions formatted and verified successfully. Boot: %s, Data: %s", boot_mount, data_mount)
            return boot_mount, data_mount

        except Exception as exc:
            if not isinstance(exc, WindowsPhysicalInstallationError):
                raise WindowsPhysicalInstallationError(f"Failed parsing PowerShell output: {exc}") from exc
            raise

    # --------------------------------------------------------------------------
    # 7. Complete 12-Step Installation Pipeline & Resume Mechanism
    # --------------------------------------------------------------------------

    def revalidate_formatted_partition_layout(
        self,
        disk_number: int,
        expected_serial: str,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Revalidate that the disk possesses the exact expected MVentX partition layout.

        Strictly verifies:
        1. Disk hardware serial matches expected_serial.
        2. Disk is GPT and Online.
        3. Disk is not system, boot, or read-only.
        4. Exactly 2 non-reserved partitions exist.
        5. Partition 1: GptType is ESP GUID ({c12a7328-f81f-11d2-ba4b-00a0c93ec93b}),
           FileSystem is FAT32, FileSystemLabel is MVX_BOOT, size >= 256 MiB.
        6. Partition 2: GptType is Basic Data GUID ({ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}),
           FileSystem is exFAT, FileSystemLabel is MVX_DATA.
        7. Assigned drive letters / mount paths are accessible.

        If ANY attribute differs from this verified state, aborts immediately without attempting repair.
        Returns:
            Tuple of (boot_mount_path, data_mount_path, layout_metadata).
        """
        ps_query = f"""
$ErrorActionPreference = 'Stop'
$diskNum = {disk_number}
$espGptType = '{{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}}'
$dataGptType = '{{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}}'

$d = Get-Disk -Number $diskNum -ErrorAction SilentlyContinue
if (-not $d) {{
    throw "Physical disk $diskNum not found on storage bus."
}}

if ($d.IsSystem -or $d.IsBoot) {{
    throw "Disk $diskNum is marked as an active system/boot disk."
}}
if ($d.IsReadOnly) {{
    throw "Disk $diskNum is hardware write-protected or read-only."
}}
if ($d.PartitionStyle -ne 'GPT') {{
    throw "Disk $diskNum PartitionStyle is $($d.PartitionStyle), expected GPT."
}}
if ($d.OperationalStatus -notmatch 'Online') {{
    throw "Disk $diskNum OperationalStatus is $($d.OperationalStatus), expected Online."
}}

$parts = Get-Partition -DiskNumber $diskNum -ErrorAction Stop | Where-Object {{ $_.Type -ne 'Reserved' }} | Sort-Object PartitionNumber
$partList = @($parts)
if ($partList.Count -lt 2) {{
    throw "Expected at least 2 partitions on Disk $diskNum, found $($partList.Count)."
}}

$p1 = $partList[0]
$p2 = $partList[1]

# GUID checks
if ($p1.GptType -ne $espGptType) {{
    throw "Partition 1 GptType mismatch: expected $espGptType, got $($p1.GptType)"
}}
if ($p2.GptType -ne $dataGptType) {{
    throw "Partition 2 GptType mismatch: expected $dataGptType, got $($p2.GptType)"
}}

# Drive letters
$bLetter = if ($p1.DriveLetter) {{ [string]$p1.DriveLetter }} else {{ '' }}
$dLetter = if ($p2.DriveLetter) {{ [string]$p2.DriveLetter }} else {{ '' }}

# Auto-assign if missing
$usedLetters = (Get-Volume).DriveLetter
$avail = [char[]](68..90) | Where-Object {{ $_ -notin $usedLetters }}

if (-not $bLetter -and $avail.Count -gt 0) {{
    Set-Partition -DiskNumber $diskNum -PartitionNumber $p1.PartitionNumber -NewDriveLetter $avail[0] | Out-Null
    $bLetter = [string]$avail[0]
    $avail = $avail[1..($avail.Count - 1)]
}}
if (-not $dLetter -and $avail.Count -gt 0) {{
    Set-Partition -DiskNumber $diskNum -PartitionNumber $p2.PartitionNumber -NewDriveLetter $avail[0] | Out-Null
    $dLetter = [string]$avail[0]
}}

if (-not $bLetter -or -not $dLetter) {{
    throw "Failed to resolve drive letters for partitions on Disk $diskNum."
}}

$volBoot = Get-Volume -DriveLetter $bLetter -ErrorAction SilentlyContinue
$volData = Get-Volume -DriveLetter $dLetter -ErrorAction SilentlyContinue

if (-not $volBoot) {{
    throw "Cannot access volume on drive ${{bLetter}}:"
}}
if (-not $volData) {{
    throw "Cannot access volume on drive ${{dLetter}}:"
}}

if ($volBoot.FileSystemLabel -ne 'MVX_BOOT') {{
    throw "Volume ${{bLetter}}: label mismatch: expected 'MVX_BOOT', got '$($volBoot.FileSystemLabel)'"
}}
if ($volBoot.FileSystem -notmatch 'FAT') {{
    throw "Volume ${{bLetter}}: filesystem mismatch: expected FAT32, got '$($volBoot.FileSystem)'"
}}

if ($volData.FileSystemLabel -ne 'MVX_DATA') {{
    throw "Volume ${{dLetter}}: label mismatch: expected 'MVX_DATA', got '$($volData.FileSystemLabel)'"
}}
if ($volData.FileSystem -notmatch 'EXFAT') {{
    throw "Volume ${{dLetter}}: filesystem mismatch: expected exFAT, got '$($volData.FileSystem)'"
}}

[PSCustomObject]@{{
    disk_number = $diskNum
    boot_letter = $bLetter
    data_letter = $dLetter
    boot_label = $volBoot.FileSystemLabel
    boot_fs = $volBoot.FileSystem
    data_label = $volData.FileSystemLabel
    data_fs = $volData.FileSystem
    serial = [string]$d.SerialNumber
    model = [string]$d.Model
    verified = $true
}} | ConvertTo-Json -Compress
"""
        raw = self.run_powershell(ps_query, timeout=20)
        data = self.extract_json_from_output(raw)

        # Serial check in Python
        live_serial = normalize_serial_number(data.get("serial"))
        exp_serial = normalize_serial_number(expected_serial)
        if exp_serial and live_serial and live_serial != exp_serial:
            raise WindowsPhysicalInstallationError(
                f"Disk serial mismatch during layout revalidation! Expected '{exp_serial}', got '{live_serial}'."
            )

        b_letter = data.get("boot_letter", "").strip().upper()
        d_letter = data.get("data_letter", "").strip().upper()
        if not b_letter or not d_letter:
            raise WindowsPhysicalInstallationError(f"Drive letter resolution failed on Disk {disk_number}.")

        boot_mount = f"{b_letter}:\\"
        data_mount = f"{d_letter}:\\"
        return boot_mount, data_mount, data

    def resume_deployment_on_formatted_disk(
        self,
        device: DeviceInfo,
        expected_fingerprint: str = "",
        menu_title: str = "MVentX Multiboot System",
        menu_timeout: int = 10,
        progress_callback: Optional[ProgressCallback] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> InstallationResult:
        """Resume installation directly at GRUB deployment (Step 8) on verified formatted partitions.

        Enforces:
        - NEVER calls Clear-Disk, Initialize-Disk, New-Partition, or Format-Volume.
        - Resolves target disk dynamically via hardware serial (never hardcodes Disk 1).
        - Revalidates partition layout strictly and aborts if it deviates.
        - Deploys BOOTX64.EFI, runtime modules, fonts, skeleton, config, menu, and runs verification.
        """
        total_steps = 12
        stages = [
            "Step 1: Validating Windows host privileges and platform",
            "Step 2: Resolving target physical disk dynamically by hardware serial",
            "Step 3: Revalidating hardware identity and system disk safety shields",
            "Step 4: Verifying existing formatted partitions (MVX_BOOT FAT32 + MVX_DATA exFAT)",
            "Step 5: Pre-deployment accessibility and mount verification",
            "Step 6: [Preserved] Partition table structures verified intact (skipping wipe)",
            "Step 7: [Preserved] Filesystem formatting verified intact (skipping format)",
            "Step 8: Deploying GRUB 2.12 EFI bootloader binaries, modules, and fonts",
            "Step 9: Initializing MVentX directory skeleton (ISO/, config/, metadata/, backups/)",
            "Step 10: Writing initial authoritative mventx.json configuration",
            "Step 11: Compiling initial mventx_menu.cfg on MVX_BOOT",
            "Step 12: Verifying structural integrity and readability of installed USB",
        ]
        self.recovery_service.set_pipeline_stages(stages)

        def report(step: int, msg: str) -> None:
            logger.info("Physical Resume Step %d/%d: %s", step, total_steps, msg)
            self.recovery_service.track_stage(step, msg)
            if progress_callback:
                progress_callback(step, total_steps, msg)
            if is_cancelled and is_cancelled():
                raise WindowsPhysicalInstallationError("Installation was cancelled by user.")

        try:
            # Step 1: Validate environment
            report(1, "Validating Windows host privileges and platform...")
            self.validate_host_environment()

            # Step 2: Resolve target physical disk by hardware serial
            report(2, f"Resolving target disk dynamically by serial '{device.serial_number}'...")
            disk_num, disk_info = self.resolve_disk_number_by_serial(device.serial_number)

            # Step 3: Revalidate safety shields
            report(3, "Revalidating hardware identity and system disk safety shields...")
            self.device_manager.assert_safe_target(device, for_installation=True)

            # Step 4-7: Verify existing formatted layout without wiping or formatting
            report(4, f"Verifying existing formatted partitions on Disk {disk_num}...")
            report(5, "Verifying mount paths and drive letter accessibility...")
            report(6, "[Preserved] Partition table structures verified intact (skipping wipe)...")
            report(7, "[Preserved] Filesystem formatting verified intact (skipping format)...")

            boot_mount_path, data_mount_path, layout_info = self.revalidate_formatted_partition_layout(
                disk_number=disk_num,
                expected_serial=device.serial_number,
            )

            boot_p = Path(boot_mount_path)
            data_p = Path(data_mount_path)
            logger.info("Mount paths verified. Boot: %s, Data: %s", boot_p, data_p)

            # Step 8: Deploy GRUB 2.12 EFI bootloader
            report(8, "Deploying GRUB 2.12 EFI bootloader binaries, modules, and fonts...")
            boot_installer = BootloaderInstaller(boot_p)
            boot_installer.deploy_all(title=menu_title, timeout=menu_timeout)

            # Step 9: Initialize MVentX directory skeleton
            report(9, "Initializing MVentX directories (ISO/, config/, metadata/, backups/)...")
            FilesystemManager.initialize_data_partition(
                data_mount_path=data_p,
                initial_title=menu_title,
                initial_timeout=menu_timeout,
            )

            # Step 10: Writing initial mventx.json
            report(10, "Writing initial mventx.json with 0 registered environments...")

            # Step 11: Compile initial GRUB fallback menu
            report(11, "Compiling initial mventx_menu.cfg on MVX_BOOT...")
            boot_installer.deploy_initial_menu(title=menu_title, timeout=menu_timeout)

            # Step 12: Verify completed installation
            report(12, "Verifying structural integrity and readability of installed USB...")
            verifier = InstallationVerifier(data_p)
            verification_report = verifier.verify_mounted_partitions(
                boot_mount=boot_p,
                data_mount=data_p,
            )

            if not verification_report.is_valid:
                errors_str = "; ".join(verification_report.errors)
                raise WindowsPhysicalInstallationError(f"Post-installation verification failed: {errors_str}")

            return InstallationResult(
                success=True,
                target_path=device.device_id,
                verification_report=verification_report,
            )

        except Exception as exc:
            logger.error("Physical resume deployment failed: %s", exc)
            rec_report = self.recovery_service.cleanup_on_error()
            detailed_err = str(exc)
            if rec_report.completed_stages:
                detailed_err += f"\nCompleted operations: {', '.join(rec_report.completed_stages)}."
            if rec_report.remaining_stages:
                detailed_err += f"\nRemaining operations: {', '.join(rec_report.remaining_stages)}."
            return InstallationResult(
                success=False,
                target_path=device.device_id,
                error_message=detailed_err,
            )

    def install_to_physical_disk(
        self,
        device: DeviceInfo,
        expected_fingerprint: str,
        esp_size_mb: int = DEFAULT_PHYSICAL_ESP_SIZE_MB,
        menu_title: str = "MVentX Multiboot System",
        menu_timeout: int = 10,
        progress_callback: Optional[ProgressCallback] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
        force_wipe: bool = False,
        resume_if_formatted: bool = False,
    ) -> InstallationResult:
        """Execute the end-to-end 12-step installation pipeline on a physical USB drive."""
        total_steps = 12
        stages = [
            "Step 1: Validating Windows host privileges and device target",
            "Step 2: Generating partition layout plan",
            "Step 3: Revalidating hardware identity and safety shields",
            "Step 4: Dismounting active volumes on target disk safely",
            "Step 5: Verifying hardware identity immediately before destructive operations",
            "Step 6: Clearing disk structures and verifying GPT partition table",
            "Step 7: Allocating Partition 1 (MVX_BOOT) and Partition 2 (MVX_DATA)",
            "Step 8: Deploying GRUB 2.12 EFI bootloader binaries and modules",
            "Step 9: Initializing MVentX directories (ISO/, config/, metadata/)",
            "Step 10: Writing initial mventx.json configuration",
            "Step 11: Compiling initial mventx_menu.cfg on MVX_BOOT",
            "Step 12: Verifying structural integrity and readability of installed USB",
        ]
        self.recovery_service.set_pipeline_stages(stages)

        def report(step: int, msg: str) -> None:
            logger.info("Physical Installation Step %d/%d: %s", step, total_steps, msg)
            self.recovery_service.track_stage(step, msg)
            if progress_callback:
                progress_callback(step, total_steps, msg)
            if is_cancelled and is_cancelled():
                raise WindowsPhysicalInstallationError("Installation was cancelled by user.")

        boot_mount_path: Optional[str] = None
        data_mount_path: Optional[str] = None

        try:
            # Step 1: Validate environment & target
            report(1, "Validating Windows host privileges and device target...")
            self.validate_host_environment()

            # Step 2: Generate partition plan
            report(2, "Generating partition layout plan...")
            plan = PartitionPlanner.calculate_plan(
                disk_size_bytes=device.size_bytes,
                esp_size_mb=esp_size_mb,
            )

            # Step 3: Pre-write fingerprint revalidation & system disk protection
            report(3, "Revalidating hardware identity and safety shields...")
            disk_num, disk_data = self.revalidate_physical_disk(device, expected_fingerprint)

            # If resume on formatted media is explicitly requested, jump straight to Step 8 deployment
            if resume_if_formatted and not force_wipe:
                logger.info(
                    "Target Disk %d: resume_if_formatted active. Delegating to resume_deployment_on_formatted_disk.",
                    disk_num,
                )
                return self.resume_deployment_on_formatted_disk(
                    device=device,
                    expected_fingerprint=expected_fingerprint,
                    menu_title=menu_title,
                    menu_timeout=menu_timeout,
                    progress_callback=progress_callback,
                    is_cancelled=is_cancelled,
                )

            # Step 4: Dismount active volumes on target disk safely
            report(4, f"Dismounting active volumes on Disk {disk_num}...")
            dismounted_volumes = self.dismount_disk_volumes(disk_num)
            if dismounted_volumes:
                logger.info("Dismounted volumes on Disk %d: %s", disk_num, ", ".join(dismounted_volumes))

            # Step 5: Strict pre-destructive revalidation gate
            report(5, "Verifying hardware identity immediately before destructive operations...")
            self.revalidate_disk_identity_strict(
                device=device,
                expected_fingerprint=expected_fingerprint,
                disk_number=disk_num,
            )

            # Step 6, 7: Clean, partition, and format
            report(6, "Clearing disk structures and verifying GPT partition table...")
            report(7, f"Allocating Partition 1 (MVX_BOOT, {esp_size_mb} MiB) and Partition 2 (MVX_DATA)...")

            boot_mount_path, data_mount_path = self.prepare_physical_partitions(
                disk_number=disk_num,
                esp_size_mb=esp_size_mb,
                skip_dismount=True,  # Already cleanly dismounted and verified in Step 4
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
                device=device,
                expected_fingerprint=expected_fingerprint,
                force_wipe=force_wipe,
            )

            boot_p = Path(boot_mount_path)
            data_p = Path(data_mount_path)

            # Step 8: Deploy GRUB 2.12 EFI bootloader
            report(8, "Deploying GRUB 2.12 EFI bootloader binaries, modules, and fonts...")
            boot_installer = BootloaderInstaller(boot_p)
            boot_installer.deploy_all(title=menu_title, timeout=menu_timeout)

            # Step 9: Initialize MVentX directory skeleton
            report(9, "Initializing MVentX directories (ISO/, config/, metadata/, backups/)...")
            FilesystemManager.initialize_data_partition(
                data_mount_path=data_p,
                initial_title=menu_title,
                initial_timeout=menu_timeout,
            )

            # Step 10: Create authoritative mventx.json configuration
            report(10, "Writing initial mventx.json with 0 registered environments...")

            # Step 11: Generate initial GRUB fallback menu
            report(11, "Compiling initial mventx_menu.cfg on MVX_BOOT...")
            boot_installer.deploy_initial_menu(title=menu_title, timeout=menu_timeout)

            # Step 12: Verify completed installation
            report(12, "Verifying structural integrity and readability of installed USB...")
            verifier = InstallationVerifier(data_p)
            verification_report = verifier.verify_mounted_partitions(
                boot_mount=boot_p,
                data_mount=data_p,
            )

            if not verification_report.is_valid:
                errors_str = "; ".join(verification_report.errors)
                raise WindowsPhysicalInstallationError(f"Post-installation verification failed: {errors_str}")

            return InstallationResult(
                success=True,
                target_path=device.device_id,
                verification_report=verification_report,
                partition_plan=plan,
            )

        except Exception as exc:
            logger.error("Physical installation failed: %s", exc)
            rec_report = self.recovery_service.cleanup_on_error()
            detailed_err = str(exc)
            if rec_report.completed_stages:
                detailed_err += f"\nCompleted operations: {', '.join(rec_report.completed_stages)}."
            if rec_report.remaining_stages:
                detailed_err += f"\nRemaining operations: {', '.join(rec_report.remaining_stages)}."
            return InstallationResult(
                success=False,
                target_path=device.device_id,
                error_message=detailed_err,
            )
