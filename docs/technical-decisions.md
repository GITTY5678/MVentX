# MVentX Technical Decisions Record (TDR)

This document records the architectural and technical decisions made during the design of MVentX, along with their context, trade-offs, and rationale.

---

## TDR 001: Separation of Bootloader and Management Application

### Context
Multiboot solutions frequently bundle boot-time logic with host configuration tools, resulting in fragile dependencies or non-standard boot stages.

### Decision
Strictly decouple the bootloader (GRUB 2) and host application (Python 3 / PySide6). GRUB 2 must function exclusively via static configuration files (`grub.cfg`, `mventx_menu.cfg`) with zero runtime dependency on Python, Qt, or external daemons.

### Consequences
- **Pros**: Extreme boot-time reliability; universal compatibility on any x86_64 UEFI machine; no host runtime required at boot time.
- **Cons**: Configuration changes require the desktop app to recompile the JSON state into GRUB script format before unmounting.

---

## TDR 002: Manual ISO Registration vs. Automatic Directory Scanning

### Context
Ventoy uses dynamic directory scanning at boot time to display all `.iso` files found on disk.

### Decision
MVentX mandates **explicit manual registration**. Files copied to the USB are not displayed in the boot menu until the user explicitly registers and enables them in `mventx.json`.

### Consequences
- **Pros**:
  - Eliminates unwanted ISO entries (e.g., partial downloads, rescue tools, auxiliary utility images).
  - Allows full customization of display names, descriptions, and boot arguments without altering ISO filenames.
  - Allows fine-grained ordering and toggling (enabled/disabled).
  - Faster bootloader initialization (no real-time recursive filesystem scans at boot).
- **Cons**: Users must use the MVentX Manager (or edit JSON) after copying a new ISO to make it bootable.

---

## TDR 003: Dual Partition Scheme (FAT32 + exFAT)

### Context
UEFI firmware natively expects FAT32 for the EFI System Partition (ESP), but FAT32 cannot store files larger than 4 GB. Modern OS installation ISOs often exceed 4 GB (e.g. Windows 11 is > 5 GB).

### Decision
Format the USB drive with two partitions:
1. `MVX_BOOT` (FAT32, ~256 MB): Houses EFI bootloader, GRUB core, fonts, themes, and configuration files.
2. `MVX_DATA` (exFAT, remaining space): Stores ISO payloads and the JSON database.

### Consequences
- **Pros**:
  - Conforms to standard UEFI boot specs without hacks.
  - Accommodates large (>4 GB) ISOs.
  - exFAT is natively read/writable on Windows, macOS, and Linux without proprietary drivers.
- **Cons**: Requires GRUB's `exfat` module to read the payload partition at boot time.

---

## TDR 004: Versioned JSON Configuration

### Context
Configuration structures will expand as new boot methods, verification schemes, and themes are added.

### Decision
Store all user and environment state in a versioned JSON file (`mventx.json`) governed by a formal JSON Schema (`schema.json`).

### Consequences
- **Pros**: Human-readable, machine-validatable, easy to back up, simple to migrate across schema versions.
- **Cons**: Slight parsing overhead compared to binary blobs, but trivial for the scale of boot menus (< 100 entries).

---

## TDR 005: Optional Chainloading Support

### Context
Users often want quick access to chainload existing local disk installations (e.g., host Windows or Linux installations) from their rescue USB.

### Decision
Support chainloading as a first-class environment type alongside ISO boot, but keep it strictly optional. No Windows or internal OS entries are injected automatically.

### Consequences
- **Pros**: Complete user sovereignty over their boot menu.
- **Cons**: Users must manually specify or enable chainload targets if desired.

---

## TDR 006: Authoritative Configuration on Data Partition & Synchronized Boot Script

### Context
MVentX separates storage into an EFI boot partition (`MVX_BOOT`) and a data partition (`MVX_DATA`). When users add, edit, or remove environments, where should the authoritative configuration live, and how should it be compiled for GRUB?

### Decision
Store the single authoritative configuration in `MVX_DATA/config/mventx.json`. The host management engine (`StorageManager`) reads this configuration, applies schema validation, creates atomic backups in `MVX_DATA/backups/`, and compiles the resulting GRUB syntax into `MVX_BOOT/boot/grub/mventx_menu.cfg`.

### Consequences
- **Pros**:
  - `MVX_DATA` is the partition routinely mounted by desktop OS file explorers (Windows, macOS, Linux). Users and GUI tools can directly access the config, ISO payloads, and backups.
  - Interrupted menu writes do not corrupt the master environment database.
  - GRUB does not parse JSON at boot time; it directly loads native GRUB commands compiled into `mventx_menu.cfg`.
- **Cons**: Requires synchronization between `MVX_DATA` and `MVX_BOOT` whenever configuration changes are committed.

---

## TDR 007: Pure-Python Rootless Virtual Disk Builder for Development & CI

### Context
Testing dual-partition GPT disk images with FAT32 ESP and exFAT data on Windows usually requires elevated tools like `diskpart.exe attach vdisk` or third-party drivers. On Linux, it typically requires root `losetup` or `parted`. Requiring administrator privileges slows down development, complicates CI, and introduces system-level risk.

### Decision
Implement a pure-Python virtual disk builder (`scripts/windows/make_dual_partition_disk.py`) that writes raw sector-level GPT tables, FAT32 boot structures, and valid Microsoft-compliant exFAT structures (VBR, allocation bitmap, upcase table, directory sets) directly into a `.img` file in user-space without administrator privileges.

### Consequences
- **Pros**:
  - Full virtual test disk creation runs in seconds on any Windows 11 machine without elevation.
  - Safe for CI/CD pipelines without loopback or root access.
  - Fully compliant GPT, FAT32, and exFAT structures verifiable via `disk_validator.py`.
- **Cons**: File writes into the virtual exFAT filesystem must be handled by the builder's directory set generator rather than host OS filesystem drivers. Physical USB installations still require administrator privileges for raw device I/O.

---

## TDR 008: Verification Tiering & Direct Cluster Traversal for Storage Validation

### Context
Validating dual-partition storage before kernel booting requires testing disk integrity, partition tables, and filesystem structures. However, attaching virtual disks as loopback volumes on Windows (`Mount-DiskImage`, Virtual Disk Service CIM) strictly requires Administrator UAC elevation. Running tests exclusively as an elevated user is undesirable for automated CI and non-elevated developer workflows, but claiming Windows filesystem interoperability without live verification would be inaccurate.

### Decision
Adopt a strict 4-level verification tiering framework:
1. **Tier 1 (Structurally Validated)**: Pure-Python sector-level parser (`core/storage/disk_validator.py`) inspecting GPT headers, CRC32s, LBA bounds, FAT32 cluster thresholds, and exFAT Sector 11 checksums. Implements raw cluster traversal to read files (`config/mventx.json`, `boot/grub/grub.cfg`) directly without OS mounting.
2. **Tier 2 (Windows Filesystem Operations)**: Pure-Python Fixed VHD generation (`convert_to_vhd.py`), dynamic VHD/VHDX conversion, and automated 10-step Windows loopback mount lifecycle script (`test_windows_mount.ps1`) with transparent UAC elevation boundary detection.
3. **Tier 3 (Tested Through QEMU)**: UEFI firmware (OVMF) execution with split read-only code pflash and writable variable store (`run_qemu.ps1`), GRUB 2.12 execution, dynamic configuration discovery, and fallback rendering.
4. **Tier 4 (Not Yet Verified)**: Full OS kernel booting (Linux loopback recipes and Windows chainloading) scheduled for subsequent milestones.

### Consequences
- **Pros**:
  - Tests run safely and deterministically in unprivileged user environments and standard CI pipelines.
  - Transparently separates structural correctness from kernel loopback driver behavior.
  - Documents exact elevation requirements for live Windows volume mounting without masking security boundaries.
- **Cons**: Requires maintaining the sector-level FAT32 and exFAT reader in `DiskValidator`.

---

## TDR 009: Two-Tier String Sanitization & Automated Headless QEMU Verification

### Context
User-defined display names in the desktop GUI may contain quotation marks, brackets, slashes, or foreign language characters. Escaping these characters during initial input validation causes double-escaping when written to JSON and subsequently read by the GRUB generator (`\"` becoming `\\\"`). Additionally, verifying that generated GRUB menus render properly without manual human inspection requires a reproducible, scriptable VM testing mechanism that functions in automated test runs.

### Decision
1. **Two-Tier String Processing**:
   - `validate_display_name()`: Enforces length, forbids control characters, and blocks command substitution (`$(...)`, `` `...` ``) without mutating the string. The natural user string is preserved in authoritative JSON.
   - `sanitize_display_name()`: Applied strictly when compiling GRUB script syntax (`mventx_menu.cfg`) to escape quotes, standalone `$`, and backslashes into valid GRUB string literals.
2. **Headless QEMU Screendump Verification**:
   - Use QEMU monitor (`-display none -monitor stdio`) to boot test disks with OVMF firmware.
   - Issue `screendump` commands to capture raw framebuffer PPMs.
   - Analyze pixel color histograms (cyan highlight `RGB(84, 254, 254)`, border/text `RGB(168, 168, 168)`) to confirm active `gfxterm` rendering programmatically.
   - Convert PPMs to high-resolution PNGs for artifact documentation.

### Consequences
- **Pros**:
  - Eliminates double-escaping bugs in JSON while maintaining total protection against GRUB script injection.
  - Enables fully automated, headless verification of UEFI boot and GRUB menu rendering in CI and regression test suites.
  - Generates visual evidence (PNG screenshots) for documentation without requiring manual VM interaction.
- **Cons**: QEMU boot execution requires 5-6 seconds of VM execution per scenario.

---

## TDR 010: Linux Live ISO Loopback Architecture & 4-Tier Boot Verification

### Context
Milestone 7 requires booting live Linux distributions directly from unextracted `.iso` images stored on the exFAT basic data partition (`MVX_DATA`). Live Linux distributions use different initramfs architectures (Casper, ArchISO, Dracut, Debian live-boot) requiring distinct kernel arguments to locate and mount their backing ISO image. Furthermore, bootloader claims must distinguish between menu syntax generation, kernel loading, initramfs execution, and reaching an interactive user environment.

### Decision
1. **Distribution Recipe Engine (`core/boot_config/recipes.py`)**:
   - Encapsulate kernel path, initramfs path, and command-line arguments into discrete recipe definitions (`casper`, `archiso`, `dracut`, `live_boot`, `generic_kernel`).
   - Automatically detect the recipe from ISO volume descriptors or filename hints while allowing user overrides (`kernel_path`, `initrd_path`, `custom_args`).
2. **Defensive GRUB Loopback Generation**:
   - Locate data partition dynamically via `search --no-floppy --label --set=mventx_data_dev MVX_DATA`.
   - Verify file existence before attempting loopback mount.
   - Attach ISO via `loopback loop0 ($mventx_data_dev)/${iso_path}`.
   - Load kernel and initrd directly from `(loop0)`.
   - On error, display human-readable diagnostics and pause before returning to the main menu.
3. **Four-Level Verification Standard**:
   - **Level 1 (Menu Generation)**: Validated via automated unit tests.
   - **Level 2 (Kernel Loading)**: Validated in QEMU via early serial console printk messages.
   - **Level 3 (Initramfs Execution)**: Validated via initramfs daemon startup (`syslogd, klogd`).
   - **Level 4 (Live OS Environment)**: Validated via interactive user space reach (installer UI).
4. **Low-Level Filesystem Support**:
   - Fixed exFAT builder to support multi-entry directory sets for filenames > 15 characters with `NoFatChain` flags.
   - Fixed FAT32 builder to support multi-cluster directory allocation chains for the 280 runtime GRUB dynamic modules.

### Consequences
- **Pros**:
  - Unextracted ISO images boot natively without disk reformatting or extraction overhead.
  - Transparent, honest reporting of boot readiness across all four verification levels.
  - Full automated coverage in QEMU without physical USB drive manipulation.
- **Cons**: Real kernel booting in headless QEMU takes ~30-40 seconds in integration test runs.




---

## TDR 011: Multi-Distribution ISO Loopback Boot Architecture & 6-Tier Compatibility Framework

### Context
Milestone 8 focuses on multi-distribution Linux compatibility and boot reliability. Different distributions structure their ISO root filesystems differently (e.g., Ubuntu uses `/casper/`, Debian Live uses `/live/`, Arch Linux uses `/arch/boot/`, Fedora uses `/images/pxeboot/`, while netboot distributions use root `/linux` or `/boot/`). Relying purely on filename hints is fragile and error-prone. Additionally, boot verification must clearly distinguish between early kernel decompression, initramfs execution, text installers, and full graphical desktop environments.

### Decision
1. **Direct ISO Filesystem Introspection (`ISOInspector`)**:
   - Directly parse ISO 9660 Primary Volume Descriptors (PVD) and directory records by following Logical Block Addresses (LBAs) into first-tier and second-tier directories (`/casper`, `/live`, `/arch`, `/images/pxeboot`, `/boot`).
   - Discover kernel binaries and initramfs archives within intact ISO files.
   - Infer recipes with confidence scoring (`high`, `medium`, `uncertain`).
   - Emit diagnostic warnings when distribution detection is uncertain; never silently default to an unverified configuration.
2. **Six-Tier Compatibility Classification System**:
   - Standardize boot verification into 6 explicit tiers: Level 0 (Recipe Implemented), Level 1 (ISO Filesystem Inspected), Level 2 (Kernel Loading Tested), Level 3 (Initramfs Loading Tested), Level 4 (Live OS Interactive User Space), and Level 5 (Modern Graphical Live Desktop).
   - Accurately report Ubuntu 24.04 LTS Desktop download constraints (~56 minutes required for 5.82 GB download at ~1.7 MB/s) and avoid misrepresenting netboot installer milestones as full graphical desktop boot.
3. **Resilient GRUB Runtime Error Recovery**:
   - Check partition existence (`MVX_DATA`) and ISO existence (`[ ! -f "${iso_file}" ]`).
   - Ensure clean disposal of prior loop devices with `loopback -d loop0`.
   - Inspect kernel and initrd paths within loopback before execution (`[ ! -f "(loop0)${kernel_path}" ]`).
   - Render formatted error screens and pause before returning to menu (preventing drops to rescue shells).
   - Quote paths with spaces across all GRUB definitions and kernel command-line arguments.

### Consequences
- **Pros**:
  - Eliminates guesswork by inspecting actual files within intact ISOs without mounting or extracting them.
  - Complete honesty and rigor in compatibility reporting.
  - Graceful recovery in GRUB when disks or ISO files are missing or modified.
- **Cons**: Deep directory traversal reads ISO metadata sectors over file streams, but overhead is minimal (< 5 ms per ISO).

---

## TDR 012: USB Installation Subsystem, Safety Gates, and Verification Engine

### Context
Milestone 9 introduces the complete automated USB preparation and installation pipeline. Formatting disks and partitioning storage carries extreme operational risk: an erroneous device selection or improper boundary check could corrupt host operating system drives (e.g. `C:\`, `/`, or `/boot`). Furthermore, multiboot media must strictly adhere to UEFI GPT partitioning standards, 1 MiB alignment, FAT32 ESP specifications, and exFAT VBR structures to guarantee bootability across disparate hardware firmwares.

### Decision
1. **Strict Safety Gates & Physical Formatting Lockdown**:
   - `PartitionManager` enforces `allow_physical_formatting = False` by default. Any attempt to write to physical block devices without explicitly providing this developer flag raises an immediate `DeviceSafetyError`.
   - `DeviceManager.assert_safe_target()` actively checks partition tables and mount points, strictly rejecting targets containing `C:\`, `/`, `/boot`, `/boot/efi`, `PhysicalDrive0` with system partitions, or read-only storage.
   - The desktop GUI never pre-selects a physical drive, enforces a confirmation checkbox, and displays a modal confirmation dialog before proceeding.
2. **1 MiB Alignment & Standardized GPT Math**:
   - `PartitionPlanner` enforces `ALIGNMENT_SECTORS_1MB = 2048` sectors (1,048,576 bytes) for all partition start and end boundaries.
   - Reserves standard LBA 0 (Protective MBR), LBA 1 (Primary GPT Header), and LBA 2–33 (Primary GPT Entries).
   - Partition 1 (`MVX_BOOT`) is configured as FAT32 ESP (`C12A7328-F81F-11D2-BA4B-00A0C93EC93B`).
   - Partition 2 (`MVX_DATA`) is configured as exFAT Basic Data (`EBD0A0A2-B9E5-4433-87C0-68B6B72699C7`).
3. **Automated 10-Point Verification Engine (`InstallationVerifier`)**:
   - Every completed installation is validated prior to returning success: Protective MBR, Primary/Backup GPT CRC32, 1 MiB alignment, FAT32 VBR/FSInfo, exFAT VBR/checksum, bootloader binary presence, GRUB runtime module library (280 modules), fallback menu syntax, and authoritative configuration file with 0 initial environments.
4. **Failure Cleanup & Safe Rollback (`InstallationRecoveryService`)**:
   - Intermediate files and temporary disk images are tracked throughout the 12-step installation sequence. On any failure or user cancellation, all temporary assets are deleted and resources released.

### Consequences
- **Pros**:
  - Zero risk of accidental host drive corruption during virtual development and testing.
  - Fully automated, standard-compliant disk layout creation and verification.
  - Transparent progress and cancellation support in the desktop GUI.
  - Complete QEMU UEFI boot verification confirming fallback menu initialization.
- **Cons**: Physical USB drive flashing is intentionally locked behind safety guards until dedicated hardware testing milestones.

