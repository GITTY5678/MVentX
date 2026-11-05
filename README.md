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
- **Filesystem Layout**:
  - Partition 1 (`MVX_BOOT`): FAT32 for EFI bootloader, GRUB binaries, and generated configuration.
  - Partition 2 (`MVX_DATA`): exFAT (or NTFS/ext4) for user ISO storage and environment payload storage.
- **Testing & Verification**: QEMU virtualized UEFI boot testing on both Windows and Linux

---

## Repository Structure

```
MVentX/
├── docs/             # Technical specifications, architecture, and guides
├── boot/             # GRUB 2 bootloader configs, EFI structures, and boot assets
├── core/             # Core Python business logic (ISO manager, storage, config)
├── manager/          # PySide6 desktop GUI and management service layer
├── installer/        # USB provisioning, partition manager, and bootloader installer
├── boot_methods/     # Modular boot strategies (Linux loopback, Windows, chainloading)
├── tests/            # Unit, integration, and QEMU-based boot test suites
├── scripts/          # Developer tooling and build scripts
│   ├── windows/      # Native Windows 11 PowerShell automation & FAT builder
│   │   ├── create_test_disk.ps1
│   │   ├── run_qemu.ps1
│   │   └── make_fat_disk.py
│   ├── create_test_disk.sh
│   ├── run_qemu.sh
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

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Boot Process Specification](docs/boot-process.md)
- [Installation Guide](docs/installation.md)
- [Configuration Schema & Reference](docs/configuration.md)
- [Development Roadmap](docs/development-roadmap.md)
- [Technical Decisions & Rationale](docs/technical-decisions.md)
- [QEMU Virtual Boot Testing Guide](tests/boot/README.md)

---

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
