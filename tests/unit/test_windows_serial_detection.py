"""Unit tests for Windows USB device serial-number detection, normalization, and fingerprint consistency."""

import json
import unittest
from unittest.mock import MagicMock, patch

from core.host.device_identity import DeviceIdentity
from core.host.windows_device_provider import (
    WindowsDeviceProvider,
    normalize_serial_number,
)
from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo
from core.storage.device_manager import DeviceManager
from installer.windows_physical_installer import (
    WindowsPhysicalInstallationError,
    WindowsPhysicalInstaller,
)


class TestWindowsSerialDetection(unittest.TestCase):
    """Test suite verifying Windows hardware serial detection, normalization, and identity verification."""

    def test_normalize_serial_number_sandisk(self) -> None:
        """Verify normalization of genuine SanDisk Cruzer Blade serial number."""
        raw = "03026216101525041338"
        self.assertEqual(normalize_serial_number(raw), "03026216101525041338")

    def test_normalize_serial_number_formatting_variants(self) -> None:
        """Verify normalization strips whitespace, quotes, null bytes, and USBSTOR instance suffixes."""
        # Leading / trailing whitespace
        self.assertEqual(
            normalize_serial_number("  03026216101525041338  \r\n"),
            "03026216101525041338",
        )
        # Enclosing quotes
        self.assertEqual(
            normalize_serial_number('"03026216101525041338"'),
            "03026216101525041338",
        )
        self.assertEqual(
            normalize_serial_number("'03026216101525041338'"),
            "03026216101525041338",
        )
        # Embedded null byte
        self.assertEqual(
            normalize_serial_number("03026216101525041338\x00"),
            "03026216101525041338",
        )
        # USBSTOR instance suffix (&0)
        self.assertEqual(
            normalize_serial_number("03026216101525041338&0"),
            "03026216101525041338",
        )

    def test_normalize_serial_number_unavailable_safely_handled(self) -> None:
        """Verify unavailable, dummy, and placeholder serials safely return empty string."""
        self.assertEqual(normalize_serial_number(None), "")
        self.assertEqual(normalize_serial_number(""), "")
        self.assertEqual(normalize_serial_number("   "), "")
        self.assertEqual(normalize_serial_number("None"), "")
        self.assertEqual(normalize_serial_number("N/A"), "")
        self.assertEqual(normalize_serial_number("na"), "")
        self.assertEqual(normalize_serial_number("NULL"), "")
        self.assertEqual(normalize_serial_number("UNKNOWN"), "")
        self.assertEqual(normalize_serial_number("Not Available"), "")
        self.assertEqual(normalize_serial_number("undefined"), "")

    def test_never_substitutes_disk_number_or_letter(self) -> None:
        """Verify normalization does not accept fabricated disk numbers or drive letters."""
        # Device without serial must retain empty serial in DeviceIdentity
        dev_no_serial = DeviceInfo(
            device_id="\\\\.\\PHYSICALDRIVE1",
            name="Generic USB Flash Disk",
            size_bytes=16000000000,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            serial_number="",
            partitions=[
                PartitionInfo(partition_number=1, mount_point="E:\\")
            ],
        )
        identity = DeviceIdentity.from_device_info(dev_no_serial)
        self.assertEqual(identity.hardware_serial, "")
        self.assertNotIn("Disk 1", identity.hardware_serial)
        self.assertNotIn("E:", identity.hardware_serial)
        self.assertNotIn("PHYSICALDRIVE1", identity.hardware_serial)

    @patch("subprocess.run")
    def test_enumerate_sandisk_cruzer_blade(self, mock_run: MagicMock) -> None:
        """Verify WindowsDeviceProvider correctly parses SanDisk Cruzer Blade from PowerShell output."""
        mock_output = json.dumps([{
            "Number": 1,
            "UniqueId": "03026216101525041338",
            "FriendlyName": "SanDisk Cruzer Blade",
            "Manufacturer": "SanDisk",
            "Model": "Cruzer Blade",
            "SerialNumber": "03026216101525041338",
            "BusType": "USB",
            "OperationalStatus": "OK",
            "Size": 63065554944,
            "LogicalSectorSize": 512,
            "PartitionStyle": "GPT",
            "IsBoot": False,
            "IsSystem": False,
            "IsReadOnly": False,
        }])
        mock_run.return_value = MagicMock(return_value=0, returncode=0, stdout=mock_output)

        provider = WindowsDeviceProvider()
        # Mock partition enumeration to avoid secondary PowerShell invocation
        provider._enumerate_partitions = MagicMock(return_value=[
            PartitionInfo(
                partition_number=1,
                name="Partition 1",
                label="MVX_BOOT",
                filesystem="FAT32",
                size_bytes=67108864,
                mount_point="E:\\",
            ),
            PartitionInfo(
                partition_number=2,
                name="Partition 2",
                label="MVX_DATA",
                filesystem="exFAT",
                size_bytes=62998446080,
                mount_point="F:\\",
            ),
        ])

        devices = provider.enumerate_devices()
        self.assertEqual(len(devices), 1)
        dev = devices[0]

        self.assertEqual(dev.device_id, "\\\\.\\PHYSICALDRIVE1")
        self.assertEqual(dev.name, "SanDisk Cruzer Blade")
        self.assertEqual(dev.vendor, "SanDisk")
        self.assertEqual(dev.model, "Cruzer Blade")
        self.assertEqual(dev.serial_number, "03026216101525041338")
        self.assertEqual(dev.size_bytes, 63065554944)
        self.assertTrue(dev.is_removable)

    @patch("subprocess.run")
    def test_enumerate_device_missing_serial_safe_handling(self, mock_run: MagicMock) -> None:
        """Verify device without hardware serial is parsed safely with empty serial string."""
        mock_output = json.dumps([{
            "Number": 2,
            "UniqueId": "",
            "FriendlyName": "Generic Flash Disk",
            "Manufacturer": "",
            "Model": "Generic Flash Disk",
            "SerialNumber": None,
            "BusType": "USB",
            "OperationalStatus": "OK",
            "Size": 16000000000,
            "LogicalSectorSize": 512,
            "PartitionStyle": "MBR",
            "IsBoot": False,
            "IsSystem": False,
            "IsReadOnly": False,
        }])
        mock_run.return_value = MagicMock(return_value=0, returncode=0, stdout=mock_output)

        provider = WindowsDeviceProvider()
        provider._enumerate_partitions = MagicMock(return_value=[])

        devices = provider.enumerate_devices()
        self.assertEqual(len(devices), 1)
        dev = devices[0]

        self.assertEqual(dev.serial_number, "")
        identity = DeviceIdentity.from_device_info(dev)
        self.assertEqual(identity.hardware_serial, "")
        self.assertTrue(len(identity.fingerprint) > 0)

    @patch("core.host.windows.windows_device_provider.WindowsDeviceProvider.enumerate_devices")
    def test_device_manager_populates_serial_number(self, mock_enum: MagicMock) -> None:
        """Verify DeviceManager.list_devices() returns normalized serial_number."""
        sandisk_dev = DeviceInfo(
            device_id="\\\\.\\PHYSICALDRIVE1",
            name="SanDisk Cruzer Blade",
            vendor="SanDisk",
            model="Cruzer Blade",
            serial_number="03026216101525041338",
            size_bytes=63065554944,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
        )
        mock_enum.return_value = [sandisk_dev]

        dm = DeviceManager(allow_physical_formatting=True)
        devices = dm._list_devices_windows()

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].serial_number, "03026216101525041338")
        self.assertEqual(devices[0].vendor, "SanDisk")

    def test_fingerprint_consistency_across_enumeration_and_revalidation(self) -> None:
        """Verify device fingerprint is 100% deterministic between enumeration and revalidation."""
        # Simulated device from initial enumeration
        enumerated_dev = DeviceInfo(
            device_id="\\\\.\\PHYSICALDRIVE1",
            name="SanDisk Cruzer Blade",
            vendor="SanDisk",
            model="Cruzer Blade",
            serial_number="03026216101525041338",
            size_bytes=63065554944,
            sector_size=512,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            partitions=[
                PartitionInfo(
                    partition_number=1,
                    label="MVX_BOOT",
                    filesystem="FAT32",
                    size_bytes=67108864,
                    type_guid="c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
                ),
                PartitionInfo(
                    partition_number=2,
                    label="MVX_DATA",
                    filesystem="exFAT",
                    size_bytes=62998446080,
                    type_guid="ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
                ),
            ],
        )

        identity_enum = DeviceIdentity.from_device_info(enumerated_dev)
        expected_fp = identity_enum.compute_fingerprint()

        # Simulated device during pre-write revalidation query
        revalidated_dev = DeviceInfo(
            device_id="\\\\.\\PHYSICALDRIVE1",
            name="SanDisk Cruzer Blade",
            vendor="SanDisk",
            model="Cruzer Blade",
            serial_number="  03026216101525041338  ",  # Raw whitespace from bus
            size_bytes=63065554944,
            sector_size=512,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
            partitions=[
                PartitionInfo(
                    partition_number=1,
                    label="MVX_BOOT",
                    filesystem="FAT32",
                    size_bytes=67108864,
                    type_guid="c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
                ),
                PartitionInfo(
                    partition_number=2,
                    label="MVX_DATA",
                    filesystem="exFAT",
                    size_bytes=62998446080,
                    type_guid="ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
                ),
            ],
        )

        identity_reval = DeviceIdentity.from_device_info(revalidated_dev)
        reval_fp = identity_reval.compute_fingerprint()

        self.assertEqual(expected_fp, reval_fp)
        self.assertEqual(identity_enum.hardware_serial, "03026216101525041338")
        self.assertEqual(identity_reval.hardware_serial, "03026216101525041338")

    @patch.object(WindowsPhysicalInstaller, "validate_host_environment")
    @patch.object(WindowsPhysicalInstaller, "run_powershell")
    def test_revalidation_detects_swapped_serial(
        self, mock_ps: MagicMock, mock_val: MagicMock
    ) -> None:
        """Verify pre-write revalidation raises error if hardware serial changed on storage bus."""
        installer = WindowsPhysicalInstaller(allow_physical_formatting=True)

        target_dev = DeviceInfo(
            device_id="\\\\.\\PHYSICALDRIVE1",
            name="SanDisk Cruzer Blade",
            vendor="SanDisk",
            model="Cruzer Blade",
            serial_number="03026216101525041338",
            size_bytes=63065554944,
            device_type=DeviceType.REMOVABLE_USB,
            is_removable=True,
        )

        # Bus returns a swapped USB drive with a different serial
        swapped_bus_json = json.dumps({
            "Number": 1,
            "FriendlyName": "Kingston DataTraveler",
            "Manufacturer": "Kingston",
            "Model": "DataTraveler",
            "SerialNumber": "9999999999999999",
            "BusType": "USB",
            "Size": 64000000000,
            "IsBoot": False,
            "IsSystem": False,
            "IsReadOnly": False,
        })
        mock_ps.return_value = swapped_bus_json

        with self.assertRaises(WindowsPhysicalInstallationError) as ctx:
            installer.revalidate_physical_disk(target_dev, "original_fingerprint")
        self.assertIn("Target hardware serial changed before write", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

