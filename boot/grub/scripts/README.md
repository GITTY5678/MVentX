# GRUB Scripts Directory (`boot/grub/scripts/`)

This directory contains reusable GRUB script snippets (`.cfg` files) and boot recipes.

## Roles
- `detect_hardware.cfg`: Diagnostic detection of CPU architecture, EFI vs BIOS, and active RAM.
- `disk_probe.cfg`: Probing routines to locate internal disk partitions for chainloading.
- `iso_helpers.cfg`: Helper functions for loopback configuration and parameter substitution.

