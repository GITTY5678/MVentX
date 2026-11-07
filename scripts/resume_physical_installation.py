"""MVentX M17.2: Resume Physical Installation on Formatted Storage Media.

Resumes deployment directly from Step 8 (GRUB Deployment) on an existing
formatted dual-partition GPT USB drive without repartitioning or reformatting.

Target hardware serial: 03026216101525041338 (SanDisk Cruzer Blade)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.host.host_platform import HostPlatform
from core.storage.device_info import DeviceInfo, DeviceType
from core.storage.device_manager import DeviceManager
from installer.installation_recovery import USBInstallationState
from installer.windows_physical_installer import WindowsPhysicalInstaller

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mventx.resume_installer")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MVentX Physical Installation Resume Tool (Milestone 17.2)"
    )
    parser.add_argument(
        "--serial",
        type=str,
        default="03026216101525041338",
        help="Hardware serial number of target USB drive (default: 03026216101525041338)",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="MVentX Multiboot System",
        help="Main bootloader menu title",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Boot menu timeout in seconds",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly authorize physical file deployment. If omitted, runs in read-only inspection mode.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 70)
    print("MVentX M17.2: Physical USB Installation Resume Subsystem")
    print("=" * 70)

    if not HostPlatform.is_windows():
        print("ERROR: This tool is only supported on Windows host platforms.")
        return 1

    if not HostPlatform.is_elevated():
        print("ERROR: Modifying physical storage devices requires Administrator privileges.")
        print("Please relaunch this script from an elevated Administrator PowerShell prompt.")
        return 1

    installer = WindowsPhysicalInstaller(allow_physical_formatting=True)

    print(f"\n[1/4] Resolving physical disk by hardware serial '{args.serial}'...")
    try:
        disk_num, disk_data = installer.resolve_disk_number_by_serial(args.serial)
        print(f"      ✓ Found Disk {disk_num}: {disk_data.get('FriendlyName')} ({disk_data.get('Size'):,} bytes)")
    except Exception as exc:
        print(f"      ✗ Failed resolving disk: {exc}")
        return 1

    print(f"\n[2/4] Diagnosing current USB installation state on Disk {disk_num}...")
    try:
        diag = installer.diagnose_physical_disk(disk_num)
        print(f"      • Partition Style   : {diag.details.get('disk_data', {}).get('PartitionStyle')}")
        print(f"      • Operational Status: {diag.details.get('disk_data', {}).get('OperationalStatus')}")
        print(f"      • Classified State  : {diag.state.value} ({diag.state_name})")
        print(f"      • Recommended Action: {diag.recommended_action}")
    except Exception as exc:
        print(f"      ✗ Diagnosis query failed: {exc}")
        return 1

    print(f"\n[3/4] Revalidating partition layout and mount accessibility on Disk {disk_num}...")
    try:
        boot_mount, data_mount, layout_data = installer.revalidate_formatted_partition_layout(
            disk_number=disk_num,
            expected_serial=args.serial,
        )
        print(f"      ✓ Partition 1: {layout_data.get('boot_label')} ({layout_data.get('boot_fs')}) mounted as {boot_mount}")
        print(f"      ✓ Partition 2: {layout_data.get('data_label')} ({layout_data.get('data_fs')}) mounted as {data_mount}")
    except Exception as exc:
        print(f"      ✗ Layout validation failed (aborting to prevent unintended modification): {exc}")
        return 1

    if not args.execute:
        print("\n" + "=" * 70)
        print("READ-ONLY INSPECTION SUCCESSFUL: NO FILES WERE WRITTEN.")
        print("=" * 70)
        print(f"Target USB ({disk_data.get('FriendlyName')}, Disk {disk_num}) is verified and ready for resume.")
        print("To proceed with GRUB deployment and MVentX file initialization, re-run with:")
        print(f"    python scripts/resume_physical_installation.py --serial {args.serial} --execute")
        print("=" * 70)
        return 0

    print(f"\n[4/4] Executing deployment directly from Step 8 (Zero partitioning or formatting)...")

    # Build target DeviceInfo
    device = DeviceInfo(
        device_id=f"\\\\.\\PHYSICALDRIVE{disk_num}",
        name=disk_data.get("FriendlyName", "USB Storage"),
        size_bytes=int(disk_data.get("Size", 0)),
        device_type=DeviceType.REMOVABLE_USB,
        is_removable=True,
        is_system_disk=False,
        vendor=disk_data.get("Manufacturer", "SanDisk"),
        model=disk_data.get("Model", "Cruzer Blade"),
        serial_number=args.serial,
    )

    def on_progress(step: int, total: int, msg: str) -> None:
        print(f"      [{step}/{total}] {msg}")

    res = installer.resume_deployment_on_formatted_disk(
        device=device,
        menu_title=args.title,
        menu_timeout=args.timeout,
        progress_callback=on_progress,
    )

    print("\n" + "=" * 70)
    if res.success and res.verification_report and res.verification_report.is_valid:
        print("✓ PHYSICAL DEPLOYMENT & VERIFICATION COMPLETED SUCCESSFULLY!")
        print("=" * 70)
        rep = res.verification_report
        print(f"Verification Report: {rep.passed_count}/{rep.total_count} Checks Passed\n")
        for c in rep.checks:
            print(f"  ✓ {c.name}: {c.message}")
        print("\nNext Steps:")
        print("1. Safely eject the USB drive.")
        print("2. Re-insert to verify MVentX recognizes the device as VALID_MVENTX.")
        print("3. Test boot on UEFI target computer.")
        return 0
    else:
        print("✗ PHYSICAL DEPLOYMENT FAILED")
        print("=" * 70)
        print(f"Error: {res.error_message}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

