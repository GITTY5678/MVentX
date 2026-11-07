"""Windows Boot Strategy Definitions, BCD Path Resolution, and Verification Taxonomy for MVentX.

Encapsulates:
1. Windows boot strategies and technical trade-offs.
2. Six-tier verification taxonomy distinguishing inspection, extraction, menu generation,
   boot manager execution, WinPE execution, and interactive setup reach.
3. BCD path resolution analysis for extracted installation media.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.iso_manager.windows_iso_inspector import (
    WindowsArchitecture,
    WindowsInspectionResult,
    WindowsPayloadType,
)


class WindowsBootStrategy(str, Enum):
    """Supported Windows boot architectures."""
    EXTRACTED_MEDIA = "windows_extracted_media"  # Primary: Extracted files on MVX_DATA with direct UEFI chainload
    WIMBOOT_PE = "windows_wimboot"              # Dedicated WinPE RAM disk boot
    HOST_CHAINLOAD = "windows_host_chainload"    # Chainload existing host Windows Boot Manager on internal ESP


class WindowsVerificationLevel(int, Enum):
    """Six-tier verification taxonomy for Windows boot compatibility."""
    LEVEL_1_ISO_INSPECTED = 1           # Windows ISO structures, EFI bootloader, and WIM payload verified
    LEVEL_2_MEDIA_EXTRACTED = 2         # Files staged onto MVX_DATA with integrity check
    LEVEL_3_MENU_GENERATED = 3          # GRUB menu configuration compiled and syntax validated
    LEVEL_4_BOOT_MANAGER_EXECUTED = 4   # Windows Boot Manager (bootmgfw.efi) executed under UEFI firmware
    LEVEL_5_WINPE_EXECUTED = 5          # Windows PE kernel (ntoskrnl.exe) loaded and initialized
    LEVEL_6_SETUP_INTERACTIVE = 6       # Windows Setup initial interactive UI reached with source detected


@dataclass
class BCDPathAnalysis:
    """Evaluates BCD references and volume path resolution for extracted media."""
    requires_root_bcd: bool = True
    bcd_relative_path: str = "EFI/Microsoft/Boot/BCD"
    fallback_bcd_path: str = "Boot/BCD"
    boot_wim_relative_path: str = "sources/boot.wim"
    exfat_compatible: bool = True
    is_valid: bool = True
    diagnostics: List[str] = field(default_factory=list)


class WindowsStrategyEvaluator:
    """Evaluates Windows media and selects appropriate boot strategy."""

    @classmethod
    def evaluate_bcd_resolution(cls, extracted_root: Path | str) -> BCDPathAnalysis:
        """Analyze BCD paths on target volume.

        Microsoft's UEFI boot manager (bootmgfw.efi / bootx64.efi) searches for its BCD
        at '\\EFI\\Microsoft\\Boot\\BCD' or '\\Boot\\BCD' relative to the root of the volume
        from which it is executed. When Windows media is staged, having standard BCD paths
        at the root of MVX_DATA ensures the boot manager can locate boot.wim without error.
        """
        root = Path(extracted_root).resolve()
        diagnostics = []

        primary_bcd = root / "EFI" / "Microsoft" / "Boot" / "BCD"
        fallback_bcd = root / "Boot" / "BCD"
        boot_wim = root / "sources" / "boot.wim"

        has_bcd = primary_bcd.is_file() or fallback_bcd.is_file()
        has_wim = boot_wim.is_file()

        if not has_bcd:
            diagnostics.append("BCD store not found in standard root paths (\\EFI\\Microsoft\\Boot\\BCD or \\Boot\\BCD).")
        if not has_wim:
            diagnostics.append("Windows PE image not found at standard path (\\sources\\boot.wim).")

        return BCDPathAnalysis(
            requires_root_bcd=True,
            bcd_relative_path="EFI/Microsoft/Boot/BCD",
            fallback_bcd_path="Boot/BCD",
            boot_wim_relative_path="sources/boot.wim",
            exfat_compatible=True,
            is_valid=(has_bcd and has_wim),
            diagnostics=diagnostics,
        )

    @classmethod
    def select_strategy(cls, inspection: WindowsInspectionResult) -> WindowsBootStrategy:
        """Select best boot strategy based on ISO inspection findings."""
        if not inspection.is_windows_media or not inspection.has_uefi_boot:
            return WindowsBootStrategy.EXTRACTED_MEDIA

        # Primary strategy: extracted media with direct UEFI chainloading
        return WindowsBootStrategy.EXTRACTED_MEDIA

    @classmethod
    def validate_strategy_support(
        cls,
        strategy: WindowsBootStrategy,
        inspection: WindowsInspectionResult,
    ) -> Tuple[bool, List[str]]:
        """Validate if selected strategy is technically viable for this media."""
        reasons: List[str] = []

        if strategy == WindowsBootStrategy.EXTRACTED_MEDIA:
            if not inspection.has_uefi_boot:
                reasons.append("Media lacks UEFI 64-bit bootloader (bootx64.efi).")
            if not inspection.has_winpe:
                reasons.append("Media lacks Windows PE environment (sources/boot.wim).")
            if inspection.architecture not in (WindowsArchitecture.X64, WindowsArchitecture.MULTI_ARCH):
                reasons.append(f"Architecture '{inspection.architecture.value}' is not tested for x86_64 UEFI boot.")
        elif strategy == WindowsBootStrategy.WIMBOOT_PE:
            if not inspection.has_winpe:
                reasons.append("Media lacks sources/boot.wim required for WinPE booting.")
        elif strategy == WindowsBootStrategy.HOST_CHAINLOAD:
            pass  # Host chainload operates on internal drive, not ISO

        is_valid = (len(reasons) == 0)
        return is_valid, reasons

