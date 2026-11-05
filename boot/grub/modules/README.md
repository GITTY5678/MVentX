# GRUB Modules Directory (`boot/grub/modules/`)

This directory is reserved for modular dynamic GRUB 2 modules (`.mod` files) bundled with the MVentX bootloader installation.

## Included / Required Modules
- `part_gpt.mod`, `part_msdos.mod`: Partition table support
- `fat.mod`, `exfat.mod`, `ext2.mod`, `ntfs.mod`: Filesystem drivers
- `iso9660.mod`: Optical disc filesystem support
- `loopback.mod`: Loopback image mounting support
- `gfxmenu.mod`, `gfxterm.mod`: Graphical menu support
- `wimboot.mod` (optional / future): Windows Imaging Format bootloader module

In production installations, modules are installed by `installer/bootloader_installer.py`.

