"""Bootloader Installer.

Installs GRUB 2 EFI executable binaries, configuration files, and support
modules onto the MVX_BOOT FAT32 partition.
"""

from pathlib import Path


class BootloaderInstaller:
    """Deploys GRUB 2 bootloader assets to the EFI system partition."""

    def __init__(self, boot_mount_path: Path) -> None:
        self.boot_mount_path = boot_mount_path

    def deploy_efi_binaries(self) -> None:
        """Copy BOOTX64.EFI and required EFI modules into /EFI/BOOT/."""
        raise NotImplementedError("Implementation reserved for Phase 4.")

    def deploy_grub_structure(self) -> None:
        """Create /boot/grub/ layout and copy base grub.cfg, modules, and themes."""
        raise NotImplementedError("Implementation reserved for Phase 4.")

