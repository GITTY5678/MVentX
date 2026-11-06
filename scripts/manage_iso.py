#!/usr/bin/env python3
"""CLI utility to manage MVentX ISO environments on the data partition.

Usage examples:
    # Validate an ISO image:
    python scripts/manage_iso.py validate /path/to/distro.iso

    # Show partition status and storage metrics:
    python scripts/manage_iso.py --data build/data status

    # List registered environments:
    python scripts/manage_iso.py --data build/data list

    # Register an ISO file manually:
    python scripts/manage_iso.py --data build/data --boot build/esp register ubuntu.iso --name "Ubuntu 24.04 LTS"

    # Rename an environment:
    python scripts/manage_iso.py --data build/data --boot build/esp rename ubuntu_24_04 "Ubuntu 24.04 LTS Noble"

    # Enable or disable an environment:
    python scripts/manage_iso.py --data build/data --boot build/esp disable ubuntu_24_04
    python scripts/manage_iso.py --data build/data --boot build/esp enable ubuntu_24_04

    # Remove registration (preserves ISO file by default):
    python scripts/manage_iso.py --data build/data --boot build/esp remove ubuntu_24_04
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.iso_manager import (
    DuplicateRegistrationError,
    EnvironmentNotFoundError,
    ISORegistrationManager,
    ISOValidator,
    MenuSyncRollbackError,
    PathTraversalError,
    RegistrationError,
)
from core.storage.storage_manager import StorageManager


def _format_bytes(num_bytes: int) -> str:
    """Format bytes into human-readable string (KiB, MiB, GiB)."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PiB"


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate an ISO file."""
    iso_file = Path(args.iso_path).resolve()
    print(f"[*] Validating ISO image: {iso_file}")
    res = ISOValidator.validate_iso(iso_file)

    print(f"  Valid: {res.is_valid}")
    print(f"  Size: {_format_bytes(res.file_size_bytes)} ({res.file_size_bytes} bytes)")
    print(f"  Filesystem Format: {res.filesystem_format}")
    if res.volume_identifier:
        print(f"  Volume ID: {res.volume_identifier}")
    if res.system_identifier:
        print(f"  System ID: {res.system_identifier}")
    print(f"  El Torito Boot Catalog: {'Present' if res.has_el_torito_boot else 'Not Detected'}")
    print(f"  Notice: {res.disclaimer}")

    if res.errors:
        print("\nErrors:")
        for err in res.errors:
            print(f"  - {err}")
        return 1

    if args.checksum:
        sha256 = ISOValidator.calculate_checksum(iso_file, "sha256")
        print(f"  SHA-256: {sha256}")

    return 0


def cmd_status(manager: ISORegistrationManager) -> int:
    """Display storage usage, registered ISO metrics, and missing/unregistered ISOs."""
    metrics = manager.get_storage_metrics()
    missing = manager.detect_missing_isos()
    unregistered = manager.list_unregistered_isos()

    print("==========================================================")
    print(" MVentX Storage & ISO Status                              ")
    print("==========================================================")
    print(f"Data Partition: {manager.storage_manager.data_mount}")
    print(f"  Total Capacity:     {_format_bytes(metrics.total_bytes)}")
    print(f"  Used Capacity:      {_format_bytes(metrics.used_bytes)}")
    print(f"  Available Capacity: {_format_bytes(metrics.available_bytes)}")
    print("----------------------------------------------------------")
    print(f"Registered ISO Environments: {metrics.registered_iso_count}")
    print(f"Total Registered ISO Size:   {_format_bytes(metrics.total_registered_iso_bytes)}")
    print(f"Missing Registered ISOs:     {metrics.missing_iso_count}")
    print(f"Unregistered ISO Files:      {len(unregistered)}")
    print("==========================================================")

    if missing:
        print("\n[!] WARNING: Missing Registered ISOs detected:")
        for env_id, iso_path in missing:
            print(f"  - [{env_id}] Configured at '{iso_path}' (file not found on disk)")

    if unregistered:
        print("\n[*] Unregistered ISO files found in ISO directory:")
        for unreg in unregistered:
            print(f"  - {unreg.name} ({_format_bytes(unreg.stat().st_size)})")
        print("  (Run 'register' to add an ISO to the active boot menu)")

    return 0


def cmd_list(manager: ISORegistrationManager) -> int:
    """List registered environments in order."""
    envs = manager.list_environments()
    if not envs:
        print("No environments currently registered in mventx.json.")
        return 0

    print(f"Registered Environments ({len(envs)} total):")
    for env in envs:
        status_str = "ENABLED" if env.enabled else "DISABLED"
        size_str = f", size: {_format_bytes(env.file_size_bytes)}" if env.file_size_bytes else ""
        print(f"  #{env.order} [{status_str}] ID: {env.id}")
        print(f"     Name: {env.display_name}")
        print(f"     Type: {env.type} | Method: {env.boot_method}{size_str}")
        if env.iso_path:
            print(f"     ISO:  {env.iso_path}")
        if env.description:
            print(f"     Desc: {env.description}")
        print()
    return 0


def cmd_register(manager: ISORegistrationManager, args: argparse.Namespace) -> int:
    """Register an ISO file manually."""
    try:
        record = manager.register_iso(
            iso_path=args.iso_file,
            display_name=args.name,
            boot_method=args.boot_method,
            custom_args=args.args,
            description=args.description,
            enabled=not args.disabled,
            calculate_hash=args.hash,
        )
        print(f"[+] Successfully registered environment '{record.id}' (#{record.order})")
        print(f"    Display Name: {record.display_name}")
        print(f"    ISO Path:     {record.iso_path}")
        print(f"    Status:       {'ENABLED' if record.enabled else 'DISABLED'}")
        return 0
    except (RegistrationError, ValueError, FileNotFoundError) as exc:
        print(f"[!] Registration failed: {exc}", file=sys.stderr)
        return 1


def cmd_rename(manager: ISORegistrationManager, args: argparse.Namespace) -> int:
    try:
        updated = manager.rename_environment(args.env_id, args.new_name)
        print(f"[+] Successfully renamed '{args.env_id}' to: '{updated.display_name}'")
        return 0
    except (EnvironmentNotFoundError, ValueError) as exc:
        print(f"[!] Rename failed: {exc}", file=sys.stderr)
        return 1


def cmd_enable(manager: ISORegistrationManager, args: argparse.Namespace) -> int:
    try:
        updated = manager.enable_environment(args.env_id)
        print(f"[+] Enabled environment: '{updated.id}' ({updated.display_name})")
        return 0
    except (EnvironmentNotFoundError, ValueError) as exc:
        print(f"[!] Failed to enable: {exc}", file=sys.stderr)
        return 1


def cmd_disable(manager: ISORegistrationManager, args: argparse.Namespace) -> int:
    try:
        updated = manager.disable_environment(args.env_id)
        print(f"[+] Disabled environment: '{updated.id}' ({updated.display_name})")
        return 0
    except (EnvironmentNotFoundError, ValueError) as exc:
        print(f"[!] Failed to disable: {exc}", file=sys.stderr)
        return 1


def cmd_reorder(manager: ISORegistrationManager, args: argparse.Namespace) -> int:
    try:
        reordered = manager.reorder_environments(args.ordered_ids)
        print("[+] Successfully reordered environments:")
        for env in reordered:
            print(f"  #{env.order} [{env.id}] {env.display_name}")
        return 0
    except ValueError as exc:
        print(f"[!] Reorder failed: {exc}", file=sys.stderr)
        return 1


def cmd_remove(manager: ISORegistrationManager, args: argparse.Namespace) -> int:
    try:
        manager.remove_environment(
            args.env_id,
            delete_file=args.delete_file,
            confirm_deletion=args.confirm,
        )
        msg = f"[+] Deregistered environment '{args.env_id}'"
        if args.delete_file and args.confirm:
            msg += " (underlying ISO file deleted)"
        else:
            msg += " (ISO file preserved on disk)"
        print(msg)
        return 0
    except (EnvironmentNotFoundError, ValueError) as exc:
        print(f"[!] Removal failed: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="MVentX ISO Environment Management CLI")
    parser.add_argument(
        "--data",
        type=Path,
        default=REPO_ROOT / "build" / "data",
        help="Path to MVX_DATA mount or staging directory",
    )
    parser.add_argument(
        "--boot",
        type=Path,
        default=REPO_ROOT / "build" / "esp",
        help="Path to MVX_BOOT mount or staging directory",
    )

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # 1. validate
    p_val = subparsers.add_parser("validate", help="Validate an ISO file without registering")
    p_val.add_argument("iso_path", type=Path, help="Path to the ISO file to inspect")
    p_val.add_argument("--checksum", action="store_true", help="Calculate SHA-256 checksum")

    # 2. status
    subparsers.add_parser("status", help="Show storage capacity and ISO counts")

    # 3. list
    subparsers.add_parser("list", help="List registered environments")

    # 4. register
    p_reg = subparsers.add_parser("register", help="Manually register an ISO image")
    p_reg.add_argument("iso_file", help="Filename or relative path inside MVX_DATA/ISO/")
    p_reg.add_argument("--name", required=True, help="Custom display name for boot menu")
    p_reg.add_argument("--boot-method", default="linux_generic_loopback", help="Boot recipe")
    p_reg.add_argument("--description", default=None, help="Optional release notes / description")
    p_reg.add_argument("--args", default=None, help="Optional kernel boot arguments")
    p_reg.add_argument("--disabled", action="store_true", help="Register as disabled")
    p_reg.add_argument("--hash", action="store_true", help="Compute SHA-256 during registration")

    # 5. rename
    p_ren = subparsers.add_parser("rename", help="Rename an environment")
    p_ren.add_argument("env_id", help="Environment ID to rename")
    p_ren.add_argument("new_name", help="New custom display name")

    # 6. enable
    p_en = subparsers.add_parser("enable", help="Enable an environment in the boot menu")
    p_en.add_argument("env_id", help="Environment ID to enable")

    # 7. disable
    p_dis = subparsers.add_parser("disable", help="Disable an environment from the boot menu")
    p_dis.add_argument("env_id", help="Environment ID to disable")

    # 8. reorder
    p_ord = subparsers.add_parser("reorder", help="Reorder environment display sequence")
    p_ord.add_argument("ordered_ids", nargs="+", help="Complete list of environment IDs in desired order")

    # 9. remove
    p_rem = subparsers.add_parser("remove", help="Remove an environment registration")
    p_rem.add_argument("env_id", help="Environment ID to remove")
    p_rem.add_argument("--delete-file", action="store_true", help="Also delete underlying ISO file")
    p_rem.add_argument("--confirm", action="store_true", help="Confirm deletion of the underlying ISO file")

    args = parser.parse_args()

    if args.subcommand == "validate":
        return cmd_validate(args)

    storage = StorageManager(data_mount=args.data, boot_mount=args.boot)
    manager = ISORegistrationManager(storage)

    if args.subcommand == "status":
        return cmd_status(manager)
    elif args.subcommand == "list":
        return cmd_list(manager)
    elif args.subcommand == "register":
        return cmd_register(manager, args)
    elif args.subcommand == "rename":
        return cmd_rename(manager, args)
    elif args.subcommand == "enable":
        return cmd_enable(manager, args)
    elif args.subcommand == "disable":
        return cmd_disable(manager, args)
    elif args.subcommand == "reorder":
        return cmd_reorder(manager, args)
    elif args.subcommand == "remove":
        return cmd_remove(manager, args)

    return 0


if __name__ == "__main__":
    sys.exit(main())

