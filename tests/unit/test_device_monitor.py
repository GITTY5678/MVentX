"""Unit tests for MVentX Device Hotplug and Disconnection Monitor."""

import time
import unittest

from core.host.device_monitor import DeviceMonitor
from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo


class TestDeviceMonitor(unittest.TestCase):
    """Test device arrival, removal, and mount change event notifications."""

    def setUp(self):
        self.dev1 = DeviceInfo(
            device_id=r"\\.\PHYSICALDRIVE2",
            name="USB Disk A",
            size_bytes=536870912,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            partitions=[
                PartitionInfo(partition_number=1, label="MVX_BOOT", mount_point="E:\\"),
                PartitionInfo(partition_number=2, label="MVX_DATA", mount_point="F:\\"),
            ],
        )
        self.dev2 = DeviceInfo(
            device_id=r"\\.\PHYSICALDRIVE3",
            name="USB Disk B",
            size_bytes=1073741824,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            partitions=[],
        )

    def test_detects_arrival_and_disconnection_events(self):
        current_list = [self.dev1]
        enumerator = lambda: list(current_list)

        monitor = DeviceMonitor(device_enumerator=enumerator, poll_interval_seconds=10.0)

        connected_events = []
        disconnected_events = []

        monitor.on_device_connected = lambda d: connected_events.append(d.device_id)
        monitor.on_device_disconnected = lambda dev_id: disconnected_events.append(dev_id)

        # 1. Initial poll registers dev1
        events1 = monitor.poll_once()
        self.assertEqual(len(events1), 1)
        self.assertEqual(events1[0].event_type, "connected")
        self.assertEqual(events1[0].device_id, self.dev1.device_id)
        self.assertIn(self.dev1.device_id, connected_events)

        # 2. Second poll with no change produces 0 events
        events2 = monitor.poll_once()
        self.assertEqual(len(events2), 0)

        # 3. Add dev2 -> arrival event
        current_list.append(self.dev2)
        events3 = monitor.poll_once()
        self.assertEqual(len(events3), 1)
        self.assertEqual(events3[0].event_type, "connected")
        self.assertEqual(events3[0].device_id, self.dev2.device_id)

        # 4. Remove dev1 -> disconnection event
        current_list.remove(self.dev1)
        events4 = monitor.poll_once()
        self.assertEqual(len(events4), 1)
        self.assertEqual(events4[0].event_type, "disconnected")
        self.assertEqual(events4[0].device_id, self.dev1.device_id)
        self.assertIn(self.dev1.device_id, disconnected_events)

    def test_detects_partition_mount_changes(self):
        current_list = [self.dev1]
        enumerator = lambda: list(current_list)

        monitor = DeviceMonitor(device_enumerator=enumerator, poll_interval_seconds=10.0)
        monitor.poll_once()

        changed_events = []
        monitor.on_device_changed = lambda d: changed_events.append(d.device_id)

        # Mutate partition mount point on dev1
        mutated_dev1 = DeviceInfo(
            device_id=r"\\.\PHYSICALDRIVE2",
            name="USB Disk A",
            size_bytes=536870912,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            partitions=[
                PartitionInfo(partition_number=1, label="MVX_BOOT", mount_point="G:\\"),  # Drive letter shifted
                PartitionInfo(partition_number=2, label="MVX_DATA", mount_point="H:\\"),
            ],
        )
        current_list = [mutated_dev1]
        events = monitor.poll_once()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "changed")
        self.assertIn(self.dev1.device_id, changed_events)


if __name__ == "__main__":
    unittest.main()

