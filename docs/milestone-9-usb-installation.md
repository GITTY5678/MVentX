# MVentX – Milestone 9: USB Installation and Preparation

## 1. Executive Summary

Milestone 9 establishes the complete **USB Preparation and Installation Subsystem** for MVentX. The subsystem automates the end-to-end provisioning of bootable MVentX media: taking a target storage device (virtual disk image or physical storage device), planning a standard-compliant GPT partition layout with 1 MiB alignment, laying down FAT32 (`MVX_BOOT`) and exFAT (`MVX_DATA`) filesystems, deploying the modular GRUB 2.12 UEFI bootloader (`BOOTX64.EFI`, static `grub.cfg`, 280 runtime x86_64 EFI modules, visual font, and initial fallback menu), and creating the MVentX directory skeleton with an empty authoritative configuration (`mventx.json`).

Per project safety commitments, **physical disk formatting is strictly locked down and disabled by default** (`allow_physical_formatting = False`). The complete installation workflow is implemented and validated end-to-end using rootless virtual disk images and verified via UEFI firmware boot in QEMU.

The regression baseline expanded from **164 passing tests** (Milestone 8) to **176 passing tests** (135 unit tests + 41 integration tests), maintaining a 100% pass rate with zero regressions.

---

## 2. Subsystem Architecture

The USB installation subsystem is structured into modular services following single-responsibility principles:

```
installer/
├── __init__.py                 # Clean subsystem exports
├── partition_planner.py        # GPT layout math, sector alignment, partition sizing
├── device_preparation.py       # Host checks, target safety validation, staging
├── partition_manager.py        # Low-level partition table and filesystem creation
├── filesystem_manager.py       # Directory skeleton creation and initial files
├── bootloader_installer.py     # GRUB 2 EFI deployment and runtime assets
├── installation_recovery.py    # Failure cleanup, rollback, and orphan deletion
├── installation_verifier.py    # Post-installation deep verification
└── installation_service.py     # 12-step coordinator with progress & cancellation

core/storage/
├── device_info.py              # DeviceInfo, PartitionInfo, DeviceType dataclasses
└── device_manager.py           # Cross-platform device discovery & safety gates
```

### 2.1 Cross-Platform Device Discovery (`core/storage/`)

- **`DeviceInfo` & `PartitionInfo`** (`core/storage/device_info.py`):
  Structured, immutable representations of physical and virtual storage devices, capturing device path, model, vendor, bus type, capacity, sector size, removable status, read-only status, virtual flag, and partition list.
- **`DeviceManager`** (`core/storage/device_manager.py`):
  - **Windows Host Discovery**: Uses PowerShell `Get-Disk` and `Get-Partition` (with fallback to WMI `Win32_DiskDrive`) to discover attached block devices without invoking destructive utilities.
  - **Linux Host Discovery**: Uses `lsblk -J -b -o ...` with `/sys/class/block` inspection.
  - **System Drive Guard (`assert_safe_target`)**: Explicitly inspects drive paths and partitions. Rejects target devices containing `C:\`, `\\.\PhysicalDrive0` when it hosts system volumes, `/`, `/boot`, `/boot/efi`, or any read-only media.
  - **Virtual Disk Inspection (`inspect_virtual_disk`)**: Examines raw disk images (`.img`, `.vhd`), validates protective MBR and GPT structures, and extracts existing partition metadata.

### 2.2 Partition Planning (`installer/partition_planner.py`)

- **GPT Sector Math & Alignment**:
  - Enforces standard 1 MiB alignment (`ALIGNMENT_SECTORS_1MB = 2048` sectors of 512 bytes).
  - LBA 0: Protective MBR.
  - LBA 1: Primary GPT Header.
  - LBA 2–33: Primary GPT Partition Entry Array (128 entries of 128 bytes = 16,384 bytes = 32 sectors).
  - First usable LBA: 2048 (aligned at exactly 1 MiB).
  - Last usable LBA: `Total Sectors - 34` (reserving 33 sectors for Backup GPT Entries and 1 sector for Backup GPT Header at `Total Sectors - 1`).
- **Partition Specifications**:
  - **Partition 1 (`MVX_BOOT`)**: EFI System Partition (ESP), GUID `C12A7328-F81F-11D2-BA4B-00A0C93EC93B`, FAT32 filesystem. Minimum 32 MiB, recommended 64–128 MiB (default: 128 MiB to comfortably accommodate GRUB binaries, 280 modules, themes, fonts, and future kernels).
  - **Partition 2 (`MVX_DATA`)**: Microsoft Basic Data Partition, GUID `EBD0A0A2-B9E5-4433-87C0-68B6B72699C7`, exFAT filesystem. Allocates all remaining aligned sectors.
- **Capacity Limits**:
  - Enforces minimum disk size: 128 MiB.
  - Validates that requested ESP size leaves sufficient room for `MVX_DATA` ($\ge 32$ MiB).

### 2.3 Low-Level Provisioning & File Deployment

- **`PartitionManager`** (`installer/partition_manager.py`):
  Coordinates the physical/virtual partition construction. Uses the proven, rootless pure-Python GPT/FAT32/exFAT disk generator (`make_dual_partition_disk.py`) for virtual disks. Blocks physical disk creation unless `allow_physical_formatting = True` is explicitly supplied.
- **`BootloaderInstaller`** (`installer/bootloader_installer.py`):
  Deploys the complete boot tree:
  - `EFI/BOOT/BOOTX64.EFI` (UEFI x86_64 GRUB 2.12 bootloader).
  - `boot/grub/grub.cfg` (Root GRUB script containing partition discovery and theme configuration).
  - `boot/grub/x86_64-efi/` (280 dynamic GRUB runtime modules, including `loopback`, `iso9660`, `exfat`, `part_gpt`, `fat`, `search`, `normal`, `linux`).
  - `boot/grub/fonts/unicode.pf2` (Visual terminal font).
  - `boot/grub/mventx_menu.cfg` (Initial empty fallback menu).
- **`FilesystemManager`** (`installer/filesystem_manager.py`):
  Initializes the `MVX_DATA` partition structure:
  - `ISO/` (Empty storage directory for bootable user images).
  - `config/mventx.json` (Valid MVentX configuration file adhering to `schema.json`, initialized with `environments: []`, custom title, and timeout).
  - `metadata/` (Directory for extracted ISO metadata and artwork).
  - `backups/` (Directory for automatic JSON configuration backups).

### 2.4 Deep Installation Verification (`installer/installation_verifier.py`)

Every installation undergoes automated bit-level verification before completion:
1. **Protective MBR**: Verifies 0xAA55 boot signature and type 0xEE protective entry spanning the disk.
2. **GPT Integrity**: Validates Primary and Backup GPT headers, partition entries, and CRC32 checksums.
3. **Partition Alignment**: Validates that all partitions start on 1 MiB boundaries (LBA % 2048 == 0) and do not overlap.
4. **FAT32 ESP Inspection**: Verifies FAT32 VBR (0xAA55, OEM identifier, jump instruction, cluster size, FSInfo sector).
5. **exFAT Data Inspection**: Verifies exFAT VBR, Sector 11 rolling checksum, and root directory.
6. **Bootloader Binaries**: Confirms `EFI/BOOT/BOOTX64.EFI` ($\ge 30$ KiB) and `boot/grub/grub.cfg` are present.
7. **GRUB Module Library**: Confirms dynamic modules are deployed in `boot/grub/x86_64-efi/`.
8. **Initial Fallback Menu**: Confirms `boot/grub/mventx_menu.cfg` exists with valid fallback syntax.
9. **Authoritative Configuration**: Confirms `config/mventx.json` exists, is valid JSON, and has 0 initial environments.
10. **Directory Skeleton**: Confirms `ISO/`, `metadata/`, and `backups/` directories are present.

Produces an immutable `InstallationVerificationReport` with overall pass/fail status and granular check items.

### 2.5 Safe Recovery & Rollback (`installer/installation_recovery.py`)

- Tracks temporary staging files and target artifacts during installation.
- On any step failure or user cancellation, immediately unmounts resources, deletes partially written images or temporary staging files, and ensures no corrupted media is left behind.

---

## 3. The 12-Step Installation Sequence

The `InstallationService` executes an atomic 12-step installation sequence with continuous percentage progress reporting and user cancellation support:

| Step | Percentage | Action | Description |
|:---:|:---:|:---|:---|
| 1 | 5% | Target Validation | Verify target path, permissions, and ensure device is not a system drive. |
| 2 | 10% | Partition Planning | Calculate GPT sectors, 1 MiB alignment boundaries, and partition sizes. |
| 3 | 15% | Storage Preparation | Verify available host storage and initialize target image file. |
| 4 | 25% | Partition Table Generation | Construct Protective MBR and Primary/Backup GPT tables. |
| 5 | 40% | FAT32 Boot Filesystem | Build FAT32 ESP filesystem structure and allocation tables. |
| 6 | 55% | exFAT Data Filesystem | Build exFAT VBR, allocation bitmap, upcase table, and root directory. |
| 7 | 70% | Bootloader Deployment | Populate `BOOTX64.EFI`, `grub.cfg`, unicode font, and 280 runtime modules. |
| 8 | 80% | Skeleton Initialization | Create `ISO/`, `config/`, `metadata/`, and `backups/` directories. |
| 9 | 85% | Initial Menu Generation | Compile fallback `mventx_menu.cfg` and initial `mventx.json` config. |
| 10 | 90% | Verification Inspection | Execute deep 10-point structural and filesystem verification. |
| 11 | 98% | Device Finalization | Flush filesystem buffers and commit partition table to disk. |
| 12 | 100% | Installation Complete | Return structured success report to caller/UI. |

---

## 4. Safety Architecture & Safeguards

To prevent catastrophic data loss on development and end-user host machines:

1. **Physical Formatting Locked by Default**:
   `PartitionManager` strictly enforces `allow_physical_formatting = False` by default. Any attempt to format a physical device (`/dev/sdX` or `\\.\PhysicalDriveN`) without explicitly setting this developer flag raises an immediate `DeviceSafetyError`.
2. **System Drive Blocking**:
   `DeviceManager.assert_safe_target()` checks device path, drive letter, mount points, and partition tables:
   - Blocks `C:\` and any drive hosting `C:`.
   - Blocks `/`, `/boot`, and `/boot/efi` on Linux.
   - Blocks `PhysicalDrive0` when it contains the system or boot partition.
   - Blocks any read-only disk or partition.
3. **No Automatic Drive Selection**:
   The desktop UI never pre-selects a physical disk. The user must explicitly choose the target from the dropdown.
4. **Mandatory Confirmation Checkbox & Dialog**:
   The desktop UI disables the "Install MVentX" button until the user explicitly checks a red-highlighted safety confirmation box (`"I understand this operation will completely erase all data on the target"`). On clicking install, a modal confirmation dialog requires explicit confirmation.
5. **No Automatic ISO Registration**:
   Initial installation provisions `config/mventx.json` with an empty `environments: []` list. It never scans the host disk or directories to auto-register files.
6. **Non-Blocking Background Worker (`InstallationWorker`)**:
   Installation executes in a dedicated `QThread` with responsive UI cancellation support, preventing GUI freezes.

---

## 5. Desktop GUI Integration

The PySide6 desktop management application incorporates a dedicated **USB Installer Screen** (`manager/ui/usb_installer.py`):

- **Target Mode Switch**: Toggle between **Virtual Disk Image (.img / .vhd)** (safe default) and **Physical USB Drive** (locked).
- **Partition Sizing Controls**:
  - Virtual Disk Size selector: 256 MiB, 512 MiB, 1 GiB, 2 GiB, 4 GiB, 8 GiB.
  - ESP Size selector: 32 MiB, 64 MiB, 128 MiB (Recommended), 256 MiB.
- **Dynamic Visual Layout Preview**:
  - Proportional two-color visual bar representing `MVX_BOOT` (blue, FAT32) and `MVX_DATA` (green, exFAT).
  - Exact calculated size labels reflecting GPT alignment and metadata reservations.
- **Bootloader Settings**:
  - Menu Title input (default: `"MVentX Boot Manager"`).
  - Menu Timeout input (default: 10 seconds).
- **Execution & Progress**:
  - Animated 12-step progress bar and live status label.
  - Cancel button for aborting in-progress installations.
- **Verification Summary**:
  - Detailed post-installation results card listing every verified component with checkmarks.

---

## 6. QEMU UEFI Boot Verification

The complete end-to-end virtual installation workflow was verified using native QEMU UEFI boot testing (`tests/integration/test_usb_installation_e2e.py`):

1. **Target Virtual Disk**: 256 MiB disk image installed via `InstallationService.install_virtual_disk()`.
2. **Firmware Environment**:
   - QEMU 11 x86_64: `C:\Program Files\qemu\qemu-system-x86_64.exe`
   - UEFI Code: `C:\Program Files\qemu\share\edk2-x86_64-code.fd` (Read-only pflash unit 0)
   - UEFI Variable Store: `build/ovmf/test_vars.fd` (Writable pflash unit 1, cloned from `edk2-i386-vars.fd`)
3. **Boot Execution**:
   - OVMF firmware detected `EFI/BOOT/BOOTX64.EFI` from `MVX_BOOT` (FAT32 ESP).
   - GRUB 2.12 executed `boot/grub/grub.cfg`, discovered `mventx_data_dev` on Partition 2 (`MVX_DATA`), and sourced `mventx_menu.cfg`.
   - GRUB visual engine rendered the clean MVentX fallback menu with title `"MVentX E2E Test"` and timeout 15 seconds.
   - Screen pixel analysis via QEMU monitor `screendump` confirmed the fallback menu display with exit code 0.

---

## 7. Automated Test Suite Results

The automated regression test suite expanded from 164 to **176 tests** across unit and integration categories:

| Test Module | Category | Tests | Status |
|:---|:---:|:---:|:---:|
| `test_installation_service.py` | Unit | 7 | Pass |
| `test_gui_installer.py` | Unit | 4 | Pass |
| `test_gui_application.py` | Unit | 10 | Pass |
| `test_dual_partition_storage.py` | Unit | 7 | Pass |
| `test_filesystem_validation.py` | Unit | 18 | Pass |
| `test_menu_generator.py` | Unit | 9 | Pass |
| `test_iso_validation.py` | Unit | 7 | Pass |
| `test_linux_boot_recipes.py` | Unit | 11 | Pass |
| `test_device_detector_safety.py` | Unit | 5 | Pass |
| Other Unit Tests | Unit | 57 | Pass |
| **Total Unit Tests** | | **135** | **Pass (100%)** |
| `test_usb_installation_e2e.py` | Integration (QEMU) | 1 | Pass |
| `test_gui_grub_pipeline.py` | Integration | 12 | Pass |
| `test_qemu_boot_harness.py` | Integration (QEMU) | 2 | Pass |
| `test_linux_loopback_boot.py` | Integration (QEMU) | 1 | Pass |
| `test_multidistro_loopback.py` | Integration (QEMU) | 2 | Pass |
| `test_iso_storage_integrity.py` | Integration | 6 | Pass |
| `test_backup_recovery.py` | Integration | 5 | Pass |
| `test_config_persistence.py` | Integration | 4 | Pass |
| `test_storage_failures.py` | Integration | 5 | Pass |
| `test_menu_integration.py` | Integration | 3 | Pass |
| **Total Integration Tests** | | **41** | **Pass (100%)** |
| **Grand Total** | | **176** | **Pass (100%)** |

All tests pass cleanly without errors or warnings.

---

## 8. Platform Differences & Future Hardware Validation

### Windows Host
- Block device enumeration relies on PowerShell `Get-Disk` and `Get-Partition`.
- Read-only queries do not require administrative elevation.
- Raw virtual disk image manipulation runs in user-space without UAC elevation.
- Physical flashing will require Administrator elevation and Win32 `DeviceIoControl` locks (`FSCTL_LOCK_VOLUME`, `IOCTL_DISK_GET_DRIVE_LAYOUT_EX`).

### Linux Host
- Block device enumeration uses `lsblk -J` and `/sys/class/block`.
- Virtual disk creation runs without root privileges.
- Physical flashing will require `sudo` privileges for writing to `/dev/sdX`.

### Known Limitations
- Physical device writing is intentionally gated behind `allow_physical_formatting = False` until hardware testing is scheduled in dedicated hardware testing milestones.
- Secure Boot shim integration (Microsoft third-party UEFI CA signing) will be addressed in future firmware milestones.

