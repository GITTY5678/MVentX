"""MVentX Storage Subsystem.

Provides drive detection, partition resolution, and filesystem mounting
abstractions for target MVentX USB media.
"""

from typing import List

from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo
from core.storage.device_manager import DeviceManager, DeviceSafetyError
from core.storage.storage_manager import StorageManager

__all__: List[str] = [
    "storage_manager",
    "device_detection",
    "device_info",
    "device_manager",
    "DeviceInfo",
    "PartitionInfo",
    "DeviceType",
    "DeviceManager",
    "DeviceSafetyError",
    "StorageManager",
]

