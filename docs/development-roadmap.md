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

## Milestone 10: Cross-Platform Host Integration (Completed)
- [x] **Multi-Attribute Device Classification** (`core/host/device_classification.py`):
  - Strictly decouples discovery, classification, mounting, and modification.
  - Non-destructive, read-only classification across 5 statuses (`VALID_MVENTX`, `INCOMPLETE_INSTALLATION`, `CORRUPTED_INSTALLATION`, `ORDINARY_STORAGE`, `INACCESSIBLE`).
  - Never automatically mounts, repairs, or formats an incomplete or corrupted installation.
- [x] **Strong Device Identity & Pre-Write Verification** (`core/host/device_identity.py`):
  - Deterministic SHA-256 fingerprinting from GPT Disk GUID, partition UUIDs, hardware serials, vendor, model, and capacity.
  - Pre-write revalidation guard (`revalidate_device_identity`) aborts writes immediately if device disappears or fingerprint shifts.
- [x] **Device Lifecycle State Machine** (`core/host/device_state.py`):
  - Explicit 14-state tracking (`DETECTED` -> `IDENTIFYING` -> `IDENTIFIED` -> `VALIDATING` -> `READY` <-> `BUSY` -> `EJECTING` -> `EJECTED`, with `DISCONNECTED` guard).
- [x] **Atomic Configuration Synchronization with Rollback** (`core/host/filesystem_access.py`):
  - JSON schema pre-validation, automated timestamped backups with rotation, and atomic replace.
  - Safe rollback restores original `mventx.json` if GRUB menu compilation fails.
- [x] **Safe Device Ejection & Disconnection Monitoring** (`core/host/safe_eject.py`, `core/host/device_monitor.py`):
  - Busy-state checks, filesystem buffer flushes (`FlushFileBuffers` / `os.sync`), and OS ejection API invocation.
  - Hotplug arrival and disconnection event dispatching with write blocking upon device removal.
- [x] **Cross-Platform Host Providers** (`core/host/windows/`, `core/host/linux/`):
  - Windows 10/11: PowerShell `Get-Disk`, `Get-Partition`, Win32 `GetVolumeInformationW`, and `Shell.Application` COM eject.
  - Linux: `lsblk` JSON, sysfs, `findmnt`, `udisksctl`, and `eject`.
- [x] **PySide6 Desktop GUI Integration** (`manager/ui/main_window.py`, `manager/services/device_service.py`):
  - Header device selector with status classification, safe eject button with confirmation modal, and disconnection alerts.
- [x] **Comprehensive Automated Testing**:
  - Test suite expanded from 176 to **207 passing tests** (165 unit tests + 42 integration tests, 0 failures, 0 regressions).
  - Dedicated Milestone 10 report authored in `docs/milestone-10-host-integration.md`.

---

## Milestone 11: Windows Boot Compatibility (Completed)
- [x] **Windows ISO Inspection Engine** (`core/iso_manager/windows_iso_inspector.py`):
  - Non-destructive ISO 9660 directory traversal discovering EFI bootloaders, BCD databases, WinPE (`sources/boot.wim`), and install payloads (`install.wim`, `install.esd`, split `.swm`).
  - Architecture detection (`x64`, `arm64`, `x86`, `multi`).
- [x] **Windows Boot Strategy & Six-Tier Verification Taxonomy** (`core/boot_config/windows_boot_strategy.py`):
  - Primary strategy: extracted media on `MVX_DATA` with direct UEFI chainload (pending physical validation).
  - Six-tier verification taxonomy distinguishing Level 1 (Inspection), Level 2 (Staging), Level 3 (Menu Generation), Level 4 (Boot Manager Execution), Level 5 (WinPE Execution), and Level 6 (Setup UI Interactive).
  - BCD path resolution analysis on exFAT target partition.
- [x] **Defensive GRUB Windows Boot Generator** (`core/boot_config/windows_boot_generator.py`):
  - Resilient GRUB 2 script blocks with partition label discovery (`search --label`), path space-escaping, bootloader verification, and descriptive error screens.
  - Full integration with `GrubMenuGenerator` and schema validation.
- [x] **Media Staging & Storage Sizing Engine** (`core/iso_manager/windows_media_manager.py`):
  - Precise disk space sizing: raw payload + 5% cluster overhead + configurable 500 MiB safety cushion.
  - Destination free space pre-checks and cancellation handling with atomic temporary staging cleanup.
  - Post-extraction payload integrity verification.
  - Strict data-protection rule: environment deregistration never silently deletes staged media.
- [x] **Desktop Manager UI Integration** (`manager/ui/add_environment.py`, `manager/services/iso_service.py`):
  - Automatic detection of Windows installation media with cyan badge and architecture labels.
  - Auto-selection of `windows_extracted_media` boot method.
- [x] **End-to-End QEMU UEFI Chainload Verification**:
  - Validated Level 1 through Level 4 in QEMU with OVMF firmware, booting GRUB and executing chainloader transition into the EFI bootloader.
- [x] **Comprehensive Automated Testing**:
  - Test suite expanded from 207 to **236 passing tests** (190 unit tests + 46 integration tests, 0 failures, 0 regressions).
  - Dedicated Milestone 11 report authored in `docs/milestone-11-windows-boot.md`.
  - Dedicated empirical verification report authored in `docs/milestone-11-verification.md`.
  - **Decision 2.1 Applied**: Windows installation ISO boot paused and classified as experimental/unsupported due to UEFI firmware lack of exFAT filesystem driver (`0xc00000bb`).

---

## Milestone 12: Linux Distribution Compatibility Expansion (Completed)
- [x] **Expanded Distribution Recipe Engine** (`core/boot_config/recipes.py`):
  - Dedicated recipes: `RECIPE_OPENSUSE` (Kiwi/Dracut Live), `RECIPE_MX_LINUX` (antiX/MX linux-live), `RECIPE_ALPINE` (modloop), `RECIPE_SYSTEMRESCUE` (ArchISO base).
  - Aliases: Zorin OS, Elementary OS, Rescuezilla, Rocky Linux, AlmaLinux, Manjaro, EndeavourOS.
  - Safe path escaping, volume-label handling, loopback device cleanup (`loopback -d loop0`), and descriptive error diagnostics.
- [x] **Rock Ridge (`SUSP/RRIP`) Deep Filesystem Inspection** (`core/iso_manager/iso_inspector.py`):
  - Parsing of System Use Area (SUA) Alternate Name (`NM`) records to extract true POSIX filenames (e.g., `vmlinuz-virt` vs sanitized ISO 9660 `VMLINUZ_VIRT;1`).
  - High-confidence recipe identification across 8 major Linux distribution families.
- [x] **Empirical Boot Verification Across Four Distribution Families**:
  - Casper Family (Ubuntu): Level 4 reached (Interactive installer).
  - Debian Live-Boot Family (Clonezilla): Level 3 reached (Initramfs initialized).
  - Alpine Linux Family (Alpine Virt): Level 3 reached (Kernel and initramfs executed).
  - ArchISO & Dracut Families: Level 1/2 verified via automated fixtures and GRUB loopback syntax tests.
- [x] **Standardized Six-Tier Verification Taxonomy**:
  - Explicit classification across L0 (Implemented), L1 (Inspected), L2 (Kernel Loaded), L3 (Initramfs Initialized), L4 (Installer/Shell Reached), and L5 (Full Live Desktop).
- [x] **Documentation & Test Suite Expansion**:
  - Test suite expanded to **240 passing tests** (194 unit tests + 46 integration tests, 100% pass rate).
  - Dedicated Milestone 12 report authored in `docs/milestone-12-linux-compatibility.md`.
  - Updated `README.md` compatibility table.

---

## Milestone 13: Physical USB Testing and Installation Validation (Completed)
- [x] **Strict 12-Step Physical Validation Protocol** (`installer/physical_device_workflow.py`):
  - Hardware discovery, attribute verification, SHA-256 fingerprinting, itemized destructive plan generation, two-phase confirmation gate, pre-write bus revalidation, stage-tracked installation, partition integrity verification, controlled safe ejection, reconnection hotplug discovery, and UEFI/Linux hardware checklists.
  - Dedicated physical validation utility (`scripts/validate_physical_usb.py`).
- [x] **Comprehensive Physical Storage Safety Shields**:
  - Physical formatting locked by default (`allow_physical_formatting=False`).
  - Active system disk rejection (`is_system_disk=True`).
  - Strict two-phase confirmation requiring explicit checkbox, modal acceptance, and exact typing of the drive name.
  - Pre-write SHA-256 hardware fingerprint revalidation detecting drive swapping or bus renumbering.
- [x] **Hardware Compatibility Analysis**:
  - USB 2.0 vs USB 3.x controller differences, host bus hand-off, and UUID-based root discovery.
  - Removable Media Bit (RMB) handling for flash thumb drives vs external USB SSDs.
  - Sector size handling: 512n, 512e, and 4Kn with 1 MiB alignment preventing read-modify-write penalties.
  - Fallback UEFI boot path: `/EFI/BOOT/BOOTX64.EFI` guaranteeing instant bootability on any UEFI-compliant hardware.
- [x] **Installation Recovery Subsystem** (`installer/installation_recovery.py`):
  - Stage tracking across the 12-step installation pipeline.
  - Recovery from interrupted partitioning (LBA 0–33 header sanitization).
  - Recovery from interrupted filesystem formatting and failed bootloader deployments.
- [x] **Empirical Host Verification & Test Expansion**:
  - Transparent hardware presence disclosure (no physical USB connected to dev host; all physical tests simulated safely via virtual pipelines).
  - Automated test suite expanded to **254 passing tests** (208 unit tests + 46 integration tests, 100% pass rate).
  - Dedicated Milestone 13 report authored in `docs/milestone-13-physical-usb-validation.md`.

---

## Milestone 14: Desktop Manager and Device Management Maturity (Completed)
- [x] **Enhanced Dashboard & Live Device Metrics** (`manager/ui/dashboard.py`):
  - Total capacity, used space, free space on `MVX_DATA`.
  - Registered environments vs total ISO files on disk (e.g. "2 active in menu • 3 total ISOs found").
  - Live Read-Write vs. Read-Only access detection (`os.access`) on both partitions.
  - Dynamic validation badges (`VALID MVX`, `CORRUPTED`, `INCOMPLETE`, `STANDARD USB`, `DISCONNECTED`).
  - Direct action shortcuts: Open in File Explorer, Copy ISO to USB, Safely Eject USB.
- [x] **Asynchronous Worker Subsystem** (`manager/services/workers.py`):
  - `ISOCopyWorker`: Chunked (1 MiB) background copying with throughput calculation, margin-aware disk space checks, and cancellation with partial file deletion.
  - `ChecksumWorker`: Non-blocking SHA-256 calculation for large ISO images.
  - `SyncBootConfigWorker`: Asynchronous compilation and synchronization of GRUB configuration.
  - `OperationProgressDialog` (`manager/ui/progress_dialog.py`): Responsive modal dialog with live progress, speed metrics, and cancellation support.
- [x] **Hotplug Monitoring & Safe Ejection Integration**:
  - Live device arrival and departure event handling via `DeviceMonitor` without GUI freezing.
  - Coordinated safe ejection: cache flushing, volume unmounting, and UI disconnection transition.
  - Graceful handling of unexpected physical disconnections with warning notifications.
- [x] **Environment Library Usability Upgrades** (`manager/ui/environment_library.py`):
  - Multi-criteria sorting: Default order, Name (A-Z, Z-A), Size (Largest, Smallest), Boot Method, Enabled First.
  - Real-time text search filtering across names, target paths, and boot recipes.
  - Batch operations: Multi-row selection with Enable Selected, Disable Selected, and Remove Selected.
  - `EnvironmentDetailDialog`: Comprehensive environment inspector with on-demand background SHA-256 calculation.
  - Drag-and-drop ISO importing onto the management window.
- [x] **Comprehensive Test Suite Expansion**:
  - Automated test suite expanded to **264 passing tests** (218 unit tests + 46 integration tests, 100% pass rate).
  - Dedicated Milestone 14 report authored in `docs/milestone-14-desktop-manager.md`.

---

## Milestone 15: Recovery, Diagnostics and Failure Handling (Completed)
- [x] **Configuration Backup & Automated Rollback Subsystem** (`core/storage/storage_manager.py`):
  - Two-stage atomic write protocol (`.tmp` staging + `os.fsync` + atomic filesystem replace).
  - Automated timestamped backups in `MVX_DATA/backups/` before any persistent modification.
  - Retention limit enforcement (default: 10 backups) with rotation.
  - Automatic rollback on failed boot menu generation.
- [x] **Schema Validation & Resilient Repair Engine** (`core/config/repair.py`):
  - Detection and recovery from truncated or malformed `mventx.json` files.
  - Automatic restoration from the latest intact backup file.
  - Clean default configuration re-initialization if no valid backups exist (with complete preservation of `MVX_DATA/ISO/`).
  - Partial environment salvage: extracts valid environment records, discards corrupted entries, and re-sequences order sequentially.
- [x] **Multi-Attribute Diagnostic Report Generator** (`core/diagnostics/diagnostic_report.py`):
  - Gathers host OS details, Python runtime, CPU architecture, and admin / root privileges.
  - Probes storage bus, partition health, live Read-Write vs Read-Only flags, and skeleton integrity.
  - Performs configuration schema audit and environment cross-referencing against physical disk images.
  - Verifies presence of UEFI fallback bootloader (`/EFI/BOOT/BOOTX64.EFI`) and GRUB menu files.
  - Formatted JSON and Markdown export via CLI utility (`scripts/generate_diagnostic_report.py`) and GUI Settings screen.
- [x] **Virtual Disk Fault-Injection Regression Suite** (`tests/unit/test_recovery_diagnostics.py`):
  - Validated truncated JSON power-loss simulation, missing config restoration, broken schema salvage, corrupt backup bypass, and missing ISO detection.
  - Test suite expanded to **271 passing tests** (225 unit tests + 46 integration tests, 100% pass rate).
  - Dedicated Milestone 15 report authored in `docs/milestone-15-recovery-diagnostics.md`.

---

## Milestone 16: Cross-Platform Host Validation (Completed)
- [x] **Windows 10 / 11 Host Validation**:
  - Win32 storage device enumeration via PowerShell and WMI.
  - Multi-partition drive letter assignment (`MVX_BOOT` FAT32 + `MVX_DATA` exFAT).
  - Safe ejection via Windows Configuration Manager (`CM_Request_Device_Eject` / `FSCTL_DISMOUNT_VOLUME`).
  - NTFS / exFAT long path handling (>260 characters).
  - UAC elevation detection (`ctypes.windll.shell32.IsUserAnAdmin()`) and graceful non-elevated management.
  - PySide6 desktop GUI rendering with native dark graphite styling on Windows 11.
- [x] **Linux Desktop Host Validation**:
  - Block device enumeration using `lsblk` JSON and sysfs.
  - System disk safety filtering protecting `/`, `/boot`, `/boot/efi`, `/home`, and swap partitions.
  - Desktop-integrated rootless mounting via `udisksctl mount` and `findmnt`.
  - Native in-kernel exFAT and vfat module integration.
  - Safe power-off via `udisksctl power-off -b /dev/sdX` and `eject`.
  - Non-elevated desktop management with polkit support.
- [x] **Cross-Platform Compatibility & Feature Parity Matrix**:
  - Comprehensive feature comparison across Windows and Linux covering device detection, classification, USB installation, ISO registration, GRUB menu sync, safe ejection, hotplug monitoring, GUI manager, and diagnostic report generation.
- [x] **Consolidated Final Project Deliverables**:
  - Automated test suite expanded to **277 passing tests** (231 unit tests + 46 integration tests, 100% pass rate).
  - Dedicated Milestone 16 report authored in `docs/milestone-16-cross-platform-validation.md`.
  - All 16 milestones in the Master Development Directive completed and verified.
