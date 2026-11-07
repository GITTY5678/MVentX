"""Windows GRUB Boot Configuration Generator for MVentX.

Generates native GRUB 2 script commands for booting Windows installation media
and host Windows Boot Manager chainloading with robust partition discovery,
path verification, and defensive error screens.
"""

from __future__ import annotations

import re
from typing import List, Optional

from core.boot_config.windows_boot_strategy import WindowsBootStrategy

# Safe pattern preventing GRUB script injection
_SAFE_STRING_REGEX = re.compile(r"^[a-zA-Z0-9_\-./ ]+$")


def sanitize_windows_path(path: str) -> str:
    """Sanitize and normalize a path for GRUB script output."""
    stripped = path.strip().replace("\\", "/")
    if not stripped.startswith("/"):
        stripped = f"/{stripped}"
    # Escape quotes and dollar signs
    return stripped.replace('"', '\\"').replace("$", "\\$")


def generate_windows_boot_commands(
    strategy: WindowsBootStrategy,
    bootloader_path: str = "/efi/boot/bootx64.efi",
    staged_subfolder: Optional[str] = None,
    custom_name: str = "Windows Setup",
    data_partition_label: str = "MVX_DATA",
) -> List[str]:
    """Generate resilient GRUB 2 script lines for booting a Windows environment.

    Args:
        strategy: Selected Windows boot strategy.
        bootloader_path: Path to bootloader relative to partition root.
        staged_subfolder: Optional subfolder if media is staged in a subdirectory.
        custom_name: Display name for diagnostic screens.
        data_partition_label: Volume label of the data partition.

    Returns:
        List of formatted GRUB script lines.
    """
    clean_bootloader = sanitize_windows_path(bootloader_path)
    clean_label = data_partition_label.strip()

    if strategy == WindowsBootStrategy.HOST_CHAINLOAD:
        return [
            '    echo "Locating internal Windows Boot Manager on host EFI system partition..."',
            '    set host_esp=""',
            '    if search --no-floppy --file --set=host_esp /EFI/Microsoft/Boot/bootmgfw.efi; then',
            '        echo "  Found Windows Boot Manager on device: ($host_esp)"',
            '        echo "  Chainloading Windows Boot Manager..."',
            '        chainloader "($host_esp)/EFI/Microsoft/Boot/bootmgfw.efi"',
            '        boot',
            '    else',
            '        echo ""',
            '        echo "=========================================================="',
            '        echo "  [ERROR] Host Windows Boot Manager Not Found             "',
            '        echo "=========================================================="',
            '        echo ""',
            '        echo "  Could not locate /EFI/Microsoft/Boot/bootmgfw.efi on any"',
            '        echo "  connected EFI system partition."',
            '        echo ""',
            '        echo "  Press Enter or ESC to return to main menu."',
            '        sleep 5',
            '    fi',
        ]

    # Strategy: EXTRACTED_MEDIA
    full_path = clean_bootloader
    if staged_subfolder:
        clean_sub = staged_subfolder.strip().replace("\\", "/").strip("/")
        full_path = f"/{clean_sub}{clean_bootloader}"

    lines = [
        f'    echo "Locating MVentX storage partition ({clean_label})..."',
        f'    set win_root=""',
        f'    if search --no-floppy --label --set=win_root {clean_label}; then',
        f'        true',
        f'    elif [ -n "$mventx_data_dev" ]; then',
        f'        set win_root="$mventx_data_dev"',
        f'    else',
        f'        echo ""',
        f'        echo "=========================================================="',
        f'        echo "  [ERROR] Storage Partition Not Found: {clean_label}     "',
        f'        echo "=========================================================="',
        f'        echo ""',
        f'        echo "  The data partition holding Windows files could not be located."',
        f'        echo "  Press Enter or ESC to return to main menu."',
        f'        sleep 5',
        f'        return',
        f'    fi',
        f'',
        f'    echo "  Partition found: ($win_root)"',
        f'    echo "  Inspecting Windows bootloader: {full_path}"',
        f'    if [ ! -f "($win_root){full_path}" ]; then',
        f'        echo ""',
        f'        echo "=========================================================="',
        f'        echo "  [ERROR] Windows Bootloader Missing                      "',
        f'        echo "=========================================================="',
        f'        echo ""',
        f'        echo "  Expected bootloader not found at: ($win_root){full_path}"',
        f'        echo "  Verify that Windows installation media has been extracted."',
        f'        echo ""',
        f'        echo "  Press Enter or ESC to return to main menu."',
        f'        sleep 5',
        f'        return',
        f'    fi',
        f'',
        f'    echo "  Bootloader verified. Handing execution to Windows Boot Manager..."',
        f'    set root="($win_root)"',
        f'    chainloader "($win_root){full_path}"',
        f'    boot',
    ]

    return lines

