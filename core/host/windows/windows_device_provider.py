"""Windows Storage Device Provider for MVentX.

Queries Windows storage subsystem via PowerShell Get-Disk, Get-CimInstance Win32_DiskDrive,
Get-Partition, and Get-Volume to extract comprehensive disk metrics, GPT GUIDs, hardware serials,
and partition layouts. Constructs strong DeviceIdentity records and DeviceInfo models.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.host.device_identity import DeviceIdentity, PartitionIdentity
from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo

logger = logging.getLogger(__name__)


def normalize_serial_number(raw_serial: Any) -> str:
    """Retrieve and normalize a storage device hardware serial number.

    Sanitizes raw serial numbers returned by Windows storage APIs (Get-Disk,
    Win32_DiskDrive, PNPDeviceID):
    - Strips leading/trailing whitespace, null bytes, and enclosing quotes.
    - Trims trailing Windows USBSTOR instance counters (e.g. '&0').
    - Discards placeholder and dummy strings ('None', 'N/A', 'Unknown', 'Null', etc.).
    - Never substitutes disk number or drive letter for hardware identity.
    - Returns empty string '' if a genuine hardware serial is unavailable.
    """
    if raw_serial is None:
        return ""

    serial = str(raw_serial).strip().strip("\x00").strip().strip("'\"")
    if not serial:
        return ""

    upper = serial.upper()
    if upper in ("", "NONE", "N/A", "NA", "NULL", "UNKNOWN", "NOT AVAILABLE", "NOT_AVAILABLE", "UNDEFINED"):
        return ""

    # Handle USBSTOR instance suffixes (e.g. '03026216101525041338&0' -> '03026216101525041338')
    if "&" in serial and not serial.startswith("&"):
        parts = serial.split("&")
        if len(parts) == 2 and parts[1].isdigit():
            serial = parts[0].strip()

    if not serial or serial.upper() in ("NONE", "N/A", "NULL", "UNKNOWN"):
        return ""

    return serial


class WindowsDeviceProvider:
    """Discovers and inspects physical and virtual disks on Windows 10/11."""

    def __init__(self) -> None:
        self.sys_drive = os.environ.get("SystemDrive", "C:").upper().rstrip("\\")

    def enumerate_devices(self) -> List[DeviceInfo]:
        """Enumerate connected physical disks and their partition tables."""
        devices: List[DeviceInfo] = []

        ps_script = """
$disks = @(Get-Disk -ErrorAction SilentlyContinue | Select-Object Number, UniqueId, FriendlyName, Manufacturer, Model, SerialNumber, BusType, OperationalStatus, Size, LogicalSectorSize, PartitionStyle, IsBoot, IsSystem, IsReadOnly)
$wmi = @(Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue | Select-Object Index, DeviceID, Model, Manufacturer, SerialNumber, PNPDeviceID)

if (-not $disks -and $wmi) {
    $disks = @($wmi | ForEach-Object {
        $idx = [int]$_.Index
        [PSCustomObject]@{
            Number = $idx
            UniqueId = $_.DeviceID
            FriendlyName = if ($_.Model) { $_.Model } else { "Disk $idx" }
            Manufacturer = $_.Manufacturer
            Model = $_.Model
            SerialNumber = $_.SerialNumber
            BusType = if ($_.InterfaceType -match 'USB') { 'USB' } else { $_.InterfaceType }
            Size = [int64]$_.Size
            LogicalSectorSize = 512
            PartitionStyle = 'unknown'
            IsBoot = $false
            IsSystem = $false
            IsReadOnly = $false
        }
    })
} else {
    foreach ($d in $disks) {
        $match = $wmi | Where-Object { $_.Index -eq $d.Number -or $_.DeviceID -match "PHYSICALDRIVE$($d.Number)$" } | Select-Object -First 1
        if ($match) {
            if (-not $d.SerialNumber -or $d.SerialNumber.ToString().Trim() -eq '') {
                if ($match.SerialNumber -and $match.SerialNumber.ToString().Trim() -ne '') {
                    $d.SerialNumber = $match.SerialNumber.ToString().Trim()
                } elseif ($match.PNPDeviceID -match '\\([^\\]+?)(&0)?$') {
                    $d.SerialNumber = $Matches[1]
                }
            }
            if (-not $d.Manufacturer -and $match.Manufacturer) {
                $d.Manufacturer = $match.Manufacturer
            }
        }
    }
}
$disks | ConvertTo-Json -Compress
"""
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=12,
            )
            if res.returncode == 0 and res.stdout.strip():
                raw = json.loads(res.stdout.strip())
                disk_list = [raw] if isinstance(raw, dict) else raw
                for d in disk_list:
                    dev = self._parse_disk(d)
                    if dev:
                        devices.append(dev)
        except Exception as exc:
            logger.debug("WindowsDeviceProvider PowerShell execution error: %s", exc)

        return devices

    def _parse_disk(self, d: Dict[str, Any]) -> Optional[DeviceInfo]:
        disk_num = d.get("Number", 0)
        device_id = f"\\\\.\\PHYSICALDRIVE{disk_num}"
        friendly_name = str(d.get("FriendlyName") or f"Disk {disk_num}").strip()
        model = str(d.get("Model") or friendly_name).strip()
        vendor = str(d.get("Manufacturer") or "").strip()
        serial = normalize_serial_number(d.get("SerialNumber"))
        bus_type = str(d.get("BusType", "")).upper()
        size = int(d.get("Size") or 0)
        sector_size = int(d.get("LogicalSectorSize") or 512)
        part_style = str(d.get("PartitionStyle", "")).lower()

        # If vendor is missing but friendly_name / model contains vendor prefix
        if not vendor and " " in friendly_name:
            parts = friendly_name.split(" ", 1)
            vendor = parts[0].strip()
            if model == friendly_name:
                model = parts[1].strip()

        is_boot = bool(d.get("IsBoot", False))
        is_system = bool(d.get("IsSystem", False))
        is_read_only = bool(d.get("IsReadOnly", False))
        is_usb = (bus_type == "USB" or "USB" in friendly_name.upper())

        dev_type = DeviceType.REMOVABLE_USB if is_usb else DeviceType.FIXED_DISK

        partitions = self._enumerate_partitions(disk_num)
        has_sys_part = any(p.is_system for p in partitions)
        is_sys_disk = is_boot or is_system or has_sys_part

        return DeviceInfo(
            device_id=device_id,
            name=friendly_name,
            vendor=vendor,
            model=model,
            serial_number=serial,
            size_bytes=size,
            sector_size=sector_size,
            device_type=dev_type,
            is_removable=is_usb,
            is_read_only=is_read_only,
            is_system_disk=is_sys_disk,
            partition_table_type=part_style,
            partitions=partitions,
        )

    def _enumerate_partitions(self, disk_number: int) -> List[PartitionInfo]:
        partitions: List[PartitionInfo] = []
        ps_script = (
            f"Get-Partition -DiskNumber {disk_number} | "
            "Select-Object PartitionNumber, UniqueId, GptType, MbrType, DriveLetter, "
            "Offset, Size, IsActive, IsBoot, IsSystem | ConvertTo-Json -Compress"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode == 0 and res.stdout.strip():
                raw = json.loads(res.stdout.strip())
                part_list = [raw] if isinstance(raw, dict) else raw
                for p in part_list:
                    pnum = p.get("PartitionNumber", 1)
                    letter = p.get("DriveLetter")
                    mount_point = f"{letter}:\\" if letter else None
                    size = int(p.get("Size") or 0)
                    type_guid = str(p.get("GptType") or "").lower()
                    is_boot = bool(p.get("IsBoot", False))
                    is_sys = bool(p.get("IsSystem", False))

                    if letter and f"{letter}:".upper() == self.sys_drive:
                        is_sys = True

                    # Retrieve volume label and filesystem
                    label = ""
                    fs = ""
                    if mount_point:
                        label, fs = self._query_volume_info(letter)

                    is_mventx_boot = (label.upper() == "MVX_BOOT")
                    is_mventx_data = (label.upper() == "MVX_DATA")

                    partitions.append(
                        PartitionInfo(
                            partition_number=pnum,
                            name=f"Partition {pnum}",
                            label=label,
                            filesystem=fs,
                            size_bytes=size,
                            type_guid=type_guid,
                            mount_point=mount_point,
                            is_bootable=is_boot,
                            is_active=bool(p.get("IsActive", False)),
                            is_system=is_sys,
                            is_mventx_boot=is_mventx_boot,
                            is_mventx_data=is_mventx_data,
                        )
                    )
        except Exception:
            pass
        return partitions

    def _query_volume_info(self, drive_letter: str) -> tuple[str, str]:
        """Query label and filesystem type for a drive letter using Win32 API."""
        try:
            import ctypes
            vol_buf = ctypes.create_unicode_buffer(261)
            fs_buf = ctypes.create_unicode_buffer(261)
            root = f"{drive_letter}:\\"
            ret = ctypes.windll.kernel32.GetVolumeInformationW(
                root, vol_buf, 261, None, None, None, fs_buf, 261
            )
            if ret:
                return vol_buf.value, fs_buf.value.upper()
        except Exception:
            pass
        return "", ""

    def build_device_identity(self, device: DeviceInfo) -> DeviceIdentity:
        """Construct a strong DeviceIdentity record from a DeviceInfo model."""
        partition_identities: List[PartitionIdentity] = []
        for p in device.partitions:
            partition_identities.append(
                PartitionIdentity(
                    partition_number=p.partition_number,
                    partition_guid="",
                    type_guid=p.type_guid,
                    start_lba=p.start_lba,
                    size_bytes=p.size_bytes,
                    label=p.label,
                    filesystem=p.filesystem,
                )
            )

        serial = normalize_serial_number(device.serial_number)

        return DeviceIdentity(
            disk_guid=device.device_id,
            hardware_serial=serial,
            model=device.model,
            vendor=device.vendor,
            capacity_bytes=device.size_bytes,
            sector_size=device.sector_size,
            bus_type="USB" if device.is_removable else "FIXED",
            device_path=device.device_id,
            is_virtual=device.is_virtual,
            partitions=tuple(partition_identities),
        )
