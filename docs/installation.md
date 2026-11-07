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

---

## 5. Windows Physical USB Installation Pipeline

The MVentX physical USB installation subsystem (`installer/windows_physical_installer.py`) implements a secure 12-step partitioning and bootloader deployment workflow on Windows 10/11:

### Multi-Layer Safety Architecture
1. **Policy Lock**: Physical device formatting is locked by default (`allow_physical_formatting=False`). Physical drives cannot be modified unless explicitly unlocked in configuration or via the UI policy toggle.
2. **Administrator Privilege Enforcement**: Physical disk operations require elevated Administrator rights. If executed in an unprivileged context, the operation is blocked immediately with an informative elevation prompt.
3. **System Disk Protection Shield**: The installer queries the Windows storage bus via PowerShell storage cmdlets (`Get-Disk`, `Get-Partition`, `Get-Volume`). Disks with `IsSystem=True`, `IsBoot=True`, or hosting an active system volume (`C:\`) are unconditionally rejected.
4. **Bus Type Enforcement**: Targets must have bus type `USB`. Fixed SATA, NVMe, and non-removable internal drives are rejected.
5. **Pre-Write Cryptographic Fingerprint Revalidation**: Prior to performing any destructive disk write, the installer re-queries the physical disk to verify hardware serial, vendor, model, capacity, and GPT disk GUID. If the drive was unplugged, swapped, or moved, the write operation is aborted.
6. **Two-Phase Confirmation**:
   - Phase 1: Explicit safety agreement checkbox.
   - Phase 2: High-contrast modal dialog requiring the user to type the exact device model name or `YES`.

### 12-Step Execution Pipeline
1. **Validate Host Environment & Target**: Verify Windows platform, Administrator elevation, and device safety attributes.
2. **Generate Partition Layout Plan**: Calculate 1 MiB aligned LBA boundaries for Partition 1 (`MVX_BOOT`) and Partition 2 (`MVX_DATA`).
3. **Revalidate Hardware Identity**: Confirm disk presence and compare SHA-256 fingerprint.
4. **Dismount Active Volumes**: Safely dismount existing file handles and partition volumes on target disk.
5. **Clear Disk & Initialize GPT**: Execute `Clear-Disk -RemoveData -RemoveOEM` and `Initialize-Disk -PartitionStyle GPT`.
6. **Allocate Partitions**:
   - Partition 1: `New-Partition -Size ($espSize * 1MB) -GptType '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}'`
   - Partition 2: `New-Partition -UseMaximumSize -GptType '{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}'`
7. **Format Filesystems**:
   - `Format-Volume -Partition $p1 -FileSystem FAT32 -NewFileSystemLabel 'MVX_BOOT'`
   - `Format-Volume -Partition $p2 -FileSystem exFAT -NewFileSystemLabel 'MVX_DATA'`
8. **Deploy GRUB 2.12 Bootloader**: Install `/EFI/BOOT/BOOTX64.EFI`, runtime modules, Unicode fonts, and primary `grub.cfg`.
9. **Initialize Directory Structure**: Create `ISO/`, `config/`, `metadata/`, and `backups/` directories on `MVX_DATA`.
10. **Write Configuration**: Generate authoritative `mventx.json` with version metadata and empty environment registry.
11. **Generate Boot Menu**: Compile fallback `mventx_menu.cfg` on `MVX_BOOT`.
12. **Verify Installation**: Execute `InstallationVerifier` to validate partition labels, filesystem readability, and bootloader binaries.

---

## 6. Manual Testing Verification Guide for Target Hardware

### Target Hardware Profile (Context Only — Never Hardcoded)
- **Manufacturer**: SanDisk
- **Model**: Cruzer Blade
- **Physical Disk Index**: Disk 1 (`\\.\PHYSICALDRIVE1`)
- **Total Capacity**: 63,065,554,944 bytes (~58.73 GiB)
- **Serial Number**: 03026216101525041338
- **Bus Type**: USB

### Prerequisites
1. Windows 11 PC with an available native USB-A port.
2. Python 3.14 virtual environment with PySide6 and dependencies installed.
3. Open an elevated PowerShell terminal (**Run as administrator**).
4. Backup any personal files from the USB drive before proceeding.

### Step-by-Step UI Workflow
1. Launch the MVentX Manager as Administrator:
   ```powershell
   python -m manager.app
   ```
2. In the left navigation sidebar, click **USB Installer**.
3. In the Safety Banner at the top, check the box:
   `[✓] Enable physical USB storage device preparation (Destructive Policy Unlock)`
4. Select the radio button:
   `(*) Physical Storage Media (Removable USB Drives) [Destructive Mode]`
5. In the **Target Physical USB** dropdown, select your USB drive:
   `SanDisk Cruzer Blade (58.7 GB) - \\.\PhysicalDrive1`
   *(Notice: The dropdown begins on `-- Select target USB storage device --` and never auto-selects a device).*
6. Review the **Hardware Identity & Attributes** card:
   - Manufacturer: SanDisk
   - Model: Cruzer Blade
   - Capacity: ~58.73 GB
   - Serial: 03026216101525041338
   - Bus Type: USB
7. Review the **Partition Layout Preview**:
   - `MVX_BOOT`: FAT32 ESP, 64 MiB
   - `MVX_DATA`: exFAT, ~58.67 GB
8. Check the Phase 1 confirmation box:
   `[✓] I understand that proceeding will initialize the partition table and format the target storage.`
9. Click **Start Installation & Verification**.
10. In the Phase 2 modal confirmation dialog:
    - Review the planned destructive operations.
    - Type `YES` or `SanDisk Cruzer Blade` into the confirmation field.
    - Click **I Understand, Wipe & Prepare USB**.
11. Observe the live 12-step progress bar and status messages.
12. Once complete, verify that the **Verification Report** indicates all checks passed (100% Valid).

### Post-Installation OS Verification Commands
Open PowerShell and verify the prepared partitions:
```powershell
# 1. Inspect physical disk partition scheme
Get-Disk | Where-Object BusType -eq 'USB' | Select-Object Number, FriendlyName, PartitionStyle, Size

# Expected Output:
# Number FriendlyName       PartitionStyle         Size
# ------ ------------       --------------         ----
#      1 SanDisk Cruzer Blade GPT           63065554944

# 2. Inspect created partitions
Get-Partition -DiskNumber 1 | Select-Object PartitionNumber, DriveLetter, GptType, Size

# Expected Output:
# PartitionNumber DriveLetter GptType                                Size
# --------------- ----------- -------                                ----
#               1 E           {c12a7328-f81f-11d2-ba4b-00a0c93ec93b}   67108864
#               2 F           {ebd0a0a2-b9e5-4433-87c0-68b6b72699c7} 62998446080

# 3. Inspect volume labels and filesystems
Get-Volume | Where-Object FileSystemLabel -like 'MVX_*' | Select-Object DriveLetter, FileSystemLabel, FileSystem, Size

# Expected Output:
# DriveLetter FileSystemLabel FileSystem        Size
# ----------- --------------- ----------        ----
# E           MVX_BOOT        FAT32         67108864
# F           MVX_DATA        exFAT      62998446080
```

### GRUB EFI File Placement Verification
Verify the bootloader structure on `MVX_BOOT` (e.g., drive `E:`):
```powershell
Get-ChildItem -Recurse E:\EFI

# Expected files:
# E:\EFI\BOOT\BOOTX64.EFI
# E:\EFI\BOOT\grub.cfg
# E:\EFI\MVX\grubx64.efi
# E:\EFI\MVX\grub.cfg
# E:\EFI\MVX\x86_64-efi\*.mod
# E:\EFI\MVX\fonts\unicode.pf2
```

### QEMU Virtual Boot Test
Before testing on real hardware, verify the physical disk boots in QEMU with disk pass-through:
```powershell
# Boot physical disk directly in QEMU with UEFI OVMF:
qemu-system-x86_64 `
  -bios "C:\Program Files\qemu\share\edk2-x86_64-code.fd" `
  -drive file=\\.\PhysicalDrive1,format=raw,if=virtio `
  -m 2048 `
  -vga std
```
Expected result: QEMU displays the GRUB 2.12 graphical bootloader menu titled "MVentX Multiboot System".

### Real Hardware UEFI Boot Checklist
1. Click **Safely Eject** in the MVentX Manager (or use the Windows system tray "Safely Remove Hardware").
2. Disconnect and re-insert the USB drive into a native USB 3.x or 2.0 port.
3. Verify that MVentX Manager detects the drive and displays status `VALID_MVENTX`.
4. Copy a genuine Linux ISO (e.g. `ubuntu-24.04-desktop-amd64.iso`) into `MVX_DATA\ISO\` and register it.
5. Restart your PC and press the UEFI boot key (`F12`, `F2`, `Del`, or `Esc` depending on motherboard).
6. Disable Legacy / CSM mode and ensure UEFI Boot Mode is active.
7. Select `UEFI: SanDisk, Partition 1` from the motherboard boot device selection menu.
8. Verify that GRUB 2.12 starts and displays the registered Linux environment.
9. Select the entry and verify kernel loading, initramfs discovery, and desktop execution.


