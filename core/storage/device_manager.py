"""Device Management and Safety Subsystem for MVentX.

Enumerates physical and virtual storage devices across Windows and Linux,
detects partition layouts, and enforces strict system disk protection rules.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo


class DeviceSafetyError(Exception):
    """Raised when an operation targets an unsafe device (e.g. system disk)."""


class DeviceManager:
    """Discovers storage devices and evaluates target safety."""

    def __init__(self, allow_physical_formatting: bool = False) -> None:
        self.allow_physical_formatting = allow_physical_formatting

    # --------------------------------------------------------------------------
    # Cross-Platform Device Discovery
    # --------------------------------------------------------------------------

    def list_devices(self) -> List[DeviceInfo]:
        """Enumerate all connected physical storage devices."""
        system = platform.system().lower()
        if system == "windows":
            return self._list_devices_windows()
        elif system == "linux":
            return self._list_devices_linux()
        else:
            return self._list_devices_generic()

    def _list_devices_windows(self) -> List[DeviceInfo]:
        """Enumerate Windows disks using PowerShell / WMI query."""
        devices: List[DeviceInfo] = []

        # Find system drive letter (usually "C:")
        sys_drive = os.environ.get("SystemDrive", "C:").upper().rstrip("\\")

        ps_script = (
            "Get-Disk | Select-Object Number, FriendlyName, BusType, OperationalStatus, "
            "Size, LogicalSectorSize, PartitionStyle, IsBoot, IsSystem, IsReadOnly | "
            "ConvertTo-Json -Compress"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode == 0 and res.stdout.strip():
                raw_json = json.loads(res.stdout.strip())
                # Handle single disk returning dict instead of list
                disk_list = [raw_json] if isinstance(raw_json, dict) else raw_json
                for d in disk_list:
                    disk_num = d.get("Number", 0)
                    device_id = f"\\\\.\\PHYSICALDRIVE{disk_num}"
                    friendly_name = d.get("FriendlyName", f"Disk {disk_num}")
                    bus_type = str(d.get("BusType", "")).upper()
                    size = int(d.get("Size") or 0)
                    sector_size = int(d.get("LogicalSectorSize") or 512)
                    part_style = str(d.get("PartitionStyle", "")).lower()

                    is_boot = bool(d.get("IsBoot", False))
                    is_system = bool(d.get("IsSystem", False))
                    is_read_only = bool(d.get("IsReadOnly", False))
                    is_usb = (bus_type == "USB" or "USB" in friendly_name.upper())

                    dev_type = DeviceType.REMOVABLE_USB if is_usb else DeviceType.FIXED_DISK

                    # Enumerate partitions for this disk
                    partitions = self._list_windows_partitions(disk_num, sys_drive)
                    # If any partition hosts C:, mark whole disk as system disk
                    has_sys_part = any(p.is_system for p in partitions)
                    is_sys_disk = is_boot or is_system or has_sys_part

                    devices.append(
                        DeviceInfo(
                            device_id=device_id,
                            name=friendly_name,
                            vendor="",
                            model=friendly_name,
                            size_bytes=size,
                            sector_size=sector_size,
                            device_type=dev_type,
                            is_removable=is_usb,
                            is_read_only=is_read_only,
                            is_system_disk=is_sys_disk,
                            partition_table_type=part_style,
                            partitions=partitions,
                        )
                    )
        except Exception:
            # Fallback to generic detection if PowerShell fails
            return self._list_devices_generic()

        return devices

    def _list_windows_partitions(self, disk_number: int, sys_drive_letter: str) -> List[PartitionInfo]:
        """Enumerate partitions on a specific Windows disk."""
        partitions: List[PartitionInfo] = []
        ps_script = (
            f"Get-Partition -DiskNumber {disk_number} | "
            "Select-Object PartitionNumber, DriveLetter, Size, GptType, MbrType, IsActive, IsBoot, IsSystem | "
            "ConvertTo-Json -Compress"
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
                    type_guid = str(p.get("GptType") or "")
                    is_boot = bool(p.get("IsBoot", False))
                    is_sys = bool(p.get("IsSystem", False))

                    if letter and f"{letter}:".upper() == sys_drive_letter:
                        is_sys = True

                    # Try to inspect volume label if drive letter assigned
                    label = ""
                    fs = ""
                    if mount_point:
                        from core.storage.device_detection import DeviceDetector
                        vinfo = DeviceDetector.get_volume_info(mount_point)
                        if vinfo:
                            label = vinfo.label
                            fs = vinfo.filesystem

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

    def _list_devices_linux(self) -> List[DeviceInfo]:
        """Enumerate Linux block devices using lsblk / sysfs."""
        devices: List[DeviceInfo] = []
        if not shutil.which("lsblk"):
            return self._list_devices_generic()

        try:
            cmd = ["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,RO,RM,MODEL,VENDOR,TYPE,MOUNTPOINTS,FSTYPE,LABEL,PARTTYPE"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout.strip())
                blockdevices = data.get("blockdevices", [])
                for b in blockdevices:
                    if b.get("type") != "disk":
                        continue

                    dev_path = b.get("path", f"/dev/{b.get('name')}")
                    model = b.get("model", "") or ""
                    vendor = b.get("vendor", "") or ""
                    size = int(b.get("size") or 0)
                    is_ro = bool(b.get("ro", False))
                    is_rm = bool(b.get("rm", False))

                    partitions: List[PartitionInfo] = []
                    is_sys_disk = False

                    children = b.get("children", [])
                    for idx, c in enumerate(children, start=1):
                        mounts = c.get("mountpoints", []) or []
                        fs = c.get("fstype", "") or ""
                        lbl = c.get("label", "") or ""
                        psize = int(c.get("size") or 0)
                        part_type = c.get("parttype", "") or ""

                        is_sys_part = any(m in ("/", "/boot", "/boot/efi", "/home") for m in mounts)
                        if is_sys_part:
                            is_sys_disk = True

                        partitions.append(
                            PartitionInfo(
                                partition_number=idx,
                                name=c.get("name", f"part{idx}"),
                                label=lbl,
                                filesystem=fs.upper(),
                                size_bytes=psize,
                                type_guid=part_type,
                                mount_point=mounts[0] if mounts else None,
                                is_system=is_sys_part,
                                is_mventx_boot=(lbl.upper() == "MVX_BOOT"),
                                is_mventx_data=(lbl.upper() == "MVX_DATA"),
                            )
                        )

                    devices.append(
                        DeviceInfo(
                            device_id=dev_path,
                            name=f"{vendor} {model}".strip() or dev_path,
                            vendor=vendor,
                            model=model,
                            size_bytes=size,
                            device_type=DeviceType.REMOVABLE_USB if is_rm else DeviceType.FIXED_DISK,
                            is_removable=is_rm,
                            is_read_only=is_ro,
                            is_system_disk=is_sys_disk,
                            partitions=partitions,
                        )
                    )
        except Exception:
            return self._list_devices_generic()

        return devices

    def _list_devices_generic(self) -> List[DeviceInfo]:
        """Fallback device scanner relying on mounted partitions."""
        from core.storage.device_detection import DeviceDetector
        parts = DeviceDetector.list_partitions()
        devices: List[DeviceInfo] = []
        for p in parts:
            devices.append(
                DeviceInfo(
                    device_id=str(p.mount_point),
                    name=f"Volume {p.label or p.mount_point}",
                    size_bytes=p.total_bytes,
                    device_type=DeviceType.REMOVABLE_USB if p.is_removable else DeviceType.FIXED_DISK,
                    is_removable=p.is_removable,
                    partitions=[
                        PartitionInfo(
                            partition_number=1,
                            label=p.label,
                            filesystem=p.filesystem,
                            size_bytes=p.total_bytes,
                            mount_point=str(p.mount_point),
                            is_mventx_boot=p.is_mventx_boot,
                            is_mventx_data=p.is_mventx_data,
                        )
                    ],
                )
            )
        return devices

    # --------------------------------------------------------------------------
    # Virtual Disk Inspection
    # --------------------------------------------------------------------------

    def inspect_virtual_disk(self, file_path: Path | str) -> DeviceInfo:
        """Inspect a raw virtual disk image (.img, .vhd) and return a DeviceInfo model."""
        p = Path(file_path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Virtual disk file does not exist: {p}")

        file_size = p.stat().st_size
        partitions: List[PartitionInfo] = []
        part_table_type = "none"

        # Check GPT header at LBA 1 (offset 512)
        if file_size >= 1024:
            try:
                with open(p, "rb") as f:
                    f.seek(512)
                    gpt_sig = f.read(8)
                    if gpt_sig == b"EFI PART":
                        part_table_type = "gpt"
                        # Read partition table metadata
                        from core.storage.disk_validator import DiskValidator
                        validator = DiskValidator(p)
                        try:
                            # Primary header and entries
                            hdr = validator._read_gpt_header(1)
                            entries = validator._read_partition_entries(hdr)
                            for idx, pe in enumerate(entries, start=1):
                                if pe.is_empty:
                                    continue
                                is_esp = (pe.type_guid.lower() == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b")
                                is_data = (pe.type_guid.lower() == "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7")

                                partitions.append(
                                    PartitionInfo(
                                        partition_number=idx,
                                        name=pe.partition_name,
                                        label=pe.partition_name,
                                        size_bytes=pe.size_bytes,
                                        start_lba=pe.first_lba,
                                        end_lba=pe.last_lba,
                                        type_guid=pe.type_guid,
                                        is_mventx_boot=is_esp or (pe.partition_name == "MVX_BOOT"),
                                        is_mventx_data=is_data or (pe.partition_name == "MVX_DATA"),
                                    )
                                )
                        except Exception:
                            pass
            except Exception:
                pass

        return DeviceInfo(
            device_id=str(p),
            name=f"Virtual Disk ({p.name})",
            vendor="Virtual",
            model="Virtual Raw Disk",
            size_bytes=file_size,
            sector_size=512,
            device_type=DeviceType.VIRTUAL_DISK,
            is_removable=True,
            is_read_only=False,
            is_system_disk=False,
            partition_table_type=part_table_type,
            partitions=partitions,
            file_path=p,
        )

    # --------------------------------------------------------------------------
    # Strict Safety Controls
    # --------------------------------------------------------------------------

    def validate_target_safety(self, device: DeviceInfo) -> Tuple[bool, List[str]]:
        """Evaluate whether a storage device is safe to target for installation.

        Returns:
            Tuple of (is_safe: bool, rejection_reasons: List[str])
        """
        reasons: List[str] = []

        # 1. Operating System Boot / System Disk Guard
        if device.is_system_disk:
            reasons.append(
                f"REJECTED: Device '{device.name}' is an active Operating System system/boot disk."
            )

        # 2. Individual partition inspection: reject if mounts C:\ or /
        for part in device.partitions:
            if part.is_system:
                reasons.append(
                    f"REJECTED: Partition {part.partition_number} ({part.mount_point or part.name}) "
                    "contains an active system or boot volume."
                )

        # 3. Read-only Guard
        if device.is_read_only:
            reasons.append(
                f"REJECTED: Device '{device.name}' is marked read-only or hardware write-protected."
            )

        # 4. Zero / Invalid Capacity Guard
        if device.size_bytes <= 0:
            reasons.append(
                f"REJECTED: Device '{device.name}' reports 0 bytes capacity or is disconnected."
            )

        # 5. Physical Formatting Safety Guard
        if not device.is_virtual and not self.allow_physical_formatting:
            reasons.append(
                "BLOCKED: Physical device formatting is currently disabled by safety policy. "
                "Virtual disk target mode (.img / .vhd) must be used."
            )

        is_safe = (len(reasons) == 0)
        return is_safe, reasons

    def assert_safe_target(self, device: DeviceInfo) -> None:
        """Enforce safety rules and raise DeviceSafetyError if unsafe."""
        is_safe, reasons = self.validate_target_safety(device)
        if not is_safe:
            msg = "Target device safety verification failed:\n- " + "\n- ".join(reasons)
            raise DeviceSafetyError(msg)

