# MVentX Architecture Specification

## 1. Architectural Philosophy

MVentX (Multi-Environment Boot Experience) is engineered around a core principle: **absolute decoupling between runtime boot environments and offline management tools**.

Traditional multiboot solutions often tightly couple bootloader scripts to proprietary discovery algorithms or platform-specific installation scripts. MVentX adopts a clean, layered architecture:

1. **Boot-Time Subsystem (Independent)**: Pure GRUB 2 and UEFI environment executed upon machine boot. No Python, no Qt, no dynamic OS dependencies.
2. **Storage Subsystem (Self-Contained)**: Dual-partition disk layout storing bootloader binaries, user configurations, and ISO payloads directly on the USB media.
3. **Management Subsystem (Host-Decoupled)**: Desktop user interface (PySide6) and core management engine (Python 3) operating against the USB storage partition via standard filesystem operations.

```
+-------------------------------------------------------------------+
|                        Host Operating System                      |
|                                                                   |
|  +--------------------+        +-------------------------------+  |
|  | PySide6 Desktop UI | -----> | MVentX Core Engine (Python 3) |  |
|  +--------------------+        +---------------+---------------+  |
+------------------------------------------------|------------------+
                                                 | Read / Write JSON & Sync Menu
                                                 v
+-------------------------------------------------------------------+
|                         MVentX USB Drive                          |
|                                                                   |
|  +--------------------------------+  +-------------------------+  |
|  | Partition 1: MVX_BOOT (FAT32)  |  | Partition 2: MVX_DATA   |  |
|  | [ESP / C12A7328-F81F-...]      |  | [Basic / EBD0A0A2-...]  |  |
|  | - /EFI/BOOT/BOOTX64.EFI        |  | - /config/mventx.json   |  |
|  | - /boot/grub/grub.cfg          |  | - /ISO/*.iso            |  |
|  | - /boot/grub/mventx_menu.cfg   |  | - /metadata/            |  |
|  | - /boot/grub/fonts/            |  | - /backups/             |  |
|  +--------------------------------+  +-------------------------+  |
+-------------------------------------------------------------------+
                                                 |
                                                 v
+-------------------------------------------------------------------+
|                       Target Machine Boot                         |
|                                                                   |
|  +------------------+     +------------------+     +-----------+  |
|  | UEFI / BIOS POST | --> | GRUB 2 Execution | --> | Boot Menu |  |
|  +------------------+     +------------------+     +-----------+  |
+-------------------------------------------------------------------+
```

---

## 2. Subsystem Breakdown

### 2.1 Boot Subsystem (`boot/`)
- **Bootloader**: GRUB 2 compiled for UEFI x86_64 (`BOOTX64.EFI`) and optionally IA32 / BIOS i386-pc.
- **Dynamic Configuration Generator**: The core engine reads the JSON registration file and generates a GRUB-compatible script (`mventx_menu.cfg`) that is included by the primary `grub.cfg`.
- **Bootloader Theme & UI**: Graphical GRUB theme rendering entries defined in the generated menu.

### 2.2 Core Subsystem (`core/`)
- **`core/config`**: JSON Schema definitions, configuration parsers, and defaults validation.
- **`core/iso_manager`**: Manual registration, ISO metadata extraction (labels, architectures, checksums), and integrity validation.
- **`core/storage`**: USB device detection, partition mounting/unmounting, and path resolution across platforms.
- **`core/boot_config`**: Converts JSON configuration into native GRUB 2 menu scripts (`mventx_menu.cfg`) using the Linux distribution recipe engine (`recipes.py` for Casper, ArchISO, Dracut, Live-Boot, and Generic).

### 2.3 Management Subsystem (`manager/`)
- **PySide6 Desktop Application**: Modern graphite dark theme GUI built on a decoupled service layer:
  - **`manager/services/device_service.py`**: Manages active target detection, physical USB pairs (`MVX_DATA` / `MVX_BOOT`), and virtual test disk staging mounts.
  - **`manager/services/iso_service.py`**: Mediates all environment operations with `ISORegistrationManager`, `StorageManager`, and `ISOValidator` without duplicating business logic in UI widgets.
  - **`manager/ui/dashboard.py`**: High-level device status, partition health, storage utilization progress, and missing ISO counters.
  - **`manager/ui/environment_library.py`**: Table view of registered boot environments with inline actions (enable/disable, rename, reorder, and non-destructive removal).
  - **`manager/ui/add_environment.py`**: Guided manual ISO registration with instant filesystem signature validation, metadata hints, custom display name entry, and GRUB menu compilation.
  - **`manager/ui/storage_manager.py`**: Detailed partition architecture inspection (FAT32 ESP vs exFAT basic data), capacity breakdowns, and directory integrity checks.
  - **`manager/ui/settings.py`**: Global boot preferences (title, timeout, default environment), timestamped configuration backup management and restore functionality.

### 2.4 Installer Subsystem (`installer/`)
- Responsible only for the initial provisioning of blank USB drives:
  - Partitioning (FAT32 boot partition + exFAT data partition).
  - Deploying GRUB EFI binaries and default directory trees.
  - Writing the initial baseline configuration.
  - **Invariant**: Destructive formatting is strictly quarantined to this module and requires explicit user consent.

---

## 3. Storage Layout & Partitioning Scheme

MVentX employs a dual-partition layout on GPT (GUID Partition Table):

| Partition | Label | Partition Type GUID | Filesystem | Recommended Size | Purpose |
|---|---|---|---|---|---|
| Partition 1 | `MVX_BOOT` | `C12A7328-F81F-11D2-BA4B-00A0C93EC93B` (ESP) | FAT32 | 64 MB – 512 MB | UEFI system partition (`ESP`), GRUB binaries, fonts, base `grub.cfg`, and dynamic `mventx_menu.cfg`. |
| Partition 2 | `MVX_DATA` | `EBD0A0A2-B9E5-4433-87C0-68B6B72699C7` (Basic Data) | exFAT | Remaining drive | User payload storage: ISO files, authoritative `config/mventx.json`, metadata, and backups. |

### 3.1 Partition Alignment & Structure

- **1 MiB Alignment**: Both partitions are aligned to 1 MiB boundaries (2,048 sectors of 512 bytes):
  - Partition 1 begins at LBA 2048 and spans to LBA 133119 (64 MiB test size).
  - Partition 2 begins at LBA 133120 and spans to `DiskEnd - 34 sectors` (leaving room for the GPT backup array and header).
- **Protective MBR (LBA 0)**: Contains a single type `0xEE` partition spanning the entire disk image to prevent legacy MBR partitioning tools from overwriting GPT structures.
- **Primary GPT (LBA 1–33)** and **Backup GPT (LBA N-33 to N)**: Contain valid GPT headers, partition entries, and independent CRC32 checksums.

### 3.2 Partition Directory Layouts

#### Partition 1: `MVX_BOOT` (FAT32)
```
MVX_BOOT/
├── EFI/
│   └── BOOT/
│       └── BOOTX64.EFI          # UEFI 64-bit GRUB bootloader
└── boot/
    └── grub/
        ├── grub.cfg             # Primary static GRUB config (theme, search, include)
        ├── mventx_menu.cfg      # Generated GRUB menu (compiled from mventx.json)
        ├── x86_64-efi/          # Dynamic GRUB runtime modules (loopback, linux, iso9660, etc.)
        └── fonts/
            └── unicode.pf2      # GRUB visual terminal font
```

#### Partition 2: `MVX_DATA` (exFAT)
```
MVX_DATA/
├── ISO/                         # User-accessible storage for bootable ISO files
├── config/
│   └── mventx.json              # Authoritative environment registration & settings
├── metadata/                    # Extracted ISO labels, hashes, and custom icons
└── backups/                     # Timestamped JSON backups prior to mutations
```

### 3.3 Authoritative Configuration Flow

```
+------------------------------------+
|  MVX_DATA/config/mventx.json       |  <-- Authoritative source of truth
+------------------------------------+
                  │
                  ▼
+------------------------------------+
|  Host-Side StorageManager          |  <-- Validates schema & atomic backups
+------------------------------------+
                  │
                  ▼
+------------------------------------+
|  core.boot_config.generator        |  <-- Escapes inputs, compiles GRUB syntax
+------------------------------------+
                  │
                  ▼
+------------------------------------+
|  MVX_BOOT/boot/grub/mventx_menu.cfg|  <-- Synchronized target boot script
+------------------------------------+
                  │
                  ▼
+------------------------------------+
|  Target Boot: GRUB 2 (UEFI)        |  <-- Reads config without running Python
+------------------------------------+
```

### 3.4 Privilege Boundaries & Safety Architecture

- **User-Space / Rootless Operations** (No Elevation Required):
  - Routine management via `StorageManager` (reading/updating `mventx.json`, compiling `mventx_menu.cfg`, managing ISO files).
  - Virtual disk creation using the pure-Python builder (`scripts/windows/make_dual_partition_disk.py` / `core/storage/disk_validator.py`).
  - QEMU virtualized UEFI boot testing (`scripts/windows/run_qemu.ps1`).
- **Elevated Operations** (Administrator / root Required):
  - Initial provisioning or repartitioning of physical USB flash drives (`installer`).
  - Native Windows VHD loopback attachment via `diskpart.exe attach vdisk` (strictly isolated and optional via `-UseDiskpart`).
- **Safe Drive Detection**:
  - `core/storage/device_detection.py` queries volume labels, drive types (`DRIVE_REMOVABLE`), and explicitly forbids target selection of system volumes (`C:\`).

### 3.5 Storage Validation & Interoperability Verification (Milestone 5)

MVentX implements a four-tier verification framework to ensure storage reliability before operating system kernel booting:
1. **Tier 1 (Structurally Validated)**: Bit-level structural inspection of Protective MBR, Primary and Backup GPT headers (CRC32, LBA bounds, GUID equality), FAT32 VBR/FSInfo/backup VBR (cluster count $\ge 65,525$), exFAT VBR, Sector 11 rolling checksum, backup boot region, and direct cluster traversal for reading files out of raw disks without OS loopback mounts (`core/storage/disk_validator.py`).
2. **Tier 2 (Windows Filesystem Operations)**: Fixed VHD format generation (`scripts/windows/convert_to_vhd.py`), dynamic VHD/VHDX conversion, and automated 10-step Windows loopback mount lifecycle (`scripts/windows/test_windows_mount.ps1`) with transparent UAC elevation boundary detection.
3. **Tier 3 (Tested Through QEMU)**: UEFI firmware (OVMF) execution with split read-only code pflash and writable variable store (`scripts/windows/run_qemu.ps1`), GRUB 2.12 execution, dynamic configuration discovery, and fallback rendering.
4. **Tier 4 (Not Yet Verified)**: Full OS kernel booting (Linux loopback recipes and Windows chainloading) scheduled for subsequent milestones.

### 3.6 End-to-End GUI-to-GRUB Operational Pipeline (Milestone 6)

The operational pipeline connects user actions in the PySide6 desktop interface to the GRUB 2 bootloader through a deterministic configuration compiler:
- **Authoritative Mutation**: User actions (register, rename, reorder, toggle, remove, global settings, restore) mutate `MVX_DATA/config/mventx.json` atomically via `tempfile.NamedTemporaryFile` + `os.fsync()` + `replace()`.
- **Dynamic Compilation**: The host engine invokes `GrubMenuGenerator` to produce `MVX_BOOT/boot/grub/mventx_menu.cfg`.
- **Safety Rollback**: If menu compilation fails, changes to `mventx.json` roll back automatically (`MenuSyncRollbackError`).
- **Firmware Loading**: At machine boot, UEFI firmware loads `EFI/BOOT/BOOTX64.EFI`, executing `boot/grub/grub.cfg`, which discovers `mventx_data_dev` and sources `mventx_menu.cfg` to render the user's custom bootloader menu.
- **Automated QEMU Verification**: `scripts/test_qemu_harness.py` boots virtual disks across test scenarios, issues `screendump` via QEMU monitor, and performs pixel-level verification of `gfxterm` cyan highlight and light gray border elements at 1280x800 resolution.

### 3.7 Linux ISO Loopback & Inspection Engine (Milestones 7 & 8)

MVentX implements direct loopback booting for Linux distributions from the exFAT `MVX_DATA` partition without extracting or modifying ISO images:
1. **Deep ISO Inspection (`core/iso_manager/iso_inspector.py`)**:
   - Directly parses ISO 9660 Primary Volume Descriptors (PVD) and directory records across top-tier and second-tier directories (`/casper/`, `/live/`, `/arch/`, `/images/pxeboot/`, `/boot/`).
   - Automatically discovers kernel (`vmlinuz`) and initramfs binaries, infers the boot recipe with confidence scoring (`high`, `medium`, `uncertain`), and attaches diagnostic warnings for ambiguous structures.
2. **Distribution Recipe Engine (`core/boot_config/recipes.py`)**:
   - Formalized recipes for Casper (Ubuntu, Mint, Pop!_OS), ArchISO (Arch Linux, Manjaro), Dracut (Fedora, RHEL), Live-Boot (Debian Live, Clonezilla), and Generic Kernel.
   - Generates resilient loopback commands with defensive checks: verifies storage partition label (`MVX_DATA`), verifies ISO existence, cleans up stale loop devices (`loopback -d loop0`), verifies kernel & initrd paths within loopback before execution, and quotes paths containing spaces.
3. **Six-Tier Compatibility Standard**:
   - Level 0 (Recipe Implemented), Level 1 (ISO Filesystem Inspected), Level 2 (Kernel Loading Tested), Level 3 (Initramfs Loading Tested), Level 4 (Live OS Interactive User Space), and Level 5 (Modern Graphical Live Desktop).

### 3.8 USB Installation and Preparation Subsystem (Milestone 9)

MVentX provides an end-to-end media preparation subsystem (`installer/`) capable of provisioning virtual disk images and physical USB flash storage with the dual-partition architecture:
1. **Device Discovery & Safety Shields (`core/storage/device_manager.py`)**:
   - Cross-platform block device discovery (PowerShell on Windows, `lsblk` on Linux).
   - Strict safety gates reject target devices containing the host operating system (`C:\`, `/`, `/boot`), read-only storage, or `PhysicalDrive0` hosting system volumes.
   - Physical device formatting is locked down by default (`allow_physical_formatting = False`).
2. **Standard GPT Layout & Alignment (`installer/partition_planner.py`)**:
   - Enforces 1 MiB alignment (`ALIGNMENT_SECTORS_1MB = 2048`).
   - Partition 1 (`MVX_BOOT`): FAT32 ESP (GUID `C12A7328-F81F-11D2-BA4B-00A0C93EC93B`), default 128 MiB.
   - Partition 2 (`MVX_DATA`): exFAT Basic Data (GUID `EBD0A0A2-B9E5-4433-87C0-68B6B72699C7`), consumes remaining space.
3. **Bootloader & Filesystem Deployment (`installer/`)**:
   - `BootloaderInstaller`: Deploys `EFI/BOOT/BOOTX64.EFI`, `boot/grub/grub.cfg`, 280 dynamic runtime modules, unicode font, and fallback `mventx_menu.cfg`.
   - `FilesystemManager`: Initializes directory skeleton (`ISO/`, `config/`, `metadata/`, `backups/`) and authoritative `mventx.json` with 0 initial environments.
4. **Automated Verification & Safe Recovery**:
   - `InstallationVerifier` performs 10-point structural inspection (Protective MBR, GPT CRC32, 1 MiB alignment, FAT32 VBR/FSInfo, exFAT VBR/checksum, bootloader binaries, module tree, and directory skeleton) returning `InstallationVerificationReport`.
   - `InstallationRecoveryService` guarantees atomic rollback and orphan cleanup on any failure or cancellation.
5. **Desktop UI Integration**:
   - `manager/ui/usb_installer.py` integrates with the PySide6 desktop suite, featuring layout previews, background `QThread` execution, progress reporting, and post-installation verification reports.

---

## 4. Key Design Invariants

1. **No Auto-Discovery Scanning**: Unlike Ventoy, which scans every directory at boot time for `.iso` extensions, MVentX requires intentional registration. This prevents unwanted, corrupted, or auxiliary images from appearing in the menu.
2. **Authoritative Configuration on Data Partition**: The primary JSON configuration strictly resides on `MVX_DATA/config/mventx.json`. The host synchronizes the compiled boot script onto `MVX_BOOT/boot/grub/mventx_menu.cfg`.
3. **Deterministic Boot Menus**: Boot order is explicitly preserved via configuration index order.
4. **Backup Before Write**: Any rewrite of `mventx.json` or `mventx_menu.cfg` creates an atomic timestamped backup in `backups/` to prevent configuration corruption.
5. **Universal Portability**: Both FAT32 and exFAT partitions mount natively on Windows 11, macOS, and Linux without external filesystem drivers.

