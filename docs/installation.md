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
> Installing MVentX to a physical USB drive is a destructive operation that will erase all existing partitions on the target device. Always verify the selected drive before confirmation.

### USB Provisioning Process

1. **Insert Target USB Drive**: Connect a USB flash drive (minimum recommended size: 16 GB).
2. **Administrator Privileges**: Physical drive repartitioning requires elevated privileges (Run as Administrator on Windows, `sudo` on Linux).
3. **Partition Scheme Selection**:
   - **GPT (Default & Recommended)**: Standard for UEFI systems with 1 MiB alignment.
4. **Partition Execution**:
   - **Partition 1 (`MVX_BOOT`)**: Formatted FAT32 (64 MB – 512 MB), Partition Type GUID `C12A7328-F81F-11D2-BA4B-00A0C93EC93B` (EFI System Partition).
   - **Partition 2 (`MVX_DATA`)**: Formatted exFAT (remaining drive capacity), Partition Type GUID `EBD0A0A2-B9E5-4433-87C0-68B6B72699C7` (Microsoft Basic Data).
5. **Bootloader Installation**:
   - GRUB 2 EFI binary copied to `/EFI/BOOT/BOOTX64.EFI` on `MVX_BOOT`.
   - Core GRUB fonts and configuration deployed to `/boot/grub/grub.cfg`.
   - Initial boot menu compiled to `/boot/grub/mventx_menu.cfg`.
6. **Data Partition Initialization**:
   - Standard directory tree initialized:
     - `/ISO/`: User directory for storing ISO images.
     - `/config/mventx.json`: Authoritative environment configuration created from `defaults.json`.
     - `/metadata/`: Directory for ISO metadata, checksums, and icons.
     - `/backups/`: Directory for automatic atomic backups prior to configuration modifications.

---

## 3. Virtual Disk Creation for Development & Testing (Rootless)

For day-to-day development, testing, and CI, MVentX provides a rootless, pure-Python dual-partition virtual disk generator that does **not** require administrator privileges, WSL, or kernel loopback mounts.

### Native Windows 11:
```powershell
# Create 256 MB GPT dual-partition virtual disk (MVX_BOOT FAT32 + MVX_DATA exFAT):
powershell -ExecutionPolicy Bypass -File scripts\windows\create_test_disk.ps1

# Run deep sector-level structural validation:
python -m core.storage.disk_validator build\mventx_test.img

# Test Windows VHD conversion and loopback mount lifecycle:
powershell -ExecutionPolicy Bypass -File scripts\windows\test_windows_mount.ps1

# Test UEFI boot in QEMU:
powershell -ExecutionPolicy Bypass -File scripts\windows\run_qemu.ps1
```

### Linux:
```bash
# Create dual-partition virtual disk:
bash scripts/create_test_disk.sh

# Test UEFI boot in QEMU:
bash scripts/run_qemu.sh
```

---

## 4. Post-Installation Verification

Once installed onto physical USB or generated as a virtual disk:
1. Re-insert the USB drive into your host machine.
2. The `MVX_DATA` partition will mount automatically in your file explorer.
3. Open `mventx-manager` (or use `StorageManager`) to register ISO files.
4. Reboot the host or launch a QEMU test instance to confirm GRUB 2 boots cleanly into the dynamic menu.

