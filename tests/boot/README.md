# MVentX QEMU/KVM Virtual Boot Testing Guide

This directory documents virtualized UEFI boot testing of MVentX using QEMU and OVMF firmware natively across both **Windows 11** and **Linux (Ubuntu 24.04 LTS)**.

---

## 1. Native Windows 11 Testing

MVentX provides first-class support for native Windows development without requiring WSL2, Linux VMs, Cygwin, or Administrator rights.

### 1.1 Prerequisites (Windows)

Install the required native tools using PowerShell:

1. **Python 3.10+**:
   ```powershell
   winget install Python.Python.3.12
   ```

2. **QEMU**:
   ```powershell
   winget install SoftwareFreedomConservancy.QEMU
   ```
   Or install via Chocolatey:
   ```powershell
   choco install qemu
   ```
   Or download the official 64-bit installer from [qemu.org](https://www.qemu.org/download/#windows).

3. **OVMF UEFI Firmware**:
   - QEMU on Windows typically bundles EDK2 firmware at `C:\Program Files\qemu\share\edk2-x86_64-code.fd`.
   - Alternatively, download standalone pre-compiled OVMF from [rust-osdev/ovmf-prebuilt](https://github.com/rust-osdev/ovmf-prebuilt/releases) and place `OVMF-pure-efi.fd` into `build\ovmf\OVMF.fd`.

4. **GRUB EFI Binary (`BOOTX64.EFI`)**:
   - **Option A (Automated)**: Pass `-DownloadGrub` to `create_test_disk.ps1` to download official GNU GRUB for Windows and compile `BOOTX64.EFI` using native `grub-mkimage.exe`.
   - **Option B (Manual)**: Place any standard 64-bit GRUB 2 EFI binary at `boot\efi\BOOTX64.EFI`.

---

### 1.2 Windows Workflow

#### Step 1: Create Virtual Disk
```powershell
# Create disk with sample registered environments:
powershell -ExecutionPolicy Bypass -File scripts\windows\create_test_disk.ps1

# Or create disk with an empty configuration (to verify fallback menu):
powershell -ExecutionPolicy Bypass -File scripts\windows\create_test_disk.ps1 -Empty
```

What this does on Windows:
- Runs `scripts\generate_test_menu.py` to compile `mventx_menu.cfg`.
- Stages the EFI System Partition files into `build\esp\`.
- Invokes `scripts\windows\make_fat_disk.py` to build a valid GPT-partitioned raw disk image (`build\mventx_test.img`) formatted as FAT16/FAT32 without needing admin rights or Linux tools.

#### Step 2: Boot in QEMU
```powershell
# Boot the raw virtual disk image:
powershell -ExecutionPolicy Bypass -File scripts\windows\run_qemu.ps1

# Or boot directly from the staged directory using QEMU virtual FAT driver:
powershell -ExecutionPolicy Bypass -File scripts\windows\run_qemu.ps1 -UseDir

# Or run in headless console mode:
powershell -ExecutionPolicy Bypass -File scripts\windows\run_qemu.ps1 -NoGraphic
```

---

## 2. Linux (Ubuntu 24.04 LTS / Debian) Testing

### 2.1 Prerequisites (Linux)
```bash
sudo apt update
sudo apt install -y \
    qemu-system-x86 \
    ovmf \
    grub-efi-amd64-bin \
    mtools \
    dosfstools \
    parted \
    python3
```

---

### 2.2 Linux Workflow

#### Step 1: Prepare Virtual Disk
```bash
# Build virtual disk with sample registered environments:
bash scripts/create_test_disk.sh

# Or build with an empty fallback configuration:
bash scripts/create_test_disk.sh --empty
```

#### Step 2: Boot with QEMU
```bash
# Graphical display:
bash scripts/run_qemu.sh

# Console mode:
bash scripts/run_qemu.sh --nographic
```

---

## 3. Test Verification Checklist

When QEMU starts (on either Windows or Linux):

- [ ] **UEFI Initialization**: OVMF firmware splash screen appears.
- [ ] **GRUB Execution**: GRUB 2 loads cleanly without dropping into `grub>` rescue shell.
- [ ] **Branding & Menu**: The menu title displays `MVentX Multiboot System` (or configured title).
- [ ] **Entry Filtering**: Only enabled environments appear. Disabled entries are excluded.
- [ ] **Custom Display Names**: User-defined custom display names are rendered accurately.
- [ ] **Selection Feedback**: Selecting an entry displays verified environment metadata and returns cleanly.
- [ ] **Fallback Verification**: With `--empty` / `-Empty`, the "No environments registered or enabled" fallback entry appears.
- [ ] **System Utilities**: The `MVentX System Utilities` submenu allows rebooting or powering down the virtual machine.
