# Boot Test Suite (`tests/boot/`)

This directory contains automated boot tests executed inside QEMU / KVM virtual machines.

## Purpose
- Validate that generated disk images properly boot through OVMF (Open Virtual Machine Firmware / UEFI).
- Verify that GRUB 2 loads the theme, recognizes the FAT32/exFAT partitions, and renders menu entries.
- Test loopback boot sequence and capture serial console output to ensure kernels load without panics.

