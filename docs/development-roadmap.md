# MVentX Development Roadmap

This document outlines the phased development plan for MVentX from initial project scaffolding to production release.

---

## Phase 1: Repository Foundation & Architecture (Completed)
- [x] Establish repository directory structure and naming conventions.
- [x] Define JSON configuration schema (`schema.json`) and defaults (`defaults.json`).
- [x] Author core architecture, boot process, and technical decisions documentation.
- [x] Configure Python build system (`pyproject.toml`) and development dependencies.
- [x] Define module boundaries, stub interfaces, and docstrings.

---

## Milestone 1: UEFI + GRUB Boot Prototype (Completed)
- [x] **GRUB Configuration Generator** (`core/boot_config/generator.py`):
  - Read `mventx.json` configuration store.
  - Render native GRUB `menuentry` blocks using custom user-defined display names.
  - Filter entries strictly based on `enabled: true`.
  - Validate all fields and guard against GRUB configuration injection (control characters, semicolons, command substitutions).
  - Deterministic entry sorting (primary: `order`, secondary: `id`).
  - Atomic write mechanism to prevent configuration corruption.
  - Fallback entry generation for empty configurations.
- [x] **Primary GRUB Configuration** (`boot/grub/grub.cfg`):
  - Dynamic discovery and loading of `mventx_menu.cfg`.
  - Fallback recovery entry when configuration is absent.
  - Terminal output and palette branding.
  - Modular system utilities submenu (Reboot, Halt, UEFI Firmware Setup).
- [x] **Virtual Disk & UEFI Boot Tooling**:
  - `scripts/create_test_disk.sh`: Automated GPT/FAT32 raw disk image builder with GRUB EFI compilation.
  - `scripts/run_qemu.sh`: OVMF UEFI boot runner for Ubuntu 24.04 LTS.
  - `scripts/generate_test_menu.py`: CLI testing and menu inspection utility.
- [x] **Automated Test Suites**:
  - 16 unit and integration tests covering schema compliance, field validation, injection prevention, atomic file writing, and determinism.

---

## Milestone 2: Dual-Partition USB Storage Architecture (Completed)
- [x] **Dual-Partition Layout**:
  - Partition 1: `MVX_BOOT` (FAT32 ESP, GUID `C12A7328-F81F-11D2-BA4B-00A0C93EC93B`, 64 MB).
  - Partition 2: `MVX_DATA` (exFAT Microsoft Basic Data, GUID `EBD0A0A2-B9E5-4433-87C0-68B6B72699C7`, ~192 MB).
- [x] **Pure-Python Rootless Disk Builder** (`scripts/windows/make_dual_partition_disk.py`):
  - Builds sector-level GPT, FAT32 ESP, and compliant exFAT with VBR, allocation bitmap, upcase table, and directory sets.
  - Runs in user-space on Windows and Linux without administrator rights or kernel loopback mounts.
- [x] **Storage Subsystem Implementation**:
  - `StorageManager` in `core/storage/storage_manager.py` manages directory trees (`ISO/`, `config/`, `metadata/`, `backups/`), atomic writes, and boot menu synchronization.
  - `device_detection.py` provides Win32 and cross-platform partition identification with safety boundaries against system drives (`C:\`).
  - `disk_validator.py` performs deep structural inspection of GPT headers, partition entries, and filesystem VBRs.
- [x] **Testing & Verification**:
  - 30 tests covering GPT partitioning, alignment, partition GUIDs, FAT32/exFAT filesystems, and QEMU UEFI boot.

---

## Milestone 3: ISO Environment Registration Engine (Completed)
- [x] **Manual ISO Registration** (`core/iso_manager/registration.py`):
  - Register ISOs manually with custom display names and unique IDs.
  - Strict directory traversal protection: forces all ISO paths to reside within `MVX_DATA/ISO/`.
  - Duplicate registration prevention matching canonical paths and filesystem identities.
  - Architectural Rule: No automatic directory scanning registration.
- [x] **Environment Lifecycle Management**:
  - Full support for list, rename, enable, disable, and reorder operations.
  - Non-destructive deregistration: ISO files are preserved on disk by default.
  - Optional file deletion requires explicit confirmation (`confirm_deletion=True`).
- [x] **ISO Validation & Metadata Extraction** (`core/iso_manager/validation.py`, `metadata.py`):
  - Verifies file existence, `.iso` extension, non-zero size, and readability.
  - Checks ISO9660 (`CD001`) and UDF (`BEA01`, `NSR02`, `NSR03`, `TEA01`) volume descriptor signatures at sector 16.
  - Detects El Torito boot catalogs and CPU architecture hints (`x86_64`, `arm64`, `i386`).
  - Checksum computation and verification (`sha256`, `sha512`, `md5`).
  - Explicit disclaimer: Valid ISO signature does not guarantee bootability.
- [x] **Configuration Integration & Rollback Safety**:
  - Authoritative configuration lives on `MVX_DATA/config/mventx.json`.
  - Atomic writes with timestamped backup creation in `MVX_DATA/backups/`.
  - Cross-partition synchronization with GRUB boot menu on `MVX_BOOT`.
  - Safe rollback restoring prior configuration and purging metadata if menu sync fails.
- [x] **Storage Management**:
  - Reports partition total, used, and available capacity.
  - Distinct metrics for registered ISO count and total registered ISO bytes.
  - Missing registered ISO detection and unregistered ISO file discovery.
- [x] **CLI Management Tool** (`scripts/manage_iso.py`):
  - Full CLI interface for validating, registering, renaming, toggling, reordering, and inspecting ISOs.
- [x] **Automated Test Suite**:
  - 57 tests passing across unit and integration suites.

---

## Milestone 4: Desktop Management Interface (PySide6) (Completed)
- [x] **Service-Oriented UI Architecture**:
  - `DeviceService` manages physical USB detection (`MVX_DATA` / `MVX_BOOT`) and virtual test disk staging mounts.
  - `ISOService` bridges UI views to backend `ISORegistrationManager`, `StorageManager`, and `ISOValidator` without duplicating business logic.
- [x] **Modern Desktop Interface**:
  - Graphite dark theme with subtle blue accent, system management utility styling (`manager/ui/styles.py`).
  - Responsive sidebar navigation across five primary screens.
- [x] **Dashboard Screen** (`manager/ui/dashboard.py`):
  - Device connection status, dual-partition health (`MVX_BOOT` FAT32 + `MVX_DATA` exFAT).
  - Storage utilization bar, active vs registered environments, missing ISO file indicators.
- [x] **Environment Library Screen** (`manager/ui/environment_library.py`):
  - Table of registered environments with search filter.
  - Action buttons: Move Up, Move Down, Rename, Enable/Disable toggle, Remove registration.
  - Non-destructive removal by default; optional physical file deletion requiring explicit confirmation.
- [x] **Add Environment Workflow** (`manager/ui/add_environment.py`):
  - Dropdown of candidate ISOs in `MVX_DATA/ISO/` (no auto-registration).
  - Instant ISO filesystem validation (`ISOValidator`) displaying format, size, volume ID, El Torito status, and disclaimer.
  - Form for custom display name, boot method, description, kernel args, enabled toggle, and SHA-256 computation.
  - Automatic GRUB boot menu synchronization on commit.
- [x] **Storage Manager Screen** (`manager/ui/storage_manager.py`):
  - Detailed partition inspection (mount points, filesystem types, partition GUIDs, directory trees).
  - Clear distinction between registered ISO sizes and total filesystem usage.
- [x] **Settings Screen** (`manager/ui/settings.py`):
  - Boot menu title, timeout countdown, and default environment selection.
  - Configuration backup manager with table of timestamped backups in `backups/` and restore capability.
  - Application information and license display.
- [x] **Automated GUI & Service Tests**:
  - Headless offscreen testing (`tests/unit/test_gui_application.py`) verifying window launch, navigation, registration, state updates, non-destructive removal, and disconnected device handling.
  - High-resolution screenshot capture tool (`scripts/capture_gui_screenshots.py`).
  - Total automated test suite expanded to 67 passing tests.

---

## Milestone 5: End-to-End Storage Validation (Completed)
- [x] **Verification Tiering Framework**:
  - Clear taxonomy establishing 4 verification levels: Structurally Validated, Windows Filesystem Operations, Tested Through QEMU, and Not Yet Verified.
- [x] **Deep GPT & Filesystem Structural Validation** (`core/storage/disk_validator.py`):
  - Primary & Backup GPT header CRC32, LBA bounds, and Disk GUID matching.
  - Backup partition array comparison matching primary partition array at `total_sectors - 33`.
  - Non-overlapping boundary and 1 MiB alignment enforcement.
  - FAT32 cluster count threshold ($\ge 65,525$ clusters), FSInfo signatures, backup VBR matching.
  - exFAT OEM signature, Sector 11 rolling checksum across sectors 0..10, backup boot region replication.
  - Direct raw cluster traversal reading files (`config/mventx.json`, `boot/grub/grub.cfg`, `EFI/BOOT/BOOTX64.EFI`) without OS kernel loopback drivers.
- [x] **Windows Interoperability & Elevation Architecture**:
  - Pure-Python fixed VHD generation (`scripts/windows/convert_to_vhd.py`) and dynamic VHD/VHDX conversion via `qemu-img`.
  - 10-step Windows loopback mount test script (`scripts/windows/test_windows_mount.ps1`) detecting Administrator UAC elevation boundaries transparently.
- [x] **Configuration Persistence, Backup, & Rollback**:
  - Cold application restart persistence across manager instances.
  - Atomic write guarantees (`NamedTemporaryFile` + `fsync` + atomic `replace`).
  - Automated timestamped backup generation, rotation (`max_retained_backups`), and non-destructive restoration preserving all ISO files.
  - Menu sync rollback protection (`MenuSyncRollbackError`) reverting configuration if boot script compilation encounters I/O errors.
- [x] **ISO Storage Integrity & Security**:
  - Strict directory confinement preventing path traversal outside `MVX_DATA/ISO/`.
  - Missing ISO detection for out-of-band deleted files (`detect_missing_isos`).
  - Modified/tampered ISO detection via file size and SHA-256 cryptographic hashes.
  - Rejection of invalid extensions, 0-byte files, truncated files, and random noise.
- [x] **Comprehensive Automated Testing**:
  - Total automated test suite expanded from 67 to **117 passing tests** (50 new tests across unit and integration suites).
  - Dedicated validation report authored in `docs/milestone-5-storage-validation.md`.

---

## Milestone 6: End-to-End GUI-to-GRUB Integration (Completed)
- [x] **Complete GUI-to-Configuration-to-GRUB Pipeline**:
  - Validated all 10 integration scenarios (Scenarios A through J): empty fallback, single environment, multi-environment ordering, disabled environment exclusion, re-enabling, reordering, renaming, removal without deleting ISOs, invalid mutation safety, and special character escaping.
  - Global settings synchronization: boot menu title and countdown timeout update in `mventx.json` and `mventx_menu.cfg`.
  - Backup restoration: restoring previous backups resynchronizes GRUB menu without touching physical ISO files.
- [x] **Strengthened GRUB Configuration Generator** (`core/boot_config/generator.py`):
  - Two-tier validation and escaping (`validate_display_name` + `sanitize_display_name`) preventing double-escaping in JSON while defending against GRUB command injection.
  - Deterministic menu sorting (primary: `order`, secondary: `id`).
  - Storage diagnostics reporting missing ISO files at generation time.
  - Safe handling of unrecognized or experimental boot methods.
  - Explicit disclaimer clarifying registration does not imply verified bootability.
- [x] **Independent GRUB Entry Point** (`boot/grub/grub.cfg`):
  - Discovers data partition (`mventx_data_dev`) and sources dynamic menu script.
  - Provides diagnostic unconfigured fallback when configuration is missing.
  - Fully self-contained without Python or host service dependencies at boot.
- [x] **Automated QEMU Boot Verification Harness** (`scripts/test_qemu_harness.py`, `tests/integration/test_qemu_boot_harness.py`):
  - Builds isolated GPT dual-partition virtual disks for test scenarios.
  - Boots in QEMU with OVMF split firmware (`edk2-x86_64-code.fd` and writable NVRAM variable copy).
  - Captures high-resolution screendumps and analyzes pixel distribution (cyan highlight box `RGB(84, 254, 254)`, border/text `RGB(168, 168, 168)`).
  - Exports screenshots for Scenarios A, B, C, D, and J to `docs/screenshots/`.
- [x] **Comprehensive Automated Testing**:
  - Total automated test suite expanded from 117 to **141 passing tests** (+24 new tests).
  - Dedicated Milestone 6 report authored in `docs/milestone-6-end-to-end-integration.md`.

---

## Milestone 7: Linux ISO Loopback Boot Implementation (Completed)
- [x] **Distribution Recipe Engine** (`core/boot_config/recipes.py`):
  - Supported recipes: Casper (Ubuntu / Linux Mint), ArchISO (Arch Linux / Manjaro), Dracut (Fedora / RHEL), Live-Boot (Debian Live / Clonezilla), and Generic Kernel.
  - Automatic recipe inference from ISO volume descriptors and filenames (`detect_recipe_from_hints`).
  - Kernel and initramfs path overrides and custom argument injection with security validation.
- [x] **Resilient GRUB 2 Loopback Generation** (`core/boot_config/generator.py`):
  - Runtime loopback mounting via `loopback loop0 ($mventx_data_dev)/${iso_path}`.
  - Multi-tier validation: checks data partition availability, verifies file existence, checks loopback mount status, executes kernel and initrd, and starts execution (`boot`).
  - Defensive error handling: informative diagnostics and pause when files or storage partitions are missing.
- [x] **Runtime Boot Assets & Low-Level Storage Fixes**:
  - `boot/grub/grub.cfg`: Loaded `loopback`, `iso9660`, and `linux` modules at startup.
  - Deployed complete suite of 280 GRUB 2.12 x86_64 EFI modules to `boot/grub/x86_64-efi/`.
  - Resolved exFAT multi-entry long filename bug (> 15 chars) in `ExFATBuilder.make_entry_set()` with `NoFatChain` flags.
  - Resolved FAT32 multi-cluster directory allocation chaining in `FAT32Builder.build_from_directory()`.
- [x] **Four-Level Verification Standard**:
  - Level 1 (Menu Generation): Verified via unit tests for all recipe variants and parameter substitutions.
  - Level 2 (Kernel Loading): Verified in QEMU via early serial console printk messages.
  - Level 3 (Initramfs Execution): Verified via initramfs daemon startup (`syslogd, klogd`).
  - Level 4 (Live OS Environment): Verified via interactive user space reach (installer UI).
- [x] **Comprehensive Automated Testing**:
  - Total automated test suite expanded from 141 to **153 passing tests** (+12 new tests).
  - Dedicated Milestone 7 report authored in `docs/milestone-7-linux-boot.md`.

---

## Milestone 8: Linux Distribution Compatibility and Boot Reliability (Completed)
- [x] **Six-Tier Compatibility Classification System**:
  - Defined explicit taxonomy: Level 0 (Recipe Implemented), Level 1 (ISO Filesystem Inspected), Level 2 (Kernel Loading Tested), Level 3 (Initramfs Loading Tested), Level 4 (Live OS Interactive User Space), and Level 5 (Modern Graphical Live Desktop).
  - Documented complete technical metrics and download throughput constraints (~56 min for 5.82 GB at 1.7 MB/s) for Ubuntu 24.04 LTS Desktop (`ubuntu-24.04.5.1-desktop-amd64.iso`, SHA-256 `2627e7f6d14902a7ea4ef48270ca882dfc394c8b8ce8fb0299f1fa02f155998a`).
- [x] **Deep ISO Filesystem Inspection Engine** (`core/iso_manager/iso_inspector.py`):
  - Direct ISO 9660 PVD and directory record traversal parsing LBAs without mounting or extraction.
  - Automatic discovery of kernel binaries and initramfs archives across standard distribution hierarchies (`/casper/`, `/live/`, `/arch/`, `/images/pxeboot/`, `/boot/`).
  - Confidence scoring (`high`, `medium`, `uncertain`) and diagnostic warning generation for ambiguous structures.
  - Seamless integration into `ISOMetadataExtractor` and `ISORegistrationManager`.
- [x] **Multi-Distribution Boot Reliability & GRUB Diagnostics**:
  - Verified Debian live-boot recipe on real Clonezilla Live Debian ISO reaching Level 2 (kernel decompressed, early execution verified in serial log).
  - Verified Ubuntu Focal Mini netboot reaching Level 4 (interactive user space language selection).
  - Verified Alpine Linux structural inspection and valid loopback generation.
  - Resilient GRUB runtime failure guards: storage partition check (`MVX_DATA`), ISO file existence check, kernel & initrd existence checks inside loopback (`[ ! -f "(loop0)${path}" ]`), clean loop device disposal (`loopback -d loop0`), and formatted error screens with countdown return.
  - Robust handling and quoting of paths containing spaces in GRUB scripts and kernel cmdline (`findiso="${iso_path}"`, `iso-scan/filename="${iso_path}"`).
- [x] **Comprehensive Automated Testing**:
  - Total automated test suite expanded from 153 to **164 passing tests** (+11 new tests across unit and integration suites).
  - Dedicated Milestone 8 report authored in `docs/milestone-8-linux-compatibility.md`.

---

## Milestone 9: USB Installation and Preparation (Completed)
- [x] **USB Device Detection & Inspection** (`core/storage/device_info.py`, `core/storage/device_manager.py`):
  - Cross-platform block device discovery supporting Windows (PowerShell `Get-Disk` / `Get-Partition`) and Linux (`lsblk` / `/sys/class/block`).
  - Structured `DeviceInfo` and `PartitionInfo` dataclasses capturing bus type, model, size, partitions, removable, read-only, and virtual flags.
  - Virtual disk inspection parsing existing GPT and filesystem headers.
- [x] **Safety Gates & Drive Protection**:
  - `assert_safe_target()` actively protects host operating system disks: blocks `C:\`, system drives, `/`, `/boot`, `/boot/efi`, and read-only media.
  - Physical device formatting locked by default policy (`allow_physical_formatting = False`).
  - Desktop GUI never auto-selects physical drives and mandates an explicit confirmation checkbox and modal confirmation dialog.
- [x] **GPT Partition Planning** (`installer/partition_planner.py`):
  - Standard GPT math enforcing 1 MiB alignment (`ALIGNMENT_SECTORS_1MB = 2048`).
  - Partition 1 (`MVX_BOOT`): FAT32 ESP (GUID `C12A7328-F81F-11D2-BA4B-00A0C93EC93B`), configurable size (32–256 MiB, default 128 MiB).
  - Partition 2 (`MVX_DATA`): exFAT Basic Data (GUID `EBD0A0A2-B9E5-4433-87C0-68B6B72699C7`), consumes all remaining aligned sectors.
- [x] **Modular Installation Subsystem** (`installer/`):
  - `DevicePreparationService`: Target validation, host capacity verification, and staging.
  - `PartitionManager`: Low-level GPT, FAT32, and exFAT disk generation.
  - `BootloaderInstaller`: Deploys `BOOTX64.EFI`, `grub.cfg`, 280 runtime x86_64 EFI modules, unicode font, and fallback `mventx_menu.cfg`.
  - `FilesystemManager`: Initializes directory skeleton (`ISO/`, `config/`, `metadata/`, `backups/`) and authoritative `mventx.json` with 0 initial environments.
  - `InstallationRecoveryService`: Safe failure rollback and temporary file cleanup.
  - `InstallationVerifier`: 10-point structural and filesystem verification producing structured `InstallationVerificationReport`.
  - `InstallationService`: 12-step installation sequence with percentage progress and cancellation support.
- [x] **Desktop GUI Integration** (`manager/ui/usb_installer.py`, `manager/ui/main_window.py`):
  - Dedicated USB Installer screen in PySide6 desktop application with virtual/physical mode toggle.
  - Visual two-color layout preview bar with dynamic partition size calculations.
  - Asynchronous background execution via `InstallationWorker` (`QThread`) with responsive UI and cancellation.
  - Post-installation verification results card.
- [x] **End-to-End QEMU UEFI Boot Verification**:
  - Validated clean boot of a freshly installed virtual disk into GRUB 2.12 fallback menu via OVMF UEFI firmware.
- [x] **Comprehensive Automated Testing**:
  - Test suite expanded from 164 to **176 passing tests** (135 unit tests + 41 integration tests, 0 failures, 0 regressions).
  - Dedicated Milestone 9 report authored in `docs/milestone-9-usb-installation.md`.

---

## Milestone 10: Windows ISOs & Advanced Boot Methods (Next Phase)
- [ ] Windows ISO boot integration (wimboot / EFI chainloading).
- [ ] Internal disk chainloading for host Windows Boot Manager.
- [ ] Custom GRUB visual theme packs.
- [ ] Physical USB drive flashing and hardware compatibility validation matrix.
