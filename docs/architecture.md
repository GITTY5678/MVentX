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
                                                 | Read / Write JSON & ISOs
                                                 v
+-------------------------------------------------------------------+
|                         MVentX USB Drive                          |
|                                                                   |
|  +--------------------------------+  +-------------------------+  |
|  | Partition 1: MVX_BOOT (FAT32)  |  | Partition 2: MVX_DATA   |  |
|  | - /EFI/BOOT/BOOTX64.EFI        |  |   (exFAT / NTFS)        |  |
|  | - /boot/grub/grub.cfg          |  | - /mventx/mventx.json   |  |
|  | - /boot/grub/mventx_menu.cfg   |  | - /isos/*.iso           |  |
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
- **`core/boot_config`**: Converts JSON configuration into native GRUB 2 menu scripts (`mventx_menu.cfg`).

### 2.3 Management Subsystem (`manager/`)
- **PySide6 Application**: Modern desktop GUI allowing users to:
  - Inspect mounted MVentX drives.
  - Manually register ISO files located on or copied to the drive.
  - Set custom display names, descriptions, and icon tags.
  - Toggle environment enabled/disabled states.
  - Reorder boot entries with instant preview.
  - Safely commit changes to `mventx.json` and regenerate `mventx_menu.cfg`.

### 2.4 Installer Subsystem (`installer/`)
- Responsible only for the initial provisioning of blank USB drives:
  - Partitioning (FAT32 boot partition + exFAT data partition).
  - Deploying GRUB EFI binaries and default directory trees.
  - Writing the initial baseline configuration.
  - **Invariant**: Destructive formatting is strictly quarantined to this module and requires explicit user consent.

---

## 3. Storage Layout & Partitioning Scheme

MVentX employs a standard two-partition layout on GPT (GUID Partition Table) or MBR:

| Partition | Label | Filesystem | Recommended Size | Purpose |
|---|---|---|---|---|
| Partition 1 | `MVX_BOOT` | FAT32 | 128 MB – 512 MB | UEFI system partition (`ESP`), GRUB binaries, themes, and dynamic menu configs. |
| Partition 2 | `MVX_DATA` | exFAT | Remaining drive | User data, registered ISO files, and `mventx.json` configuration store. |

### Rationale for Dual Partitions:
- FAT32 is universally required by UEFI firmware for boot discovery.
- FAT32 has a 4 GB file size limit, which prevents storing modern operating system ISOs (often 4 GB – 10 GB+).
- exFAT allows large files (> 4 GB) while maintaining broad cross-platform read/write support in Windows, macOS, and modern Linux kernels, supported by GRUB via the `exfat` module.

---

## 4. Key Design Invariants

1. **No Auto-Discovery Scanning**: Unlike Ventoy, which scans every directory at boot time for `.iso` extensions, MVentX requires intentional registration. This prevents unwanted, corrupted, or auxiliary images from appearing in the menu.
2. **Deterministic Boot Menus**: Boot order is explicitly preserved via configuration index order.
3. **Backup Before Write**: Any rewrite of `mventx.json` or `mventx_menu.cfg` creates an atomic timestamped backup to prevent configuration corruption.

