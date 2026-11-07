"""Unit tests for Windows Boot Strategy Evaluator and BCD Analysis (Milestone 11).

Tests strategy selection, 6-tier verification taxonomy, BCD path evaluation,
and architecture constraints.
"""

from pathlib import Path
import tempfile
import unittest

from core.boot_config.windows_boot_strategy import (
    BCDPathAnalysis,
    WindowsBootStrategy,
    WindowsStrategyEvaluator,
    WindowsVerificationLevel,
)
from core.iso_manager.windows_iso_inspector import (
    WindowsArchitecture,
    WindowsInspectionResult,
    WindowsPayloadType,
)


class TestWindowsBootStrategy(unittest.TestCase):
    """Test suite for WindowsBootStrategy, BCD analysis, and taxonomy."""

    def test_verification_levels_taxonomy(self):
        """Verify the 6 verification levels exist in strict hierarchical order."""
        levels = list(WindowsVerificationLevel)
        self.assertEqual(len(levels), 6)
        self.assertEqual(WindowsVerificationLevel.LEVEL_1_ISO_INSPECTED.value, 1)
        self.assertEqual(WindowsVerificationLevel.LEVEL_2_MEDIA_EXTRACTED.value, 2)
        self.assertEqual(WindowsVerificationLevel.LEVEL_3_MENU_GENERATED.value, 3)
        self.assertEqual(WindowsVerificationLevel.LEVEL_4_BOOT_MANAGER_EXECUTED.value, 4)
        self.assertEqual(WindowsVerificationLevel.LEVEL_5_WINPE_EXECUTED.value, 5)
        self.assertEqual(WindowsVerificationLevel.LEVEL_6_SETUP_INTERACTIVE.value, 6)

    def test_strategy_selection_for_x64_windows(self):
        """Standard x64 Windows installer selects EXTRACTED_MEDIA strategy."""
        insp = WindowsInspectionResult(
            is_windows_media=True,
            architecture=WindowsArchitecture.X64,
            payload_type=WindowsPayloadType.WIM,
            has_uefi_boot=True,
            has_winpe=True,
            has_install_payload=True,
            efi_bootloader_path="/efi/boot/bootx64.efi",
            bcd_path="/efi/microsoft/boot/bcd",
            boot_wim_path="/sources/boot.wim",
            install_image_path="/sources/install.wim",
        )
        strategy = WindowsStrategyEvaluator.select_strategy(insp)
        self.assertEqual(strategy, WindowsBootStrategy.EXTRACTED_MEDIA)

        is_valid, reasons = WindowsStrategyEvaluator.validate_strategy_support(strategy, insp)
        self.assertTrue(is_valid)
        self.assertEqual(len(reasons), 0)

    def test_strategy_validation_fails_when_lacking_uefi(self):
        """Validates error reporting when media lacks UEFI bootloader."""
        insp = WindowsInspectionResult(
            is_windows_media=True,
            architecture=WindowsArchitecture.X64,
            has_uefi_boot=False,
            has_winpe=True,
        )
        is_valid, reasons = WindowsStrategyEvaluator.validate_strategy_support(
            WindowsBootStrategy.EXTRACTED_MEDIA, insp
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("UEFI" in r for r in reasons))

    def test_strategy_validation_fails_when_lacking_winpe(self):
        """Validates error reporting when media lacks WinPE boot.wim."""
        insp = WindowsInspectionResult(
            is_windows_media=True,
            architecture=WindowsArchitecture.X64,
            has_uefi_boot=True,
            has_winpe=False,
        )
        is_valid, reasons = WindowsStrategyEvaluator.validate_strategy_support(
            WindowsBootStrategy.EXTRACTED_MEDIA, insp
        )
        self.assertFalse(is_valid)
        self.assertTrue(any("Windows PE" in r for r in reasons))

    def test_bcd_resolution_evaluation(self):
        """Verify BCD path analysis on extracted directory tree."""
        with tempfile.TemporaryDirectory(prefix="mventx_bcd_test_") as tmp_dir:
            root = Path(tmp_dir)

            # Initially empty directory should fail BCD and boot.wim checks
            analysis_empty = WindowsStrategyEvaluator.evaluate_bcd_resolution(root)
            self.assertFalse(analysis_empty.is_valid)
            self.assertTrue(len(analysis_empty.diagnostics) >= 2)

            # Populate standard EFI BCD and sources/boot.wim
            (root / "EFI" / "Microsoft" / "Boot").mkdir(parents=True)
            (root / "sources").mkdir(parents=True)
            (root / "EFI" / "Microsoft" / "Boot" / "BCD").write_bytes(b"MOCK_BCD")
            (root / "sources" / "boot.wim").write_bytes(b"MOCK_BOOT_WIM")

            analysis_valid = WindowsStrategyEvaluator.evaluate_bcd_resolution(root)
            self.assertTrue(analysis_valid.is_valid)
            self.assertEqual(len(analysis_valid.diagnostics), 0)
            self.assertTrue(analysis_valid.exfat_compatible)

    def test_bcd_fallback_path(self):
        """Verify BCD resolution succeeds when BCD is at fallback /Boot/BCD."""
        with tempfile.TemporaryDirectory(prefix="mventx_bcd_fallback_") as tmp_dir:
            root = Path(tmp_dir)
            (root / "Boot").mkdir(parents=True)
            (root / "sources").mkdir(parents=True)
            (root / "Boot" / "BCD").write_bytes(b"MOCK_FALLBACK_BCD")
            (root / "sources" / "boot.wim").write_bytes(b"MOCK_BOOT_WIM")

            analysis = WindowsStrategyEvaluator.evaluate_bcd_resolution(root)
            self.assertTrue(analysis.is_valid)
            self.assertEqual(len(analysis.diagnostics), 0)


if __name__ == "__main__":
    unittest.main()

