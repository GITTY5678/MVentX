# MVentX (Multi-Environment Boot Experience)

MVentX is an open-source, customizable multiboot USB platform inspired by Ventoy.

It allows users to initialize a USB drive once and subsequently manage bootable environments—including ISO images and chainloaded operating systems—without needing to reformat or reinstall the drive. Users have granular control to manually add, register, rename, enable, disable, reorder, and remove bootable targets.

---

## Key Principles & Design Philosophy

1. **User Control**: Users have complete ownership over the boot menu. No operating system or distribution is mandatory, and no arbitrary boot entries are hardcoded.
2. **Explicit Registration**: MVentX adopts a manual registration model rather than arbitrary filesystem auto-discovery. Only environments explicitly registered by the user appear in the boot menu.
3. **Decoupled Architecture**: Boot-time components (GRUB 2 / UEFI) run entirely independently of the host-level management application (Python / PySide6). GRUB does not depend on Python or desktop UI runtimes.
4. **Self-Contained Storage**: All configuration, metadata, and registered ISO files reside on the target USB drive itself, ensuring portability across multiple host machines.
5. **Safety First**: Non-destructive operation is prioritized. Boot configurations are validated before deployment, and existing configurations are backed up prior to modification. Destructive USB formatting operations are restricted to dedicated initial installer routines.

---

## Technology Stack

- **Bootloader**: GRUB 2 (Modular EFI & BIOS configuration)
- **Firmware Integration**: UEFI (x86_64, with support for legacy BIOS/CSM chainloading)
- **Management Suite**: Python 3.10+
- **Desktop UI**: PySide6 (Qt 6)
- **Configuration & Metadata**: JSON with JSON Schema validation
- **Filesystem Layout (Dual-Partition GPT)**:
  - Partition 1 (`MVX_BOOT`): FAT32 EFI System Partition (ESP) for UEFI bootloader (`BOOTX64.EFI`), GRUB binaries, fonts, and synchronized `mventx_menu.cfg`.
  - Partition 2 (`MVX_DATA`): exFAT Microsoft Basic Data Partition for user ISO storage (`ISO/`), authoritative environment configuration (`config/mventx.json`), metadata (`metadata/`), and atomic backups (`backups/`).
- **Testing & Verification**: QEMU virtualized UEFI boot testing on both Windows and Linux using pure-Python rootless disk creation.

---

## Repository Structure

```
MVentX/
├── docs/             # Technical specifications, architecture, and guides
│   ├── architecture.md
│   ├── boot-process.md
│   ├── configuration.md
│   ├── development-roadmap.md
│   ├── installation.md
│   └── technical-decisions.md
├── boot/             # GRUB 2 bootloader configs, EFI structures, and boot assets
│   ├── efi/          # BOOTX64.EFI bootloader binary
│   └── grub/         # grub.cfg, fonts, and generated menu scripts
├── core/             # Core Python business logic
│   ├── boot_config/  # Native GRUB configuration generator & escaping
│   ├── config/       # Schema validation, default settings, and loaders
│   ├── iso_manager/  # ISO metadata extraction and validation
│   └── storage/      # Dual-partition manager, Win32 drive detection, disk validator
├── manager/          # PySide6 desktop GUI and management service layer
├── installer/        # USB provisioning, partition manager, and bootloader installer
├── boot_methods/     # Modular boot strategies (Linux loopback, Windows, chainloading)
├── tests/            # Unit, integration, and QEMU-based boot test suites
│   ├── unit/         # Unit tests (config, generator, storage manager, validator)
│   ├── integration/  # End-to-end integration tests
│   └── boot/         # QEMU UEFI boot testing guides and scripts
├── scripts/          # Developer tooling and build scripts
│   ├── windows/      # Native Windows 11 PowerShell automation & disk builders
│   │   ├── create_test_disk.ps1
│   │   ├── run_qemu.ps1
│   │   ├── test_windows_mount.ps1
│   │   ├── convert_to_vhd.py
│   │   ├── make_dual_partition_disk.py
│   │   └── make_fat_disk.py
│   ├── create_test_disk.sh
│   ├── run_qemu.sh
│   ├── test_qemu_harness.py # QEMU automated boot harness and screendump verifier
│   ├── manage_iso.py        # ISO environment registration & management CLI
│   └── generate_test_menu.py
└── resources/        # Icons, logos, and UI/GRUB themes
```

---

## Development & Testing Workflow

### Native Windows 11 Setup

MVentX development and Milestone 1 virtual boot testing run natively on Windows 11 without requiring WSL2, Cygwin, MSYS2, or Administrator privileges.

#### 1. Prerequisites (Windows)
- **Python 3.10+**: Install via winget or python.org:
  ```powershell
  winget install Python.Python.3.12
  ```
- **QEMU**:
  ```powershell
  winget install SoftwareFreedomConservancy.QEMU
  ```
- **OVMF UEFI Firmware**:
  Included with QEMU Windows distributions (e.g. `C:\Program Files\qemu\share\edk2-x86_64-code.fd`) or downloadable from [rust-osdev/ovmf-prebuilt](https://github.com/rust-osdev/ovmf-prebuilt/releases) to `build\ovmf\OVMF.fd`.
- **GRUB EFI Binary (`BOOTX64.EFI`)**:
  - Option A: Run `create_test_disk.ps1 -DownloadGrub` to download official GNU GRUB for Windows and compile `BOOTX64.EFI` automatically.
  - Option B: Place any pre-compiled 64-bit GRUB EFI binary into `boot\efi\BOOTX64.EFI`.

#### 2. Running Python Tests on Windows
```powershell
# Run unit and integration tests
python -m unittest discover tests
```

#### 3. Building the Virtual Disk (Native Windows)
```powershell
# Create virtual disk with sample registered environments:
powershell -ExecutionPolicy Bypass -File scripts\windows\create_test_disk.ps1

# Or test the empty fallback menu behavior:
powershell -ExecutionPolicy Bypass -File scripts\windows\create_test_disk.ps1 -Empty
```

#### 4. Running QEMU UEFI Boot (Native Windows)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\run_qemu.ps1
```

#### 5. Managing ISO Environments (CLI)
```powershell
# Validate an ISO file structure and filesystem signatures:
python scripts\manage_iso.py validate C:\path\to\ubuntu.iso

# View partition storage metrics and registered environment status:
python scripts\manage_iso.py --data build\data status

# List all registered environments in display sequence:
python scripts\manage_iso.py --data build\data list

# Manually register an ISO file (never auto-registered):
python scripts\manage_iso.py --data build\data --boot build\esp register ubuntu-24.04-desktop-amd64.iso --name "Ubuntu 24.04 LTS"

# Rename an environment:
python scripts\manage_iso.py --data build\data --boot build\esp rename ubuntu_24_04 "Ubuntu 24.04 LTS Noble"

# Enable / Disable an environment:
python scripts\manage_iso.py --data build\data --boot build\esp disable ubuntu_24_04
python scripts\manage_iso.py --data build\data --boot build\esp enable ubuntu_24_04

# Remove registration (preserves ISO file by default):
python scripts\manage_iso.py --data build\data --boot build\esp remove ubuntu_24_04
```

#### 6. Launching the PySide6 Desktop Application
```powershell
# Launch targeting detected physical USB drives (or automatically fallback to virtual staging):
python manager\main.py

# Or launch explicitly targeting virtual test directories:
python manager\main.py --data build\data --boot build\esp

#### 7. Storage Validation & Windows Interoperability
```powershell
# Run the complete automated test suite (240 tests):
python -m unittest discover tests

# Deep sector-level structural validation of GPT, FAT32, and exFAT:
python -m core.storage.disk_validator build\mventx_test.img

# Direct sector read of configuration out of raw exFAT clusters:
python -m core.storage.disk_validator build\mventx_test.img --read-exfat config/mventx.json

# Test Windows VHD/VHDX conversion and loopback mount lifecycle:
powershell -ExecutionPolicy Bypass -File .\scripts\windows\test_windows_mount.ps1
```

#### 8. Automated QEMU Boot Verification Harness (Milestones 6, 7 & 12)
```powershell
# Run automated virtual UEFI boot harness across all scenarios and verify GRUB graphics:
python scripts/test_qemu_harness.py --scenario all

# Run real Linux ISO loopback boot verification in QEMU across distribution families:
python -m unittest tests/integration/test_linux_loopback_boot.py
python -m unittest tests/integration/test_multidistro_loopback.py

# Interactive windowed UEFI virtual boot:
powershell -ExecutionPolicy Bypass -File .\scripts\windows\run_qemu.ps1
```

---

## Operating System Compatibility & Verification Levels

MVentX evaluates distribution boot compatibility across six standardized verification levels:

- **L0: Recipe Implemented**: GRUB 2 loopback parameters defined and syntactically validated.
- **L1: ISO Structure Inspected**: Filesystem inspected with Rock Ridge (`SUSP/RRIP`) Alternate Name parsing; kernel and initramfs discovered.
- **L2: Kernel Loaded**: Kernel decompressed into memory and execution initiated under UEFI firmware.
- **L3: Initramfs Initialized**: Initramfs loaded, device discovery performed, and early user-space started.
- **L4: Installer / Shell Reached**: Text/graphical installer or interactive recovery shell reached.
- **L5: Full Live Desktop**: Graphical desktop environment (e.g., GNOME, KDE, Cinnamon) fully interactive.

### Supported Distributions Matrix

| Distribution Family | Representative Distributions | Recipe ID | Boot Parameters / Discovery | Verification Level |
| :--- | :--- | :--- | :--- | :--- |
| **Casper** | Ubuntu Desktop, Linux Mint, Pop!_OS, Zorin OS, Elementary OS | `casper` | `iso-scan/filename=${iso_path} boot=casper` | **L4 (Installer Reached)** |
| **Debian Live-Boot** | Debian Live, Clonezilla, Kali Linux, Rescuezilla | `live_boot` | `boot=live findiso=${iso_path} union=overlay` | **L3 (Initramfs Initialized)** |
| **Alpine Linux** | Alpine Linux Virt, Alpine Standard | `alpine` | `modules=loop,squashfs modloop=/boot/modloop-virt` | **L3 (Initramfs Initialized)** |
| **ArchISO** | Arch Linux, Manjaro, EndeavourOS | `archiso` | `img_dev=/dev/disk/by-label/MVX_DATA img_loop=${iso_path}` | **L1 (Inspected / L2 Ready)** |
| **SystemRescue** | SystemRescue (v6.0+) | `systemrescue` | `img_dev=... img_loop=... archisobasedir=sysrescue` | **L1 (ArchISO Architecture)** |
| **Dracut** | Fedora Workstation, RHEL, CentOS, Rocky Linux, AlmaLinux | `dracut` | `root=live:CDLABEL=${vol_id} iso-scan.filename=${iso_path}` | **L1 (Inspected / L2 Ready)** |
| **openSUSE** | openSUSE Leap, openSUSE Tumbleweed Live | `opensuse` | `root=live:CDLABEL=${vol_id} iso-scan.filename=${iso_path}` | **L0 (Recipe Implemented)** |
| **MX Linux** | MX Linux, antiX Live | `mx_linux` | `fromiso=${iso_path} bootdev=MVX_DATA` | **L0 (Recipe Implemented)** |
| **Windows Installer** | Windows 10/11 x64 Installation Media | `windows_extracted` | Direct UEFI Chainloading (`bootmgfw.efi`) | **PAUSED (Experimental / Unsupported)** |

> [!NOTE]
> **Windows Installation ISO Boot Decision**: In accordance with Project Decision 2.1, Windows installation ISO booting is **paused** and classified as experimental/unsupported. Because Microsoft Windows Boot Manager lacks an embedded exFAT driver and delegates I/O to UEFI firmware (`0xc00000bb` / `STATUS_NOT_SUPPORTED`), Windows installation media is not advertised as a supported production feature. Standard chainloading of existing Windows EFI installations remains functional.

---

### Linux (Ubuntu 24.04 LTS / Debian) Setup

#### 1. Prerequisites (Linux)
```bash
sudo apt update
sudo apt install -y qemu-system-x86 ovmf grub-efi-amd64-bin mtools dosfstools parted python3
```

#### 2. Running Python Tests on Linux
```bash
python3 -m unittest discover tests
```

#### 3. Building the Virtual Disk & Running QEMU (Linux)
```bash
# Build virtual disk
bash scripts/create_test_disk.sh

# Run QEMU UEFI virtual machine
bash scripts/run_qemu.sh
```

---

## Feature Verification Matrix

| Feature / Subsystem | Status | Verification Level | Notes |
|---|---|---|---|
| **GPT Dual-Partition Layout** | Production | **Physically verified** | Verified on SanDisk Cruzer Blade 64GB (FAT32 ESP + exFAT Data) |
| **Windows Physical Installer & Recovery** | Production | **Physically verified** | State-aware 7-state installation and resume engine |
| **Official GNU GRUB 2.12 EFI Bootloader** | Production | **Physically verified** | Boots cleanly to gfxterm on physical HP laptop hardware |
| **Command Capability Registry** | Production | **Build-verified & QEMU-verified** | Validates built-in vs dynamic modules, manifests, and dependencies |
| **Reboot System Utility (`reboot`)** | Production | **QEMU-verified** | Hardware cold reset verified in QEMU via UEFI Runtime Services |
| **Halt / Power Off Utility (`halt`)** | Production | **QEMU-verified** | System halt & ACPI D3/S5 poweroff verified with clean VM exit |
| **Exit GRUB Utility (`exit`)** | Production | **QEMU-verified** | Return to UEFI Boot Manager / firmware dispatcher verified |
| **UEFI Firmware Settings (`fwsetup`)** | Production | **QEMU-verified** | Firmware setup request with fallback notice for unsupported systems |
| **exFAT Dynamic Module (`exfat.mod`)** | Production | **QEMU-verified** | Dynamic module load and `MVX_DATA` discovery verified |
| **Linux ISO Loopback Boot** | Production | **QEMU-verified** | Casper, ArchISO, Dracut, Live-Boot recipes implemented |
| **Secure Boot (Enabled)** | In Progress | **Not yet supported** | Requires signed shim / Microsoft 3rd-party UEFI CA certificate |
| **Windows Installation ISO Direct Boot** | Paused | **Not yet supported** | Paused per Decision 2.1 (UEFI exFAT loader limitations) |

---

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Boot Process Specification](docs/boot-process.md)
- [Installation Guide](docs/installation.md)
- [Configuration Schema & Reference](docs/configuration.md)
- [Development Roadmap](docs/development-roadmap.md)
- [Milestone 18: GRUB Command Integration & System Utilities](docs/milestone-18-grub-command-integration.md)
- [Milestone 17.1: Windows Physical Installer Reliability](docs/milestone-17-1-windows-physical-installer-reliability.md)
- [Milestone 17.2: Physical Installation Resume](docs/milestone-17-2-physical-installation-resume.md)
- [Milestone 5: Storage Validation Report](docs/milestone-5-storage-validation.md)
- [Milestone 6: End-to-End Integration Report](docs/milestone-6-end-to-end-integration.md)
- [Milestone 7: Linux ISO Loopback Boot Report](docs/milestone-7-linux-boot.md)
- [Milestone 8: Linux Compatibility Report](docs/milestone-8-linux-compatibility.md)
- [Milestone 9: USB Installation and Preparation Report](docs/milestone-9-usb-installation.md)
- [Milestone 10: Cross-Platform Host Integration Report](docs/milestone-10-host-integration.md)
- [Milestone 11: Windows Boot Compatibility Report](docs/milestone-11-windows-boot.md)
- [Milestone 11: Windows Verification Report](docs/milestone-11-verification.md)
- [Milestone 12: Linux Distribution Compatibility Report](docs/milestone-12-linux-compatibility.md)
- [Milestone 13: Physical USB Validation Report](docs/milestone-13-physical-usb-validation.md)
- [Milestone 14: Desktop Manager Maturity Report](docs/milestone-14-desktop-manager.md)
- [Milestone 15: Recovery and Diagnostics Report](docs/milestone-15-recovery-diagnostics.md)
- [Milestone 16: Cross-Platform Host Validation Report](docs/milestone-16-cross-platform-validation.md)
- [Technical Decisions & Rationale](docs/technical-decisions.md)
- [QEMU Virtual Boot Testing Guide](tests/boot/README.md)

---

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
