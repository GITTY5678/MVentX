#!/usr/bin/env python3
"""CLI utility to generate GRUB menu configuration from an MVentX JSON configuration.

Usage:
    python scripts/generate_test_menu.py [--config PATH] [--output PATH] [--sample]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.boot_config.generator import GrubMenuGenerator


SAMPLE_CONFIG = {
    "version": "1.0.0",
    "meta": {
        "title": "MVentX Multiboot System (Milestone 1 Prototype)",
        "theme": "mventx",
        "timeout": 10,
        "default_entry_id": "prototype-linux",
    },
    "environments": [
        {
            "id": "prototype-linux",
            "display_name": "Test Environment: Minimal Linux Workstation",
            "type": "iso",
            "iso_path": "isos/sample-workstation.iso",
            "boot_method": "linux_generic_loopback",
            "enabled": True,
            "order": 1,
            "description": "Registered Linux diagnostic and recovery environment",
            "custom_args": "quiet splash",
        },
        {
            "id": "prototype-recovery",
            "display_name": "Test Environment: System Rescue & Offline Tools",
            "type": "iso",
            "iso_path": "isos/rescue-suite.iso",
            "boot_method": "linux_generic_loopback",
            "enabled": True,
            "order": 2,
            "description": "Hardware diagnostics and partition maintenance toolkit",
        },
        {
            "id": "prototype-chainload",
            "display_name": "Test Environment: Host OS Chainloader",
            "type": "chainload",
            "target_path": "/EFI/Microsoft/Boot/bootmgfw.efi",
            "target_hint": "hd1,gpt1",
            "boot_method": "chainload_efi",
            "enabled": False,
            "order": 3,
            "description": "Optional chainload to local disk (disabled by default)",
        },
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate MVentX GRUB menu configuration")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to mventx.json (defaults to built-in sample config)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "boot" / "grub" / "mventx_menu.cfg",
        help="Output path for mventx_menu.cfg",
    )
    parser.add_argument(
        "--empty",
        action="store_true",
        help="Generate empty configuration to test fallback menu",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print generated GRUB script to stdout instead of writing to file",
    )

    args = parser.parse_args()
    generator = GrubMenuGenerator()

    if args.empty:
        config_data = {
            "version": "1.0.0",
            "meta": {"title": "MVentX Multiboot System (Empty Configuration)", "timeout": 10},
            "environments": [],
        }
    elif args.config:
        config_data = generator.read_config_file(args.config)
    else:
        config_data = SAMPLE_CONFIG

    if args.stdout:
        script = generator.generate_menu_script(config_data)
        sys.stdout.write(script)
        return 0

    generator.write_menu_file(config_data, args.output)
    print(f"Successfully generated MVentX menu configuration at: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

