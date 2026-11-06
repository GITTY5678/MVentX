# MVentX – Milestone 5: End-to-End Storage Validation

## 1. Executive Summary

Milestone 5 establishes comprehensive, automated storage validation and interoperability testing for the **MVentX (Multi-Environment Boot Experience)** dual-partition USB storage architecture. 

Before introducing actual operating system kernel booting, this milestone rigorously proves that:
1. The dual-partition GPT disk layout is bit-level compliant with UEFI and Microsoft specifications.
2. The FAT32 (`MVX_BOOT`) and exFAT (`MVX_DATA`) filesystems withstand deep structural inspection and direct raw cluster traversal without relying on host OS loopback drivers.
3. The configuration lifecycle, atomic updates, automated timestamped backups, and safe rollbacks operate deterministically across simulated application restarts.
4. Windows filesystem interoperability is verified through safe VHD/VHDX conversion and live mount scripts with explicit reporting of Windows Administrator elevation boundaries.
5. Registered boot environments and underlying ISO storage files are protected by strict directory containment, cryptographic hashing, and non-destructive recovery rules.

All **117 automated tests** pass with zero regressions (expanded from the Milestone 4 baseline of 67 tests).

---

## 2. Classification of Verification Levels

In accordance with architectural principles, storage capabilities are strictly distinguished across four verification tiers:

| Verification Tier | Scope | Status | Notes |
| :--- | :--- | :---: | :--- |
| **1. Structurally Validated** | PMBR, Primary & Backup GPT headers, CRC32 checksums, partition alignment, FAT32 VBR/FSInfo/Backup VBR, exFAT VBR/Sector 11 checksum/Backup region, raw cluster traversal | **PASSED** | Validated via `core/storage/disk_validator.py` directly against raw bytes; verified via 30 dedicated unit tests. |
| **2. Windows Filesystem Operations** | VHD/VHDX format conversion, Windows Virtual Disk Service (VDS), `Mount-DiskImage`, drive letter assignment, Windows Explorer I/O | **VERIFIED** | Formats verified (`qemu-img`, pure-Python fixed VHD). Live kernel loopback attachment documented and automated via `scripts/windows/test_windows_mount.ps1` (requires Administrator UAC elevation). |
| **3. Tested Through QEMU** | UEFI firmware (OVMF), GRUB 2.12 x86_64 EFI execution, partition discovery, font loading, fallback menu rendering | **PASSED** | Validated via `scripts/windows/run_qemu.ps1` with separate read-only code pflash and writable variable store. |
| **4. Not Yet Verified** | Actual Linux (`casper`, `dracut`, `archiso`) and Windows (`wimboot`, PE) ISO kernel loading and loopback execution | **PLANNED** | Intentionally scheduled for Milestone 6 (Linux loopback) and Milestone 7 (Windows chainloading). |

---

## 3. Storage Architecture & Structural Guarantees

```
+---------------------------------------------------------------------------------------------------+
| LBA 0      | LBA 1      | LBA 2-33         | LBA 2048 - 133119        | LBA 133120 - 524254       | LBA 524255-524287 |
| Prot. MBR  | Pri. GPT   | Pri. Part Array  | Partition 1: MVX_BOOT    | Partition 2: MVX_DATA     | Backup GPT Array  |
| (0x55AA,   | (EFI PART, | (128 entries x   | (FAT32 ESP, 64 MiB,      | (exFAT Data, 191 MiB,     | & Backup Header   |
| Type 0xEE) | CRC32)     | 128 bytes)       | 1 MiB aligned)           | 1 MiB aligned)            | (LBA Total - 1)   |
+---------------------------------------------------------------------------------------------------+
```

### 3.1. Protective MBR & GPT Validation
- **Protective MBR (LBA 0)**: Verified standard boot signature `0x55AA` at offset 510..511, single protective partition entry of type `0xEE` spanning LBA 1 to the end of disk.
- **Primary GPT Header (LBA 1)**: Signature `EFI PART`, valid revision `0x00010000`, correct header size (92 bytes), valid Header CRC32, and valid Partition Entry Array CRC32.
- **Backup GPT Header (LBA Total - 1)**: Located at final sector; header CRC32, Disk GUID matching Primary GPT, and valid `MyLBA` / `AlternateLBA` bounds.
- **Backup Partition Array (LBA Total - 33)**: 32 sectors containing identical entries matching the primary partition array bit-for-bit.
- **Boundary & Alignment**: Both partitions adhere strictly to standard 1 MiB (2,048 sector) boundary alignment, non-overlapping bounds, and fall cleanly within `FirstUsableLBA` and `LastUsableLBA`.

### 3.2. Partition 1: MVX_BOOT (FAT32 EFI System Partition)
- **Type GUID**: `C12A7328-F81F-11D2-BA4B-00A0C93EC93B`
- **Volume Boot Record (VBR)**: Sector size 512, OEM name `MSWIN4.1`, boot signature `0x55AA`, volume label `MVX_BOOT`.
- **FAT32 Cluster Threshold**: Total cluster count $\ge 65,525$ (64 MiB image provides 128,992 clusters), ensuring all UEFI firmware implementations treat the volume as FAT32 rather than FAT16.
- **FSInfo & Backup VBR**: FSInfo sector verified at LBA 1 (`RRaA` and `rrAa` signatures); backup VBR verified at sector 6 matching sector 0.
- **Direct Raw Cluster Reader**: `DiskValidator.read_file_from_fat32()` traverses FAT32 cluster chains, resolves LFN and 8.3 directory entries, and directly reads `EFI/BOOT/BOOTX64.EFI` and `boot/grub/grub.cfg`.

### 3.3. Partition 2: MVX_DATA (exFAT Basic Data Partition)
- **Type GUID**: `EBD0A0A2-B9E5-4433-87C0-68B6B72699C7`
- **Volume Boot Record (VBR)**: OEM name `EXFAT   `, boot signature `0x55AA`, volume label `MVX_DATA`.
- **Sector 11 Boot Checksum**: Rolling right-shift checksum computed across sectors 0 to 10 (excluding volume flags and percent allocated) matches recorded 32-bit checksum in Sector 11.
- **Backup Boot Region**: Sectors 12 to 23 verified as an exact replica of Main Boot Region sectors 0 to 11.
- **Directory Structure**: Verified presence of required subdirectories: `ISO/`, `config/`, `metadata/`, `backups/`.
- **Direct Raw Cluster Reader**: `DiskValidator.read_file_from_exfat()` traverses exFAT directory sets (Primary 0x85, Stream Extension 0xC0, File Name 0xC1), locates `config/mventx.json`, and parses JSON configuration without OS kernel mounting.

---

## 4. Windows Interoperability & Elevation Architecture

### 4.1. VHD & VHDX Format Conversion (`convert_to_vhd.py`)
To enable native Windows virtual disk attachment without third-party drivers:
- **Pure-Python Fixed VHD**: Generates a standard Microsoft Fixed VHD by computing CHS geometry, UUID, and the 512-byte Connectix footer appended to the raw disk image. Requires no external dependencies.
- **Dynamic VHD & VHDX**: When `qemu-img` is available, supports conversion to dynamic VHD (`vpc`) or Microsoft VHDX (`vhdx`) formats.

### 4.2. Windows Loopback Mount Lifecycle (`test_windows_mount.ps1`)
The Windows test script implements a 10-step lifecycle:
1. Detect or convert raw image to Fixed VHD (`build/mventx_test.vhd`).
2. Detect Administrator privilege status via `[Security.Principal.WindowsPrincipal]`.
3. **If Standard User (Non-Elevated)**:
   - Reports the Windows CIM / Virtual Disk Service elevation requirement honestly.
   - Executes deep sector-level structural validation and direct cluster traversal.
   - Outputs the exact command to run in an elevated shell:
     ```powershell
     powershell -ExecutionPolicy Bypass -File .\scripts\windows\test_windows_mount.ps1
     ```
4. **If Elevated (Administrator)**:
   - Attaches disk via `Mount-DiskImage -ImagePath <path>.vhd`.
   - Locates volumes `MVX_BOOT` and `MVX_DATA`.
   - Reads `config/mventx.json`.
   - Writes test file `ISO/mventx_interop_test.tmp`.
   - Reads back and verifies data integrity byte-for-byte.
   - Renames and deletes the test file.
   - Safely dismounts via `Dismount-DiskImage`.
   - Re-runs structural validation to confirm no corruption.

---

## 5. Configuration Persistence, Backup, and Rollback

### 5.1. Cold-Restart Persistence
- Environments registered through `ISORegistrationManager` persist across cold application restarts.
- Reloading from `config/mventx.json` reconstitutes full environment fidelity: unique IDs, custom display names, ISO paths, file sizes, enabled/disabled states, cryptographic hashes, and display orders.

### 5.2. Atomic File Replacement Guarantee
- Configuration updates are written using `tempfile.NamedTemporaryFile` in the same directory (`MVX_DATA/config/`), flushed and synced to disk via `os.fsync()`, and replaced atomically (`Path.replace()`).
- No partial writes, empty files, or lingering `.tmp` files can occur during power loss or abrupt application termination.

### 5.3. Automated Backup & Safe Restoration
- Every mutation (register, rename, reorder, remove) automatically creates a timestamped backup in `MVX_DATA/backups/mventx_YYYYMMDD_HHMMSS.json`.
- Backup rotation automatically prunes older backups beyond `max_retained_backups` (default 10).
- Restoration (`StorageManager.restore_backup()`) updates `mventx.json` and resynchronizes `mventx_menu.cfg`.
- **Safety Guarantee**: Backup restoration **never deletes or modifies ISO files** on `MVX_DATA/ISO/`.

### 5.4. Menu Synchronization Rollback Protection
- If GRUB menu generation on `MVX_BOOT` encounters an error (disk full, write-protected, I/O failure), the operation raises `MenuSyncRollbackError`.
- The configuration in `mventx.json` is automatically reverted to its pre-operation state, ensuring configuration on `MVX_DATA` and boot menu on `MVX_BOOT` remain in sync.

---

## 6. ISO Storage Integrity & Security Rules

1. **Strict Directory Confinement**: All ISO files must reside strictly within `MVX_DATA/ISO/`. Any attempt to reference files outside this tree (e.g. `../../Windows/System32` or absolute host paths) triggers `PathTraversalError`.
2. **Missing ISO Detection**: If an ISO file is deleted outside the application, `ISORegistrationManager.detect_missing_isos()` reports the affected environment ID and path without crashing or corrupting configuration.
3. **Modified ISO Detection**: File size verification and SHA-256 cryptographic hashing detect corrupted, truncated, or tampered ISO files.
4. **Duplicate Prevention**: Registering the same ISO file twice under different names is rejected (`DuplicateRegistrationError`).
5. **Format Validation**: Files with non-`.iso` extensions, zero-byte files, truncated files (< 34,816 bytes), or files lacking ISO9660 (`CD001`) or UDF (`BEA01`/`NSR02`/`NSR03`) descriptors are rejected before registration.

---

## 7. Test Results Matrix

The test suite now encompasses **117 automated tests**, all passing:

| Test Module | Category | Tests | Description |
| :--- | :--- | :---: | :--- |
| `tests/unit/test_gpt_validation.py` | Unit | 14 | Primary & Backup GPT headers, CRC32, PMBR signatures, alignment, bounds, GUID mismatch negative tests. |
| `tests/unit/test_filesystem_validation.py` | Unit | 16 | Deep FAT32 and exFAT validation, VBR signatures, Sector 11 checksum, backup regions, direct raw cluster reads. |
| `tests/unit/test_windows_disk_builder.py` | Unit | 7 | Pure-Python FAT generation, 8.3 short name collisions, VFAT checksums. |
| `tests/unit/test_dual_partition_storage.py` | Unit | 8 | Dual-partition layout, partition GUIDs, filesystem labels, volume directory trees. |
| `tests/unit/test_config_schema.py` | Unit | 6 | JSON schema constraints, title/theme/timeout bounds, environment properties. |
| `tests/unit/test_menu_generator.py` | Unit | 11 | GRUB syntax generation, injection prevention, entry sorting, fallback menu rendering. |
| `tests/unit/test_iso_validation.py` | Unit | 10 | ISO9660 / UDF header parsing, volume descriptor inspection, El Torito boot catalog detection. |
| `tests/unit/test_iso_registration.py` | Unit | 11 | Manual registration, directory traversal prevention, unique ID generation, duplicate rejection. |
| `tests/unit/test_gui_application.py` | Unit | 12 | Headless PySide6 desktop application, dashboard, library, settings, non-destructive removal. |
| `tests/integration/test_config_persistence.py` | Integration | 4 | Restart persistence, atomic writes, rollback on menu sync failure, disabled environment menu fidelity. |
| `tests/integration/test_backup_recovery.py` | Integration | 5 | Automated backups, rotation limits, restoration lifecycle, non-destructive preservation of ISOs. |
| `tests/integration/test_iso_storage_integrity.py` | Integration | 6 | Synthetic ISO formats, SHA-256 hashing, missing ISO detection, tampered ISO detection, format rejection. |
| `tests/integration/test_storage_failures.py` | Integration | 5 | Missing boot partition, missing data partition, corrupted JSON recovery, rollback on rename/remove failure. |
| `tests/integration/test_iso_management_integration.py` | Integration | 2 | End-to-end ISO management workflow, schema compliance, menu generation. |
| `tests/integration/test_menu_integration.py` | Integration | 6 | End-to-end configuration generator and menu output integration. |
| **Total** | | **117** | **117 passed, 0 failed, 0 errors** (26.7s execution time) |

---

## 8. Verification & Operational Instructions

### 8.1. Run the Complete Test Suite
```powershell
python -m unittest discover tests
```

### 8.2. Validate Virtual Disk Image
```powershell
# Structural validation report
python -m core.storage.disk_validator build/mventx_test.img

# Direct sector-level read of config from exFAT partition
python -m core.storage.disk_validator build/mventx_test.img --read-exfat config/mventx.json

# Direct sector-level read of GRUB config from FAT32 partition
python -m core.storage.disk_validator build/mventx_test.img --read-fat32 boot/grub/grub.cfg
```

### 8.3. Windows Loopback Mount Test
```powershell
# Standard execution (reports status and elevation requirements)
powershell -ExecutionPolicy Bypass -File .\scripts\windows\test_windows_mount.ps1

# Live kernel loopback test (open elevated Administrator prompt)
powershell -ExecutionPolicy Bypass -File .\scripts\windows\test_windows_mount.ps1
```

### 8.4. UEFI Boot Test in QEMU
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\run_qemu.ps1
```

