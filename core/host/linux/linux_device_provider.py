"""Linux Storage Device Provider for MVentX.

Queries Linux storage subsystem via lsblk JSON and sysfs block hierarchies.
Extracts GPT GUIDs, hardware serials, udev properties, and mount points.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.host.device_identity import DeviceIdentity, PartitionIdentity
from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo


class LinuxDeviceProvider:
    """Discovers and inspects physical and virtual block devices on Linux."""

    def enumerate_devices(self) -> List[DeviceInfo]:
        """Enumerate Linux block devices using lsblk."""
        devices: List[DeviceInfo] = []
        if not shutil.which("lsblk"):
            return devices

        cmd = [
            "lsblk", "-J", "-b", "-o",
            "NAME,PATH,SIZE,RO,RM,MODEL,VENDOR,TYPE,MOUNTPOINTS,FSTYPE,LABEL,PARTTYPE,PARTUUID,SERIAL",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout.strip())
                blockdevices = data.get("blockdevices", [])
                for b in blockdevices:
                    if b.get("type") != "disk":
                        continue
                    dev = self._parse_blockdevice(b)
                    if dev:
                        devices.append(dev)
        except Exception:
            pass

        return devices

    def _parse_blockdevice(self, b: Dict[str, Any]) -> Optional[DeviceInfo]:
        dev_path = b.get("path", f"/dev/{b.get('name')}")
        model = (b.get("model") or "").strip()
        vendor = (b.get("vendor") or "").strip()
        serial = (b.get("serial") or "").strip()
        size = int(b.get("size") or 0)
        is_ro = bool(b.get("ro", False))
        is_rm = bool(b.get("rm", False))

        partitions: List[PartitionInfo] = []
        is_sys_disk = False

        children = b.get("children", [])
        for idx, c in enumerate(children, start=1):
            mounts = c.get("mountpoints", []) or []
            fs = (c.get("fstype") or "").strip().upper()
            lbl = (c.get("label") or "").strip()
            psize = int(c.get("size") or 0)
            part_type = (c.get("parttype") or "").strip().lower()

            is_sys_part = any(m in ("/", "/boot", "/boot/efi", "/home") for m in mounts)
            if is_sys_part:
                is_sys_disk = True

            partitions.append(
                PartitionInfo(
                    partition_number=idx,
                    name=c.get("name", f"part{idx}"),
                    label=lbl,
                    filesystem=fs,
                    size_bytes=psize,
                    type_guid=part_type,
                    mount_point=mounts[0] if mounts else None,
                    is_system=is_sys_part,
                    is_mventx_boot=(lbl.upper() == "MVX_BOOT"),
                    is_mventx_data=(lbl.upper() == "MVX_DATA"),
                )
            )

        return DeviceInfo(
            device_id=dev_path,
            name=f"{vendor} {model}".strip() or dev_path,
            vendor=vendor,
            model=model,
            serial_number=serial,
            size_bytes=size,
            sector_size=512,
            device_type=DeviceType.REMOVABLE_USB if is_rm else DeviceType.FIXED_DISK,
            is_removable=is_rm,
            is_read_only=is_ro,
            is_system_disk=is_sys_disk,
            partition_table_type="gpt",
            partitions=partitions,
        )

    def build_device_identity(self, device: DeviceInfo) -> DeviceIdentity:
        """Construct a DeviceIdentity record from a Linux DeviceInfo model."""
        part_identities: List[PartitionIdentity] = []
        for p in device.partitions:
            part_identities.append(
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

        return DeviceIdentity(
            disk_guid=device.device_id,
            hardware_serial=device.serial_number,
            model=device.model,
            vendor=device.vendor,
            capacity_bytes=device.size_bytes,
            sector_size=device.sector_size,
            bus_type="USB" if device.is_removable else "FIXED",
            device_path=device.device_id,
            is_virtual=device.is_virtual,
            partitions=tuple(part_identities),
        )

