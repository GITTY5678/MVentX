"""Bootloader Installer for MVentX.

Installs GRUB 2 EFI executable binaries, primary grub.cfg, dynamic modules,
terminal fonts, and initial fallback menu onto the MVX_BOOT FAT32 EFI partition.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Optional

from core.boot_config.generator import GrubMenuGenerator

REPO_ROOT = Path(__file__).resolve().parent.parent


class BootloaderInstallError(Exception):
    """Raised when bootloader file staging fails."""


class BootloaderInstaller:
    """Deploys GRUB 2 EFI bootloader assets and default configuration to MVX_BOOT."""

    def __init__(self, boot_mount_path: Path | str, assets_root: Optional[Path] = None) -> None:
        self.boot_mount = Path(boot_mount_path).resolve()
        self.assets_root = assets_root or REPO_ROOT

    def deploy_all(self, title: str = "MVentX Multiboot System", timeout: int = 10) -> None:
        """Execute complete bootloader deployment sequence."""
        self.deploy_directories()
        self.deploy_efi_binaries()
        self.deploy_grub_configuration()
        self.deploy_modules()
        self.deploy_fonts()
        self.deploy_initial_menu(title=title, timeout=timeout)

    def deploy_directories(self) -> None:
        """Create standard EFI and GRUB directory layout."""
        (self.boot_mount / "EFI" / "BOOT").mkdir(parents=True, exist_ok=True)
        (self.boot_mount / "boot" / "grub" / "x86_64-efi").mkdir(parents=True, exist_ok=True)
        (self.boot_mount / "boot" / "grub" / "fonts").mkdir(parents=True, exist_ok=True)

    def deploy_efi_binaries(self) -> Path:
        """Copy BOOTX64.EFI to /EFI/BOOT/BOOTX64.EFI."""
        candidates = [
            self.assets_root / "boot" / "efi" / "BOOTX64.EFI",
            self.assets_root / "build" / "esp" / "EFI" / "BOOT" / "BOOTX64.EFI",
        ]
        source_efi = next((c for c in candidates if c.is_file()), None)
        if not source_efi:
            raise BootloaderInstallError(
                f"BOOTX64.EFI binary not found in repository. Searched: {[str(c) for c in candidates]}"
            )

        target = self.boot_mount / "EFI" / "BOOT" / "BOOTX64.EFI"
        shutil.copy2(source_efi, target)
        return target

    def deploy_grub_configuration(self) -> Path:
        """Copy primary static grub.cfg to /boot/grub/grub.cfg."""
        source_cfg = self.assets_root / "boot" / "grub" / "grub.cfg"
        if not source_cfg.is_file():
            raise BootloaderInstallError(f"Static grub.cfg not found at: {source_cfg}")

        target = self.boot_mount / "boot" / "grub" / "grub.cfg"
        shutil.copy2(source_cfg, target)
        return target

    def deploy_modules(self) -> int:
        """Copy runtime GRUB dynamic modules (.mod) to /boot/grub/x86_64-efi/."""
        candidates = [
            self.assets_root / "boot" / "grub" / "x86_64-efi",
            self.assets_root / "build" / "grub-windows" / "grub-2.12-for-windows" / "x86_64-efi",
        ]
        source_mod_dir = next((c for c in candidates if c.is_dir()), None)
        if not source_mod_dir:
            raise BootloaderInstallError("GRUB runtime modules directory (x86_64-efi) not found.")

        target_dir = self.boot_mount / "boot" / "grub" / "x86_64-efi"
        count = 0
        for mod_file in source_mod_dir.glob("*.mod"):
            shutil.copy2(mod_file, target_dir / mod_file.name)
            count += 1

        if count == 0:
            raise BootloaderInstallError(f"No .mod files found in {source_mod_dir}")
        return count

    def deploy_fonts(self) -> Optional[Path]:
        """Copy ASCII/Unicode font to /boot/grub/fonts/."""
        candidates = [
            self.assets_root / "build" / "esp" / "boot" / "grub" / "fonts" / "ascii.pf2",
            self.assets_root / "boot" / "grub" / "fonts" / "ascii.pf2",
        ]
        source_font = next((c for c in candidates if c.is_file()), None)
        if source_font:
            target = self.boot_mount / "boot" / "grub" / "fonts" / "ascii.pf2"
            shutil.copy2(source_font, target)
            return target
        return None

    def deploy_initial_menu(self, title: str = "MVentX Multiboot System", timeout: int = 10) -> Path:
        """Generate and write initial fallback mventx_menu.cfg (0 registered environments)."""
        initial_config = {
            "version": "1.0.0",
            "meta": {
                "title": title,
                "timeout": timeout,
            },
            "environments": [],
        }
        target_path = self.boot_mount / "boot" / "grub" / "mventx_menu.cfg"
        generator = GrubMenuGenerator()
        generator.write_menu_file(initial_config, target_path)
        return target_path
