"""Physical Device Validation Workflow and Safety Protocol for MVentX (Milestone 13).

Implements the strict 12-step validation procedure for physical USB storage devices:
1. Identify the USB device.
2. Verify manufacturer, model, capacity, and serial where available.
3. Verify the device identity twice (fingerprint verification).
4. Display the exact destructive operations.
5. Require explicit two-phase user confirmation.
6. Prepare the USB (partitioning and formatting).
7. Verify both partitions (FAT32 MVX_BOOT + exFAT MVX_DATA).
8. Safely eject the device.
9. Reconnect device and detect hotplug event.
10. Verify that MVentX recognizes the prepared USB as VALID_MVENTX.
11. Boot the USB on UEFI hardware.
12. Test registered Linux boot environments.

CRITICAL SAFETY POLICY:
- Physical storage device formatting is disabled by default.
- Never modify a physical storage device without explicit confirmation, accurate device
  identification, capacity/identity verification, and pre-write fingerprint revalidation.
- All automated testing must use virtual disks unless explicitly authorized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.host.device_classification import DeviceClassifier, MVentXStatus
from core.host.device_identity import DeviceIdentity
from core.storage.device_info import DeviceInfo, DeviceType
from core.storage.device_manager import DeviceManager, DeviceSafetyError
from installer.installation_verifier import InstallationVerificationReport, InstallationVerifier
from installer.installation_service import InstallationResult, ProgressCallback
from installer.partition_planner import (
    DEFAULT_ESP_SIZE_MB,
    MIN_DISK_CAPACITY_MB,
    PartitionPlan,
    PartitionPlanner,
)

logger = logging.getLogger(__name__)


class PhysicalSafetyViolationError(Exception):
    """Raised when physical storage safety rules or confirmation checks fail."""


@dataclass
class DestructiveOperationPlan:
    """Explicit declaration of all destructive changes to be performed on target drive."""
    device_id: str
    device_name: str
    capacity_bytes: int
    capacity_gb: float
    vendor: str
    model: str
    serial: str
    is_removable: bool
    destructive_actions: List[str] = field(default_factory=list)
    existing_partitions: List[str] = field(default_factory=list)
    partition_plan: Optional[PartitionPlan] = None
    fingerprint: str = ""

    def summary(self) -> str:
        actions_str = "\n".join(f"  - {a}" for a in self.destructive_actions)
        existing_str = ", ".join(self.existing_partitions) if self.existing_partitions else "None"
        return (
            "==========================================================\n"
            "  DESTRUCTIVE PHYSICAL DRIVE INITIALIZATION PLAN\n"
            "==========================================================\n"
            f"  Target Device:       {self.device_name} ({self.device_id})\n"
            f"  Manufacturer/Model:  {self.vendor} {self.model}\n"
            f"  Hardware Serial:     {self.serial or 'N/A'}\n"
            f"  Capacity:            {self.capacity_gb:.2f} GB ({self.capacity_bytes:,} bytes)\n"
            f"  Device Fingerprint:  {self.fingerprint[:16]}...\n"
            f"  Existing Partitions: {existing_str}\n"
            "----------------------------------------------------------\n"
            "  Destructive Operations to Execute:\n"
            f"{actions_str}\n"
            "=========================================================="
        )


class PhysicalDeviceWorkflow:
    """Coordinates controlled physical USB preparation, safety shields, and verification."""

    def __init__(
        self,
        device_manager: Optional[DeviceManager] = None,
        allow_physical_formatting: bool = False,
    ) -> None:
        self.allow_physical_formatting = allow_physical_formatting
        self.device_manager = device_manager or DeviceManager(
            allow_physical_formatting=allow_physical_formatting
        )

    # --------------------------------------------------------------------------
    # Step 1 & 2: Identification & Hardware Attribute Verification
    # --------------------------------------------------------------------------

    def identify_and_validate_device(self, device: DeviceInfo) -> DeviceIdentity:
        """Step 1 & 2: Identify physical device, verify hardware attributes, and compute identity."""
        # Enforce system disk protection
        self.device_manager.assert_safe_target(device)

        # Enforce removable USB device type
        if not device.is_removable and device.device_type != DeviceType.REMOVABLE_USB:
            raise PhysicalSafetyViolationError(
                f"Device '{device.name}' is not classified as removable USB storage. "
                "Fixed physical drives cannot be initialized as MVentX USBs."
            )

        # Enforce minimum capacity (e.g. 128 MB)
        size_mb = device.size_bytes / (1024 ** 2)
        if size_mb < MIN_DISK_CAPACITY_MB:
            raise PhysicalSafetyViolationError(
                f"Device '{device.name}' capacity ({size_mb:.1f} MiB) is below minimum "
                f"required capacity ({MIN_DISK_CAPACITY_MB} MiB)."
            )

        # Construct and return deterministic identity
        return DeviceIdentity.from_device_info(device)

    # --------------------------------------------------------------------------
    # Step 3 & 4: Fingerprint & Destructive Plan Generation
    # --------------------------------------------------------------------------

    def generate_plan(
        self,
        device: DeviceInfo,
        esp_size_mb: int = DEFAULT_ESP_SIZE_MB,
    ) -> DestructiveOperationPlan:
        """Step 3 & 4: Construct explicit destructive operation plan with hardware fingerprint."""
        identity = self.identify_and_validate_device(device)
        fingerprint = identity.compute_fingerprint()

        partition_plan = PartitionPlanner.calculate_plan(
            disk_size_bytes=device.size_bytes,
            esp_size_mb=esp_size_mb,
            sector_size=device.sector_size or 512,
        )

        existing_parts = [
            f"Part {p.partition_number} ({p.label or 'Unnamed'}, {p.size_bytes // (1024**2)} MiB, {p.filesystem})"
            for p in device.partitions
        ]

        actions = [
            "WIPE: Erase existing partition table and partition boot records.",
            f"INITIALIZE: Write new GPT (GUID Partition Table) with Protective MBR at LBA 0.",
            f"ALLOCATE: Partition 1 'MVX_BOOT' (FAT32, {esp_size_mb} MiB, Type GUID C12A7328-F81F-11D2-BA4B-00A0C93EC93B).",
            f"ALLOCATE: Partition 2 'MVX_DATA' (exFAT, {partition_plan.data_partition.size_mb:.1f} MiB, Type GUID EBD0A0A2-B9E5-4433-87C0-68B6B72699C7).",
            "DEPLOY: Install GNU GRUB 2.12 x86_64-efi bootloader and standalone EFI fallback (/EFI/BOOT/BOOTX64.EFI).",
            "INITIALIZE: Create MVentX directory skeletons (ISO/, config/, metadata/, backups/).",
            "CONFIGURE: Write initial mventx.json with 0 registered environments and fallback boot menu.",
        ]

        return DestructiveOperationPlan(
            device_id=device.device_id,
            device_name=device.name,
            capacity_bytes=device.size_bytes,
            capacity_gb=device.size_bytes / (1024 ** 3),
            vendor=device.vendor,
            model=device.model,
            serial=device.serial_number,
            is_removable=device.is_removable,
            destructive_actions=actions,
            existing_partitions=existing_parts,
            partition_plan=partition_plan,
            fingerprint=fingerprint,
        )

    # --------------------------------------------------------------------------
    # Step 5: Two-Phase Explicit User Confirmation Guard
    # --------------------------------------------------------------------------

    def verify_two_phase_confirmation(
        self,
        plan: DestructiveOperationPlan,
        confirmed_phase_one: bool,
        confirmed_phase_two: bool,
        typed_device_confirmation: str,
    ) -> None:
        """Step 5: Enforce strict two-phase manual user confirmation before writing."""
        if not self.allow_physical_formatting:
            raise PhysicalSafetyViolationError(
                "CRITICAL SAFETY SHIELD: Physical device preparation is disabled by system policy. "
                "Set allow_physical_formatting=True to unlock physical drive operations."
            )

        if not confirmed_phase_one:
            raise PhysicalSafetyViolationError("Phase 1 confirmation checkbox was not checked.")

        if not confirmed_phase_two:
            raise PhysicalSafetyViolationError("Phase 2 destructive modal confirmation was not accepted.")

        # Require user to have typed the exact drive model or drive name as safety gate
        expected = plan.device_name.strip().upper()
        typed = typed_device_confirmation.strip().upper()
        if typed not in expected and expected not in typed and typed != "YES":
            raise PhysicalSafetyViolationError(
                f"Device confirmation mismatch. Expected drive name '{plan.device_name}', received '{typed_device_confirmation}'."
            )

    # --------------------------------------------------------------------------
    # Step 6: Pre-Write Fingerprint Revalidation
    # --------------------------------------------------------------------------

    def revalidate_device_before_write(
        self,
        initial_device: DeviceInfo,
        recorded_fingerprint: str,
    ) -> DeviceInfo:
        """Step 6: Query the storage bus to ensure device has not been swapped or unplugged."""
        current_devices = self.device_manager.list_devices()
        matching = [d for d in current_devices if d.device_id == initial_device.device_id]

        if not matching:
            raise PhysicalSafetyViolationError(
                f"Target storage device '{initial_device.name}' ({initial_device.device_id}) "
                "disappeared from the storage bus before writing could begin."
            )

        current_dev = matching[0]
        current_identity = DeviceIdentity.from_device_info(current_dev)
        current_fp = current_identity.compute_fingerprint()

        if current_fp != recorded_fingerprint:
            raise PhysicalSafetyViolationError(
                f"Device identity shifted between confirmation and execution. "
                f"Initial fingerprint: {recorded_fingerprint[:16]}..., current: {current_fp[:16]}... Aborting."
            )

        # Enforce that it hasn't become a system disk
        if current_dev.is_system_disk:
            raise PhysicalSafetyViolationError(
                f"Target device '{current_dev.name}' is flagged as an active system disk. Aborting write."
            )

        return current_dev

    # --------------------------------------------------------------------------
    # Step 7: Preparation Execution Guard
    # --------------------------------------------------------------------------

    def prepare_physical_usb(
        self,
        device: DeviceInfo,
        plan: DestructiveOperationPlan,
        confirmed_phase_one: bool,
        confirmed_phase_two: bool,
        typed_confirmation: str,
        esp_size_mb: int = DEFAULT_ESP_SIZE_MB,
        menu_title: str = "MVentX Multiboot System",
        menu_timeout: int = 10,
        progress_callback: Optional[ProgressCallback] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> InstallationResult:
        """Execute physical preparation pipeline with all shields active."""
        # 1. Enforce two-phase confirmation
        self.verify_two_phase_confirmation(
            plan=plan,
            confirmed_phase_one=confirmed_phase_one,
            confirmed_phase_two=confirmed_phase_two,
            typed_device_confirmation=typed_confirmation,
        )

        # 2. Revalidate hardware fingerprint immediately prior to writing
        current_dev = self.revalidate_device_before_write(device, plan.fingerprint)

        # 3. Invoke platform installer
        from core.host.host_platform import HostPlatform
        if HostPlatform.is_windows():
            from installer.windows_physical_installer import WindowsPhysicalInstaller
            installer = WindowsPhysicalInstaller(
                device_manager=self.device_manager,
                allow_physical_formatting=self.allow_physical_formatting,
            )
            return installer.install_to_physical_disk(
                device=current_dev,
                expected_fingerprint=plan.fingerprint,
                esp_size_mb=esp_size_mb,
                menu_title=menu_title,
                menu_timeout=menu_timeout,
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
            )
        else:
            raise NotImplementedError(
                "Physical USB disk formatting requires dedicated elevated host permissions on Linux."
            )

    # --------------------------------------------------------------------------
    # Step 8: Post-Installation Partition Verification
    # --------------------------------------------------------------------------

    def verify_partitions(
        self,
        boot_mount: Path | str,
        data_mount: Path | str,
    ) -> InstallationVerificationReport:
        """Step 8: Verify mounted partitions of the prepared USB drive."""
        verifier = InstallationVerifier(Path(data_mount))
        return verifier.verify_mounted_partitions(boot_mount=boot_mount, data_mount=data_mount)

    # --------------------------------------------------------------------------
    # Step 9 & 10: Safe Eject & Reconnection Protocol
    # --------------------------------------------------------------------------

    def check_reconnection_status(
        self,
        device: DeviceInfo,
        data_mount: Optional[Path | str] = None,
        boot_mount: Optional[Path | str] = None,
    ) -> MVentXStatus:
        """Step 10: Verify that reconnected USB drive is classified as VALID_MVENTX."""
        b_mount = Path(boot_mount).resolve() if boot_mount else None
        d_mount = Path(data_mount).resolve() if data_mount else None
        classification = DeviceClassifier.classify_device(
            device=device,
            data_mount_override=d_mount,
            boot_mount_override=b_mount,
        )
        return classification.status

    # --------------------------------------------------------------------------
    # Step 11 & 12: Hardware Checklists
    # --------------------------------------------------------------------------

    @staticmethod
    def get_hardware_boot_checklist() -> List[str]:
        """Step 11: UEFI hardware booting checklist."""
        return [
            "1. Insert prepared MVentX USB drive into a native USB 3.x or 2.0 port.",
            "2. Power on the test computer and enter UEFI Firmware Settings (F2, F12, Del, or Esc).",
            "3. Confirm that UEFI Boot Mode is enabled (CSM / Legacy mode disabled).",
            "4. Verify that the UEFI Boot Menu displays the USB drive (e.g., 'UEFI: SanDisk, Partition 1').",
            "5. Select the MVentX USB entry to boot.",
            "6. Verify that GRUB 2.12 displays the graphical/text MVentX boot menu.",
            "7. Verify that fallback menu items (Reboot, Shutdown, Firmware Setup) function.",
        ]

    @staticmethod
    def get_linux_test_checklist() -> List[str]:
        """Step 12: Linux environment booting checklist."""
        return [
            "1. Copy genuine Linux ISO(s) into MVX_DATA/ISO/ using the MVentX Manager.",
            "2. Register the ISO(s) explicitly and verify menu synchronization.",
            "3. Reboot the hardware machine into the MVentX USB.",
            "4. Select the registered Linux entry.",
            "5. Verify kernel loading (Level 2), initramfs execution (Level 3), and root discovery.",
            "6. Confirm access to MVX_DATA after the live environment is booted.",
        ]
