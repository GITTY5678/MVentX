"""Unit tests for MVentX Strong Device Identity and Fingerprinting."""

import unittest

from core.host.device_identity import (
    DeviceDisappearedError,
    DeviceIdentity,
    DeviceIdentityMismatchError,
    PartitionIdentity,
    revalidate_device_identity,
)


class TestDeviceIdentity(unittest.TestCase):
    """Test device fingerprinting, comparison, and pre-write verification."""

    def setUp(self):
        self.p1 = PartitionIdentity(
            partition_number=1,
            partition_guid="11111111-2222-3333-4444-555555555555",
            type_guid="c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
            start_lba=2048,
            size_bytes=134217728,
            label="MVX_BOOT",
            filesystem="FAT32",
        )
        self.p2 = PartitionIdentity(
            partition_number=2,
            partition_guid="66666666-7777-8888-9999-000000000000",
            type_guid="ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
            start_lba=264192,
            size_bytes=401587712,
            label="MVX_DATA",
            filesystem="EXFAT",
        )
        self.identity1 = DeviceIdentity(
            disk_guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            hardware_serial="USB_FLASH_12345",
            model="SanDisk Ultra",
            vendor="SanDisk",
            capacity_bytes=536870912,
            sector_size=512,
            bus_type="USB",
            device_path=r"\\.\PHYSICALDRIVE2",
            is_virtual=False,
            partitions=(self.p1, self.p2),
        )

    def test_fingerprint_deterministic(self):
        fp1 = self.identity1.fingerprint
        fp2 = self.identity1.fingerprint
        self.assertEqual(fp1, fp2)
        self.assertEqual(len(fp1), 64)  # SHA-256 hex length

    def test_matches_identical(self):
        identity2 = DeviceIdentity(
            disk_guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            hardware_serial="USB_FLASH_12345",
            model="SanDisk Ultra",
            vendor="SanDisk",
            capacity_bytes=536870912,
            sector_size=512,
            bus_type="USB",
            device_path=r"\\.\PHYSICALDRIVE2",
            is_virtual=False,
            partitions=(self.p1, self.p2),
        )
        self.assertTrue(self.identity1.matches(identity2))

    def test_rejects_different_disk_guid(self):
        identity_diff_guid = DeviceIdentity(
            disk_guid="ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee",
            hardware_serial="USB_FLASH_12345",
            model="SanDisk Ultra",
            vendor="SanDisk",
            capacity_bytes=536870912,
            sector_size=512,
            bus_type="USB",
            device_path=r"\\.\PHYSICALDRIVE2",
            is_virtual=False,
            partitions=(self.p1, self.p2),
        )
        self.assertFalse(self.identity1.matches(identity_diff_guid))

    def test_rejects_different_serial(self):
        identity_diff_serial = DeviceIdentity(
            disk_guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            hardware_serial="DIFFERENT_SERIAL_999",
            model="SanDisk Ultra",
            vendor="SanDisk",
            capacity_bytes=536870912,
            sector_size=512,
            bus_type="USB",
            device_path=r"\\.\PHYSICALDRIVE2",
            is_virtual=False,
            partitions=(self.p1, self.p2),
        )
        self.assertFalse(self.identity1.matches(identity_diff_serial))

    def test_revalidate_passes_when_device_present(self):
        fetcher = lambda: self.identity1
        verified = revalidate_device_identity(self.identity1, fetcher, "test write")
        self.assertEqual(verified.fingerprint, self.identity1.fingerprint)

    def test_revalidate_raises_when_device_disappeared(self):
        fetcher = lambda: None
        with self.assertRaises(DeviceDisappearedError):
            revalidate_device_identity(self.identity1, fetcher, "test write")

    def test_revalidate_raises_when_device_mismatched(self):
        imposter = DeviceIdentity(
            disk_guid="99999999-bbbb-cccc-dddd-eeeeeeeeeeee",
            hardware_serial="IMPOSTER_DRIVE",
            model="Generic USB Drive",
            vendor="Generic",
            capacity_bytes=536870912,
            sector_size=512,
            bus_type="USB",
            device_path=r"\\.\PHYSICALDRIVE2",
            is_virtual=False,
            partitions=(),
        )
        fetcher = lambda: imposter
        with self.assertRaises(DeviceIdentityMismatchError):
            revalidate_device_identity(self.identity1, fetcher, "test write")


if __name__ == "__main__":
    unittest.main()

