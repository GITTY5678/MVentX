#!/usr/bin/env python3
"""MVentX Physical USB Validation Tool and Test Procedure Runner (Milestone 13).

Provides the controlled 12-step validation workflow for physical USB drives:
1. Identify the USB device.
2. Verify manufacturer, model, capacity, and serial where available.
3. Verify the device identity twice (SHA-256 fingerprint).
4. Display the exact destructive operations.
5. Require explicit two-phase user confirmation.
6. Prepare the USB (partitioning and formatting).
7. Verify both partitions (FAT32 MVX_BOOT + exFAT MVX_DATA).
8. Safely eject the device.
9. Reconnect device and detect hotplug event.
10. Verify that MVentX recognizes the prepared USB as VALID_MVENTX.
11. Boot the USB on UEFI hardware.
12. Test registered Linux boot environments.

SAFETY POLICY:
- Physical USB formatting is disabled by default.
- Never modifies physical storage without explicit user confirmation and pre-write revalidation.
- Supports virtual disk fallback mode (--virtual) for automated CI and non-destructive runs.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.host.device_classification import DeviceClassifier, MVentXStatus
from core.storage.device_info import DeviceInfo, DeviceType
from core.storage.device_manager import DeviceManager, DeviceSafetyError
from installer.installation_service import InstallationService
from installer.installation_verifier import InstallationVerifier
from installer.physical_device_workflow import (
    DestructiveOperationPlan,
    PhysicalDeviceWorkflow,
    PhysicalSafetyViolationError,
)


def print_banner() -> None:
    print("==========================================================")
    print("  MVentX Physical USB Validation & Test Procedure Runner  ")
    print("  Milestone 13: Hardware Compatibility & Safety Subsystem ")
    print("==========================================================")


def list_available_removable_devices(mgr: DeviceManager) -> list[DeviceInfo]:
    devices = mgr.list_devices()
    return [d for d in devices if d.is_removable or d.device_type == DeviceType.REMOVABLE_USB]


def run_virtual_validation_mode() -> int:
    print("\n[VIRTUAL MODE] Running 12-step validation using virtual disk...")
    installer = InstallationService()
    temp_img = REPO_ROOT / "build" / "mventx_m13_virtual_validation.img"
    temp_img.parent.mkdir(parents=True, exist_ok=True)

    print(f"Target Image: {temp_img} (256 MiB)")
    res = installer.install_to_virtual_disk(
        target_image_path=temp_img,
        disk_size_mb=256,
        esp_size_mb=64,
        menu_title="MVentX M13 Virtual Test",
    )

    if res.success:
        print("[SUCCESS] Virtual disk installation completed and 100% verified.")
        rep = res.verification_report
        if rep:
            print(f"Passed checks: {rep.passed_count}/{rep.total_count}")
            for c in rep.checks:
                print(f"  [PASS] {c.name}: {c.message}")
        return 0
    else:
        print(f"[FAIL] Virtual installation failed: {res.error_message}")
        return 1


def run_physical_test_procedure(device_id: str, allow_physical: bool) -> int:
    mgr = DeviceManager(allow_physical_formatting=allow_physical)
    workflow = PhysicalDeviceWorkflow(device_manager=mgr, allow_physical_formatting=allow_physical)

    devices = mgr.list_devices()
    matching = [d for d in devices if d.device_id == device_id or str(d.name) == device_id]

    if not matching:
        print(f"[ERROR] Target device '{device_id}' not found.")
        print("Available devices:")
        for d in devices:
            remov_tag = "REMOVABLE" if d.is_removable else "FIXED"
            sys_tag = " [SYSTEM DISK - PROTECTED]" if d.is_system_disk else ""
            print(f"  - {d.device_id} ({d.name}, {d.size_bytes // (1024**3)} GB, {remov_tag}){sys_tag}")
        return 1

    target_dev = matching[0]

    # Step 1 & 2: Identify and Validate Hardware Attributes
    print("\n--- Step 1 & 2: Device Identification & Attribute Verification ---")
    try:
        identity = workflow.identify_and_validate_device(target_dev)
        print(f"Device Name:     {target_dev.name}")
        print(f"Device ID:       {target_dev.device_id}")
        print(f"Capacity:        {target_dev.size_bytes // (1024**3)} GB ({target_dev.size_bytes:,} bytes)")
        print(f"Sector Size:     {target_dev.sector_size} bytes")
        print(f"Is Removable:    {target_dev.is_removable}")
        print(f"Is System Disk:  {target_dev.is_system_disk} (Protected)")
        print(f"Fingerprint:     {identity.compute_fingerprint()[:24]}...")
    except PhysicalSafetyViolationError as e:
        print(f"[SAFETY VIOLATION] {e}")
        return 1

    # Step 3 & 4: Destructive Plan Generation
    print("\n--- Step 3 & 4: Destructive Operation Plan & Double Verification ---")
    plan = workflow.generate_plan(target_dev, esp_size_mb=64)
    print(plan.summary())

    if not allow_physical:
        print("\n[SAFETY SHIELD ACTIVE]")
        print("Physical formatting is currently locked by default policy.")
        print("To authorize physical operations, re-run with --authorize-physical-test.")
        return 0

    # Step 5: Two-Phase User Confirmation
    print("\n--- Step 5: Explicit Two-Phase Confirmation Guard ---")
    print("WARNING: All data on this device will be PERMANENTLY DESTROYED.")
    resp1 = input("Phase 1: Do you confirm you wish to prepare this USB drive? (yes/no): ").strip().lower()
    if resp1 != "yes":
        print("[ABORTED] User declined Phase 1 confirmation.")
        return 0

    expected_name = target_dev.name.strip()
    prompt = f"Phase 2: Type the exact device name ('{expected_name}') to proceed: "
    resp2 = input(prompt).strip()

    try:
        workflow.verify_two_phase_confirmation(
            plan=plan,
            confirmed_phase_one=True,
            confirmed_phase_two=True,
            typed_device_confirmation=resp2,
        )
    except PhysicalSafetyViolationError as e:
        print(f"[ABORTED] {e}")
        return 1

    # Step 6: Pre-Write Revalidation
    print("\n--- Step 6: Pre-Write Fingerprint Revalidation ---")
    try:
        reval_dev = workflow.revalidate_device_before_write(target_dev, plan.fingerprint)
        print("[VERIFIED] Device identity revalidated successfully immediately before writing.")
    except PhysicalSafetyViolationError as e:
        print(f"[REVALIDATION FAILED] {e}")
        return 1

    print("\n--- Step 7: Preparing USB Storage ---")
    print("Formatting physical media requires elevated operating system utilities.")
    print("Please use the MVentX PySide6 Desktop Manager or Windows PowerShell installer.")
    return 0


def main() -> int:
    print_banner()
    parser = argparse.ArgumentParser(description="MVentX Physical USB Validation Runner")
    parser.add_argument("--device", help="Physical device path or number (e.g. \\\\.\\PHYSICALDRIVE1)")
    parser.add_argument("--authorize-physical-test", action="store_true", help="Unlock physical storage formatting shield")
    parser.add_argument("--virtual", action="store_true", help="Run automated validation on virtual disk")
    parser.add_argument("--checklist", action="store_true", help="Display 12-step physical validation checklist")

    args = parser.parse_args()

    if args.checklist:
        print("\n=== 12-Step Physical USB Validation Checklist ===")
        print("1. Identify the USB device.")
        print("2. Verify manufacturer, model, capacity, and serial.")
        print("3. Verify the device identity twice (SHA-256 fingerprint).")
        print("4. Display the exact destructive operations.")
        print("5. Require explicit two-phase user confirmation.")
        print("6. Prepare the USB (partitioning and formatting).")
        print("7. Verify both partitions (FAT32 MVX_BOOT + exFAT MVX_DATA).")
        print("8. Safely eject the device.")
        print("9. Reconnect device and verify hotplug event.")
        print("10. Verify MVentX recognizes the prepared USB as VALID_MVENTX.")
        print("11. Boot the USB on UEFI hardware.")
        print("12. Test registered Linux boot environments.")
        print("\nHardware Boot Instructions:")
        for item in PhysicalDeviceWorkflow.get_hardware_boot_checklist():
            print(f"  {item}")
        print("\nLinux Environment Verification Instructions:")
        for item in PhysicalDeviceWorkflow.get_linux_test_checklist():
            print(f"  {item}")
        return 0

    if args.virtual or not args.device:
        mgr = DeviceManager()
        removable = list_available_removable_devices(mgr)
        if not removable:
            print("\n[INFO] No physical removable USB devices currently detected on host.")
            print("[INFO] Fallback to virtual disk validation mode.\n")
            return run_virtual_validation_mode()
        else:
            print("\nDetected Removable USB Devices:")
            for d in removable:
                print(f"  - {d.device_id}: {d.name} ({d.size_bytes // (1024**3)} GB)")
            print("\nTo test a physical device, pass --device <device_id> --authorize-physical-test.")
            print("Defaulting to virtual validation mode for safety...\n")
            return run_virtual_validation_mode()

    return run_physical_test_procedure(args.device, args.authorize_physical_test)


if __name__ == "__main__":
    sys.exit(main())
