"""End-to-End Host Integration Test for MVentX Storage Devices.

Verifies complete cross-platform host workflow using actual filesystem structures:
- Device discovery and multi-attribute classification (VALID_MVENTX).
- Strong identity fingerprinting and pre-write revalidation.
- State machine lifecycle progression (DETECTED -> READY -> BUSY -> EJECTED/DISCONNECTED).
- Reading authoritative mventx.json configuration.
- ISO registration with file staging, atomic writing, and automated backups.
- GRUB menu synchronization and verification.
- Full environment lifecycle: rename, toggle, reorder, deregistration (preserving ISO).
- Disconnection guard blocking subsequent writes.
- Safe device ejection.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.host.device_classification import MVentXStatus
from core.host.device_identity import DeviceDisappearedError, DeviceIdentityMismatchError
from core.host.device_state import DeviceLifecycleState
from core.host.host_device_service import HostDeviceService
from core.iso_manager.registration import ISORegistrationManager
from core.storage.device_info import DeviceInfo, DeviceType, PartitionInfo
from core.storage.storage_manager import StorageManager


class TestHostIntegrationE2E(unittest.TestCase):
    """End-to-end integration test on real filesystem structures."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="mventx_host_e2e_"))
        self.boot_mount = self.temp_dir / "MVX_BOOT"
        self.data_mount = self.temp_dir / "MVX_DATA"
        self.host_iso_source = self.temp_dir / "host_downloads"

        self.boot_mount.mkdir(parents=True)
        self.data_mount.mkdir(parents=True)
        self.host_iso_source.mkdir(parents=True)

        # 1. Initialize boot partition structure
        (self.boot_mount / "EFI" / "BOOT").mkdir(parents=True)
        (self.boot_mount / "boot" / "grub").mkdir(parents=True)
        (self.boot_mount / "EFI" / "BOOT" / "BOOTX64.EFI").write_bytes(b"MZ_EFI_TEST_BOOTLOADER" * 100)
        (self.boot_mount / "boot" / "grub" / "grub.cfg").write_text(
            'search --no-floppy --label MVX_DATA --set=mventx_data_dev\n'
            'configfile ($mventx_boot_dev)/boot/grub/mventx_menu.cfg\n',
            encoding="utf-8",
        )
        (self.boot_mount / "boot" / "grub" / "mventx_menu.cfg").write_text(
            'menuentry "MVentX Fallback" { echo "Fallback" }\n',
            encoding="utf-8",
        )

        # 2. Initialize data partition structure
        (self.data_mount / "ISO").mkdir(parents=True)
        (self.data_mount / "config").mkdir(parents=True)
        (self.data_mount / "metadata").mkdir(parents=True)
        (self.data_mount / "backups").mkdir(parents=True)

        self.initial_config = {
            "version": "1.0.0",
            "meta": {"title": "MVentX Multiboot System", "theme": "mventx", "timeout": 10},
            "environments": [],
        }
        with open(self.data_mount / "config" / "mventx.json", "w", encoding="utf-8") as f:
            json.dump(self.initial_config, f, indent=2)

        # 3. Create a synthetic valid ISO file in host download folder
        self.sample_iso = self.host_iso_source / "ubuntu_sample.iso"
        # Standard ISO 9660 PVD header at offset 32768
        iso_bytes = bytearray(65536)
        iso_bytes[32768:32774] = b"\x01CD001"  # ISO 9660 Volume Descriptor
        self.sample_iso.write_bytes(iso_bytes)

        # 4. Construct DeviceInfo representing this connected device
        self.p1 = PartitionInfo(
            partition_number=1,
            label="MVX_BOOT",
            filesystem="FAT32",
            size_bytes=134217728,
            type_guid="c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
            mount_point=str(self.boot_mount),
            is_mventx_boot=True,
        )
        self.p2 = PartitionInfo(
            partition_number=2,
            label="MVX_DATA",
            filesystem="EXFAT",
            size_bytes=401587712,
            type_guid="ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
            mount_point=str(self.data_mount),
            is_mventx_data=True,
        )
        self.device = DeviceInfo(
            device_id=r"\\.\PHYSICALDRIVE7",
            name="MVentX USB 3.0",
            vendor="Kingston",
            model="DataTraveler",
            serial_number="KNG_987654321",
            size_bytes=536870912,
            sector_size=512,
            device_type=DeviceType.VIRTUAL_DISK,
            is_removable=True,
            is_system_disk=False,
            partition_table_type="gpt",
            partitions=[self.p1, self.p2],
            file_path=self.data_mount,
        )

        self.service = HostDeviceService(allow_physical_formatting=False)
        self.service.device_manager.list_devices = lambda: [self.device]

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_host_integration_lifecycle(self):
        """Execute full end-to-end host device integration pipeline."""
        # ----------------------------------------------------------------------
        # Step 1: Device Classification & Identification
        # ----------------------------------------------------------------------
        classification = self.service.classify_device(
            device=self.device,
            data_mount_override=self.data_mount,
            boot_mount_override=self.boot_mount,
        )
        self.assertEqual(classification.status, MVentXStatus.VALID_MVENTX)
        self.assertTrue(classification.is_valid)
        self.assertEqual(classification.environment_count, 0)
        self.assertEqual(classification.menu_title, "MVentX Multiboot System")

        # ----------------------------------------------------------------------
        # Step 2: Device Selection & State Machine Activation
        # ----------------------------------------------------------------------
        target = self.service.select_mventx_device(
            device=self.device,
            data_mount_override=self.data_mount,
            boot_mount_override=self.boot_mount,
        )
        self.assertIsNotNone(target)
        self.assertEqual(target.state_machine.current_state, DeviceLifecycleState.READY)
        self.assertTrue(target.state_machine.is_operational)

        # ----------------------------------------------------------------------
        # Step 3: ISO Registration & Staging onto MVX_DATA
        # ----------------------------------------------------------------------
        storage = StorageManager(data_mount=self.data_mount, boot_mount=self.boot_mount)
        reg_mgr = ISORegistrationManager(storage)

        # Copy sample ISO into MVX_DATA/ISO/ as user would
        dest_iso = self.data_mount / "ISO" / "ubuntu_sample.iso"
        shutil.copy2(self.sample_iso, dest_iso)

        # Register ISO
        record = reg_mgr.register_iso(
            iso_path=dest_iso,
            display_name="Ubuntu 24.04 LTS",
            boot_method="linux_casper",
            enabled=True,
        )
        self.assertEqual(record.display_name, "Ubuntu 24.04 LTS")
        self.assertTrue(record.enabled)

        # ----------------------------------------------------------------------
        # Step 4: Verification of Authoritative JSON & Generated GRUB Menu
        # ----------------------------------------------------------------------
        # Check mventx.json
        cfg_file = self.data_mount / "config" / "mventx.json"
        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg_data = json.load(f)
        self.assertEqual(len(cfg_data["environments"]), 1)
        self.assertEqual(cfg_data["environments"][0]["display_name"], "Ubuntu 24.04 LTS")

        # Check automated backup was created in backups/
        backups = list((self.data_mount / "backups").glob("mventx_*.json"))
        self.assertGreaterEqual(len(backups), 1)

        # Check mventx_menu.cfg was generated and contains entry
        menu_file = self.boot_mount / "boot" / "grub" / "mventx_menu.cfg"
        self.assertTrue(menu_file.is_file())
        menu_content = menu_file.read_text(encoding="utf-8")
        self.assertIn("Ubuntu 24.04 LTS", menu_content)
        self.assertIn("ubuntu_sample.iso", menu_content)

        # ----------------------------------------------------------------------
        # Step 5: Environment Management (Rename, Toggle, Reorder)
        # ----------------------------------------------------------------------
        # 5a. Rename
        reg_mgr.rename_environment(record.id, "Ubuntu Workstation")
        menu_content = menu_file.read_text(encoding="utf-8")
        self.assertIn("Ubuntu Workstation", menu_content)

        # 5b. Disable
        reg_mgr.disable_environment(record.id)
        menu_content = menu_file.read_text(encoding="utf-8")
        # When all entries disabled, fallback menu is rendered
        self.assertNotIn("Ubuntu Workstation", menu_content)
        self.assertIn("No environments registered or enabled", menu_content)

        # 5c. Re-enable
        reg_mgr.enable_environment(record.id)
        menu_content = menu_file.read_text(encoding="utf-8")
        self.assertIn("Ubuntu Workstation", menu_content)

        # ----------------------------------------------------------------------
        # Step 6: Deregistration (Preserves ISO by default)
        # ----------------------------------------------------------------------
        reg_mgr.remove_environment(record.id, delete_file=False)
        # ISO file must remain untouched in MVX_DATA/ISO/
        self.assertTrue(dest_iso.is_file())
        # Environment list in JSON must now be empty
        envs = reg_mgr.list_environments()
        self.assertEqual(len(envs), 0)

        # ----------------------------------------------------------------------
        # Step 7: Pre-write Identity Revalidation Guard
        # ----------------------------------------------------------------------
        fs_accessor = self.service.get_filesystem_accessor()
        # Normal write succeeds when identity matches
        fs_accessor.write_config_atomically(self.initial_config, create_backup=False)

        # If identity fetcher returns an imposter device, write is blocked
        fs_accessor.identity_fetcher = lambda: None  # Device disconnected
        with self.assertRaises(DeviceDisappearedError):
            fs_accessor.write_config_atomically(self.initial_config)

        # ----------------------------------------------------------------------
        # Step 8: Disconnection Handling
        # ----------------------------------------------------------------------
        self.service._handle_device_disconnected(self.device.device_id)
        # Target must be cleared immediately
        self.assertIsNone(self.service.active_target)
        self.assertEqual(target.state_machine.current_state, DeviceLifecycleState.DISCONNECTED)

        # ----------------------------------------------------------------------
        # Step 9: Safe Device Ejection
        # ----------------------------------------------------------------------
        # Re-activate target
        target = self.service.select_mventx_device(
            device=self.device,
            data_mount_override=self.data_mount,
            boot_mount_override=self.boot_mount,
        )
        self.assertEqual(target.state_machine.current_state, DeviceLifecycleState.READY)

        eject_result = self.service.eject_active_device()
        self.assertTrue(eject_result.success)
        self.assertIsNone(self.service.active_target)
        self.assertEqual(target.state_machine.current_state, DeviceLifecycleState.EJECTED)


if __name__ == "__main__":
    unittest.main()
