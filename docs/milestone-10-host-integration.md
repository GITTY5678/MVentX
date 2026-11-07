# MVentX – Milestone 10: Cross-Platform Host Integration

## 1. Executive Summary

Milestone 10 establishes the **Cross-Platform Host Integration Subsystem** for MVentX. The subsystem provides the software bridge between desktop operating systems (Windows 10/11 and Linux desktop distributions) and connected MVentX storage devices. It enables the desktop management application to discover, identify, classify, access, manage, and safely eject existing MVentX USB media without altering the host operating system or risking accidental data loss.

In accordance with strict safety mandates:
1. **Physical formatting remains disabled** throughout Milestone 10 (`allow_physical_formatting = False`).
2. **Device detection, classification, mounting, and modification are completely separated**. Detection and classification are strictly read-only and never automatically mount, format, or repair a device.
3. **Strong Device Identity** (`DeviceIdentity`) prevents drive-letter drift, volume swapping, or accidental writes to disconnected or imposter disks by enforcing pre-write identity revalidation.
4. **An explicit Device Lifecycle State Machine** (`DeviceStateMachine`) tracks devices from discovery through identification, validation, operational readiness, active operations, safe ejection, and disconnection.
5. **Atomic configuration synchronization** guarantees automated backups, schema validation, GRUB menu compilation verification, and safe rollback upon failure.

The automated regression test suite expanded from **176 passing tests** (Milestone 9) to **207 passing tests** (165 unit tests + 42 integration tests), maintaining a 100% pass rate with zero regressions.

---

## 2. Architecture & Subsystem Structure

The host integration layer is encapsulated in `core/host/` with clean platform separation:

```
core/host/
├── __init__.py                 # Clean public subsystem exports
├── host_platform.py            # Platform detection, OS capabilities, elevation checks
├── device_state.py             # Explicit device lifecycle state machine (14 states)
├── device_identity.py          # Cryptographic multi-attribute fingerprinting & pre-write guards
├── device_classification.py    # Multi-attribute classification (5 statuses, read-only)
├── filesystem_access.py        # Atomic JSON writer, backup rotation, and rollback sync
├── mount_manager.py            # Mount tracking, ownership management, and unmount interface
├── safe_eject.py               # Buffer flushing, handle release, and OS eject dispatcher
├── device_monitor.py           # Hotplug arrival/removal monitor with delta detection
├── host_device_service.py      # Master host coordinator bridging providers to desktop UI
├── windows/                    # Windows 10/11 native provider implementation
│   ├── windows_device_provider.py  # PowerShell Get-Disk, Get-Partition, Win32 GetVolumeInformationW
│   ├── windows_mount_manager.py    # Drive letter discovery and assignment
│   ├── windows_eject.py            # Shell.Application COM InvokeVerb("Eject")
│   └── windows_filesystem.py       # Win32 volume serial numbers and removable drive checks
└── linux/                      # Linux distribution native provider implementation
    ├── linux_device_provider.py    # lsblk JSON, sysfs, and udev property inspection
    ├── linux_mount_manager.py      # udisksctl mount / findmnt / umount
    ├── linux_eject.py              # udisksctl power-off / eject
    └── linux_filesystem.py         # POSIX permissions and sync
```

---

## 3. Key Subsystem Implementations

### 3.1 Device Lifecycle State Machine (`core/host/device_state.py`)

Every storage device is tracked through an explicit state machine preventing illegal operations:

```
[DETECTED]
    │
    ▼
[IDENTIFYING] ──────> [INACCESSIBLE]
    │
    ▼
[IDENTIFIED]  ──────> [ORDINARY]
    │
    ▼
[VALIDATING]  ──────> [INCOMPLETE] / [CORRUPTED]
    │
    ▼
 [READY] <──────────> [BUSY]
    │
    ▼
[EJECTING] ─────────> [EJECTED]
    │
    └──(Unplugged)──> [DISCONNECTED]
```

- **`READY`**: Device has been structurally classified as a valid MVentX installation, mount points are verified for read/write access, and management operations may proceed safely.
- **`BUSY`**: Active I/O, ISO copying, or configuration synchronization is running; ejection and concurrent mutations are blocked.
- **`DISCONNECTED`**: Device was removed or lost; all pending writes are immediately blocked to protect data integrity.

### 3.2 Strong Device Identity & Pre-Write Verification (`core/host/device_identity.py`)

Relying on transient identifiers like drive letters (`E:\`) or mount paths (`/media/user/MVX_DATA`) is unsafe because host operating systems can reassign them upon reconnection or when other drives are plugged in.

MVentX computes a deterministic SHA-256 fingerprint from:
- GPT Disk GUID / Unique identifier
- Partition unique GUIDs (`UniquePartitionGUID` / `PARTUUID`)
- Partition type GUIDs (ESP `C12A7328-...` and Basic Data `EBD0A0A2-...`)
- Hardware serial number
- Device model and manufacturer
- Raw disk capacity and logical sector size

**Pre-Write Guard (`revalidate_device_identity`)**:
Immediately before every write operation (configuration update, ISO copy, menu synchronization), the target storage media is re-probed. If the device has disappeared or if an imposter device with the same drive letter is detected, `DeviceDisappearedError` or `DeviceIdentityMismatchError` is raised, aborting the write before any data is altered.

### 3.3 Multi-Attribute Classification (`core/host/device_classification.py`)

MVentX identifies installations using multiple attributes rather than relying solely on volume labels:
1. Partition 1 must be an EFI System Partition (ESP GUID or `MVX_BOOT` label).
2. Partition 2 must be a Basic Data Partition (GUID or `MVX_DATA` label).
3. The data partition must contain the standard directory skeleton (`ISO/`, `config/`, `metadata/`, `backups/`).
4. The authoritative configuration file `config/mventx.json` must be present, valid JSON, and strictly conform to `core/config/schema.json`.
5. If the boot partition is accessible, `EFI/BOOT/BOOTX64.EFI` and `boot/grub/grub.cfg` must be present.

**Classification Statuses (`MVentXStatus`)**:
- `VALID_MVENTX`: Compliant and operational.
- `INCOMPLETE_INSTALLATION`: Missing a partition, bootloader binary, or directory skeleton.
- `CORRUPTED_INSTALLATION`: Syntax error in `mventx.json` or JSON schema validation failure.
- `ORDINARY_STORAGE`: Device lacks MVentX partition layout.
- `INACCESSIBLE`: Device is unmounted, locked, or permission denied.

*Strict Architectural Rule*: Classification is read-only. It never automatically mounts, repairs, or formats an incomplete or corrupted installation.

### 3.4 Atomic Configuration Synchronization with Rollback (`core/host/filesystem_access.py`)

To ensure the USB storage media can never be left in a corrupted or half-written state:
1. **Pre-Validation**: Configuration dictionaries are validated against JSON schema before any file touch.
2. **Automated Timestamped Backups**: A timestamped copy (e.g. `mventx_20261106_091500_123456.json`) is created in `backups/` and rotated to preserve up to 10 historical snapshots.
3. **Atomic File Writing**: Configuration is written to a temporary file in the same directory (`mventx_cfg_*.tmp`), flushed with `os.fsync()`, and atomically moved into place using filesystem atomic rename.
4. **GRUB Menu Compilation**: `GrubMenuGenerator` compiles `boot/grub/mventx_menu.cfg` on the boot partition.
5. **Safe Rollback**: If menu compilation fails (e.g. storage error or corrupted syntax), `mventx.json` is automatically restored to its original state, and a `ConfigurationRollbackError` is raised.

### 3.5 Safe Ejection Service (`core/host/safe_eject.py`)

Coordinates clean device unmounting and hardware detachment:
1. Checks that no foreground or background file operations are running (`is_busy == False`).
2. Flushes OS filesystem buffers (`FlushFileBuffers` on Windows, `os.sync` on Linux).
3. Verifies that configuration synchronization is complete.
4. Unmounts partitions owned by MVentX without disrupting partitions mounted externally by the user.
5. Dispatches safe ejection to the operating system:
   - **Windows**: Invokes Shell.Application COM verb: `(New-Object -ComObject Shell.Application).Namespace(17).ParseName('E:').InvokeVerb('Eject')`.
   - **Linux**: Calls `udisksctl power-off -b /dev/...` or `eject`.
6. Transitions state machine to `EJECTED` upon confirmation.

### 3.6 Device Monitoring & Disconnection Safety (`core/host/device_monitor.py`)

- Monitors host storage bus for device arrival, removal, and partition mount changes.
- Dispatches event callbacks (`on_device_connected`, `on_device_disconnected`, `on_device_changed`).
- On disconnection of the active MVentX device:
  - Immediately transitions state machine to `DISCONNECTED`.
  - Clears active target reference to prevent further write attempts.
  - Emits UI warning notification.

---

## 4. PySide6 Desktop GUI Integration

The PySide6 desktop suite (`manager/ui/main_window.py`, `manager/services/device_service.py`) incorporates:
- **Device Selector Dropdown**: Displays connected MVentX devices with status classification.
- **Safely Eject Button (`btn_eject`)**:
  - Dynamically enabled when an active device is selected.
  - Prompts with a confirmation modal detailing the device name.
  - Invokes `DeviceService.eject_active_device()`.
  - Reports safe ejection outcome in status bar and notifications.
- **Disconnection Alerting**: Automatically handles device removal, clears target references, and updates UI status cleanly without freezing or crashing.

---

## 5. Automated Test Suite Results

The automated regression test suite expanded from 176 to **207 passing tests** across unit and integration categories:

| Test Module | Category | Tests | Status |
|:---|:---:|:---:|:---:|
| `test_device_state.py` | Unit (Host) | 4 | Pass |
| `test_device_identity.py` | Unit (Host) | 7 | Pass |
| `test_device_classification.py` | Unit (Host) | 6 | Pass |
| `test_filesystem_access.py` | Unit (Host) | 7 | Pass |
| `test_host_platform.py` | Unit (Host) | 4 | Pass |
| `test_device_monitor.py` | Unit (Host) | 2 | Pass |
| `test_installation_service.py` | Unit (Installer) | 7 | Pass |
| `test_gui_installer.py` | Unit (Installer GUI) | 4 | Pass |
| `test_gui_application.py` | Unit (Manager GUI) | 10 | Pass |
| `test_dual_partition_storage.py` | Unit (Storage) | 7 | Pass |
| `test_filesystem_validation.py` | Unit (Storage) | 18 | Pass |
| `test_menu_generator.py` | Unit (Boot Config) | 9 | Pass |
| `test_iso_validation.py` | Unit (ISO Manager) | 7 | Pass |
| `test_linux_boot_recipes.py` | Unit (Boot Recipes) | 11 | Pass |
| Other Unit Tests | Unit | 62 | Pass |
| **Total Unit Tests** | | **165** | **Pass (100%)** |
| `test_host_integration_e2e.py` | Integration (Host E2E) | 1 | Pass |
| `test_usb_installation_e2e.py` | Integration (QEMU Boot) | 1 | Pass |
| `test_gui_grub_pipeline.py` | Integration (GUI-GRUB) | 12 | Pass |
| `test_qemu_boot_harness.py` | Integration (QEMU Boot) | 2 | Pass |
| `test_linux_loopback_boot.py` | Integration (QEMU Boot) | 1 | Pass |
| `test_multidistro_loopback.py` | Integration (QEMU Boot) | 2 | Pass |
| `test_iso_storage_integrity.py` | Integration (Storage) | 6 | Pass |
| `test_backup_recovery.py` | Integration (Storage) | 5 | Pass |
| `test_config_persistence.py` | Integration (Storage) | 4 | Pass |
| `test_storage_failures.py` | Integration (Storage) | 5 | Pass |
| `test_menu_integration.py` | Integration (Boot Config) | 3 | Pass |
| **Total Integration Tests** | | **42** | **Pass (100%)** |
| **Grand Total** | | **207** | **Pass (100%)** |

All 207 tests pass with zero regressions.

---

## 6. Verified Capabilities vs. Scope Constraints

### Verified Capabilities
- Windows 10/11 block device enumeration and partition discovery via PowerShell and Win32 APIs.
- Linux block device enumeration via `lsblk` JSON and sysfs.
- Multi-attribute classification across all five operational statuses.
- Full end-to-end management lifecycle (register, rename, toggle, reorder, remove) against actual filesystem mounts.
- Pre-write device identity revalidation blocking mismatched or disconnected devices.
- Safe device ejection dispatcher.

### Scope Constraints
- Physical device repartitioning remains locked (`allow_physical_formatting = False`) until dedicated hardware verification milestones.
- Testing in automated CI utilizes virtual disk image filesystems and verified host directory mounts.

