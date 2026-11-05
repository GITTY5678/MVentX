# MVentX Installation Guide

This document describes how to prepare and install the MVentX platform on a USB storage device, as well as installing the host management tools.

---

## 1. Host Management Tool Installation

The MVentX desktop management application runs on Linux, macOS, and Windows.

### Prerequisites
- Python 3.10 or higher
- Qt 6 runtime libraries (automatically satisfied via PySide6 wheels)
- Administrative privileges for low-level USB partition provisioning (during installer operations only)

### Installation from Source

1. Clone repository:
   ```bash
   git clone https://github.com/mventx/mventx.git
   cd mventx
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. Install MVentX in editable mode:
   ```bash
   pip install -e .
   ```

4. Launch the manager UI:
   ```bash
   mventx-manager
   ```

---

## 2. Installing MVentX onto a USB Drive

> [!CAUTION]
> Installing MVentX to a USB drive is a destructive operation that will erase all existing partitions on the target device. Always double-check target disk paths.

### USB Provisioning Process

1. **Insert Target USB Drive**: Connect a USB flash drive (minimum recommended size: 16 GB).
2. **Launch MVentX Installer**:
   - Using the GUI: Select **Tools > Install MVentX to Drive**.
   - Using the CLI installer:
     ```bash
     python -m installer.usb_installer --device /dev/sdX  # Linux
     ```
3. **Partition Scheme Selection**:
   - **GPT (Default)**: Modern standard for pure UEFI systems.
   - **MBR**: Legacy hybrid compatibility mode for older hardware.
4. **Partition Execution**:
   - Partition 1 (`MVX_BOOT`): Formatted FAT32 (~256 MB), marked with EFI System Partition (ESP) type code.
   - Partition 2 (`MVX_DATA`): Formatted exFAT (remaining capacity) for storing ISO images and `mventx.json`.
5. **Bootloader Installation**:
   - GRUB 2 EFI binary copied to `/EFI/BOOT/BOOTX64.EFI` on `MVX_BOOT`.
   - Core GRUB modules and themes deployed to `/boot/grub/`.
   - Base `grub.cfg` deployed.
6. **Configuration Initialization**:
   - `defaults.json` copied to `/mventx/mventx.json` on `MVX_DATA`.
   - Empty directory `/isos/` created on `MVX_DATA`.

---

## 3. Post-Installation Verification

Once installed:
1. Re-insert the USB drive into your host machine.
2. The `MVX_DATA` partition will mount automatically in your file explorer.
3. Open `mventx-manager` to register ISO files.
4. Reboot the host or launch a QEMU test instance to confirm GRUB 2 boots properly.

