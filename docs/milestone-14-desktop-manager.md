# MVentX — Milestone 14: Desktop Manager & Device Management Maturity Report

## 1. Executive Summary

Milestone 14 elevates the MVentX PySide6 desktop management application from an initial multi-screen prototype into a responsive, production-quality utility for day-to-day multiboot USB drive management.

The focus of this milestone is **user responsiveness, deep visibility, and proactive device handling**:
- All intensive disk operations (large ISO file copying, SHA-256 hashing, GRUB compilation) run asynchronously on background `QThread` workers. The GUI event loop is never blocked.
- The Dashboard displays comprehensive device metrics: capacity, used vs. free space, registered vs. found ISO files, partition mount points, filesystem types, read-only vs. read-write access permissions, and dynamic validation badges (`VALID MVX`, `CORRUPTED`, `INCOMPLETE`, `STANDARD USB`).
- Direct action shortcuts allow users to immediately open the target drive in File Explorer, copy ISO images with live progress dialogs, or safely eject the device.
- Hotplug event monitoring automatically updates connected device lists and cleanly handles safe ejection and unexpected disconnections.
- The Environment Library features multi-column sorting, instant search filtering, batch operations (enable/disable/remove multiple entries), on-demand checksum inspection, and drag-and-drop ISO importing.

The automated test suite expanded to **264 passing tests** (218 unit tests + 46 integration tests, 0 failures, 0 regressions).

---

## 2. Dashboard & Device Metrics

The Dashboard ([`manager/ui/dashboard.py`](file:///C:/MVentX/manager/ui/dashboard.py)) provides complete visibility into the storage state:

```
+---------------------------------------------------------------------------------------------------+
| TARGET: MVentX USB (SanDisk Ultra 32GB)     [ VALID MVX ]                                         |
| Data Mount: E:\ | Boot Mount: F:\                                                                 |
| [ Inspect Storage ]   [ + Add Environment ]                                                       |
| ------------------------------------------------------------------------------------------------- |
| SHORTCUTS: [ 📁 Open in File Explorer ]   [ 📥 Copy ISO to USB... ]   [ ⏏ Safely Eject USB ]       |
+---------------------------------------------------------------------------------------------------+
|  REGISTERED ENVS      REGISTERED ISO SIZE       TOTAL CAPACITY            MISSING ISOS            |
|        3                    4.21 GiB               28.64 GiB                   0                  |
|  2 active • 5 found   Across 3 image files     Free: 24.43 GiB          All files verified        |
+---------------------------------------------------------------------------------------------------+
|  STORAGE UTILIZATION (MVX_DATA):  4.21 GiB used of 28.64 GiB (14%)                                |
|  [============================>-----------------------------------------------------------------] |
+---------------------------------------------------------------------------------------------------+
|  PARTITION ARCHITECTURE STATUS                                                                    |
|  Partition 1: MVX_BOOT (FAT32 ESP)       Mounted at F:\ (FAT32, Read-Write) • GRUB Synchronized   |
|  Partition 2: MVX_DATA (exFAT Basic)     Mounted at E:\ (exFAT, Read-Write) • Structure Valid     |
+---------------------------------------------------------------------------------------------------+
```

### 2.1 Dynamic Validation Badges
The dashboard evaluates the target against the `MVentXClassification` model ([`core/host/device_classification.py`](file:///C:/MVentX/core/host/device_classification.py)):
- `VALID MVX`: Both partitions present, valid GUIDs, synchronized configuration, intact skeleton.
- `CORRUPTED`: Partition corruption, broken JSON schema, or missing essential directory structure.
- `INCOMPLETE`: Missing EFI system partition, missing GRUB binary, or unpartitioned device.
- `STANDARD USB`: Formatted removable storage lacking MVentX signatures.
- `DISCONNECTED`: No target currently selected.

### 2.2 Deep Partition Health & Permission Checking
Rather than merely checking directory existence, the dashboard verifies:
- Filesystem types (FAT32 for `MVX_BOOT`, exFAT for `MVX_DATA`).
- Live Read-Write vs. Read-Only access permissions (`os.access(..., os.W_OK)`). If a USB drive is hardware write-protected or mounted read-only due to filesystem dirty flags, the dashboard immediately alerts the user.

---

## 3. Asynchronous Worker Architecture & Responsiveness

To eliminate GUI freezing during long disk operations, Milestone 14 implements dedicated cancellable `QThread` workers in [`manager/services/workers.py`](file:///C:/MVentX/manager/services/workers.py):

| Worker Class | Responsibility | Signals Emitted | Cancellation Behavior |
| :--- | :--- | :--- | :--- |
| **`ISOCopyWorker`** | Chunked (1 MiB) copying from source to `MVX_DATA/ISO/`. Pre-validates disk space with margin. | `progress(copied, total, pct, speed_mbps)`<br>`finished_success(dest_path)`<br>`failed(err)`<br>`cancelled()` | Atomic `_is_cancelled` flag. Closes file handles, deletes partial destination file, emits `cancelled()`. |
| **`ChecksumWorker`** | Asynchronous SHA-256 calculation for large ISO images without UI stutter. | `progress(read, total, pct)`<br>`finished_success(hex_hash)`<br>`failed(err)`<br>`cancelled()` | Halts hashing loop immediately upon request. |
| **`SyncBootConfigWorker`** | Off-thread compilation and synchronization of GRUB boot menu to `MVX_BOOT`. | `finished_success(msg)`<br>`failed(err)` | Executes atomically with rollback protection. |

### 3.1 Reusable Progress Dialog
All background operations integrate with [`manager/ui/progress_dialog.py`](file:///C:/MVentX/manager/ui/progress_dialog.py), providing:
- Real-time percentage progress bar.
- Continuous throughput calculation (e.g. `45.2 MiB/s`).
- One-click "Cancel" button that terminates worker execution and cleans up partial disk files.

---

## 4. Hotplug Monitoring & Safe Ejection

The Desktop Manager coordinates with the host integration layer ([`manager/services/device_service.py`](file:///C:/MVentX/manager/services/device_service.py)):

### 4.1 Live Hotplug Event Handling
- `DeviceMonitor` monitors device insertion and removal.
- On device arrival (`device_connected` signal): The target combo box automatically re-scans the storage bus and updates available targets without requiring the user to click "Scan USBs".
- On device departure (`device_disconnected` signal): If the removed device was the active target, the application immediately transitions to the `DISCONNECTED` state, displays an informative status warning, and resets UI controls without crashing.

### 4.2 Safe Ejection Workflow
- Users trigger safe ejection via the header bar or dashboard shortcut button.
- Flushes OS filesystem buffers (`FlushFileBuffers` on Windows, `os.sync` on POSIX).
- Unmounts volume mount points.
- Dispatches OS safe removal IOCTLs.
- Notifies the user with a modal confirmation: *"Safe to remove hardware"*.

---

## 5. Environment Library Usability Enhancements

The Environment Library ([`manager/ui/environment_library.py`](file:///C:/MVentX/manager/ui/environment_library.py)) incorporates major usability upgrades:

### 5.1 Multi-Criteria Sorting & Live Filtering
- **Sorting Options**:
  - `Default (Boot Order)`: Displays entries in exact boot menu sequence.
  - `Name (A → Z)` and `Name (Z → A)`: Alphabetical sorting.
  - `Size (Largest First)` and `Size (Smallest First)`: File size sorting.
  - `Boot Method`: Groups distributions by loopback recipe.
  - `Enabled First`: Prioritizes active boot entries.
- **Search Filtering**: Real-time filtering matching display names, ISO filenames, and boot recipes.

### 5.2 Batch Operations
- Multi-row selection (`QTableWidget.ExtendedSelection`).
- Batch actions bar:
  - `✓ Enable Selected`: Enables multiple boot entries simultaneously.
  - `✕ Disable Selected`: Disables multiple boot entries simultaneously.
  - `🗑 Remove Selected`: Removes selected registrations with a single confirmation prompt while preserving physical ISO files.

### 5.3 Detailed Environment Inspector
The `EnvironmentDetailDialog` provides deep inspection of any registered entry:
- Unique Environment ID and Display Name.
- Storage paths (`iso_path`, `target_path`).
- File size and boot order index.
- Kernel and initramfs binary paths.
- Custom kernel command line arguments.
- **On-Demand SHA-256 Checksum Calculation**: Calculates and displays cryptographic hash in the background via `ChecksumWorker`.

### 5.4 Drag-and-Drop ISO Importing
Users can drag `.iso` or `.img` files directly from their operating system file manager and drop them onto the Environment Library window. MVentX prompts to copy the image to `MVX_DATA/ISO/` using `ISOCopyWorker` and automatically opens the Add Environment screen upon completion.

---

## 6. Verification and Test Results

Automated headless tests in [`tests/unit/test_desktop_manager_maturity.py`](file:///C:/MVentX/tests/unit/test_desktop_manager_maturity.py) and [`tests/unit/test_gui_application.py`](file:///C:/MVentX/tests/unit/test_gui_application.py) verified all Milestone 14 features:
- Dashboard metric calculation, free space display, and validation badge transitions.
- Read-Write permission flags on dual partitions.
- Asynchronous `ISOCopyWorker` file copying and speed reporting.
- Asynchronous cancellation and partial file deletion.
- Background `ChecksumWorker` and `SyncBootConfigWorker` execution.
- Multi-criteria library sorting and search filtering.
- Multi-row batch enable, disable, and removal operations.
- Hotplug arrival/departure signals and safe ejection state transitions.

```
Ran 218 unit tests + 46 integration tests: 264 total tests, 100% pass rate.
```

---

## 7. Milestone 14 Acceptance Checklist

| Item | Requirement | Status | Evidence |
| :--- | :--- | :--- | :--- |
| **1** | Dashboard & Device Metrics | **PASSED** | Capacity, used, free space, registered vs total ISOs, validation badges, and RW health implemented in [`manager/ui/dashboard.py`](file:///C:/MVentX/manager/ui/dashboard.py). |
| **2** | Background Threading & Responsiveness | **PASSED** | `ISOCopyWorker`, `ChecksumWorker`, `SyncBootConfigWorker`, and `OperationProgressDialog` implemented and tested with cancellation. |
| **3** | Hotplug & Safe Ejection Integration | **PASSED** | Live arrival/departure events and safe ejection workflow integrated in [`manager/services/device_service.py`](file:///C:/MVentX/manager/services/device_service.py) and [`manager/ui/main_window.py`](file:///C:/MVentX/manager/ui/main_window.py). |
| **4** | Environment Library Usability | **PASSED** | Sorting, live text filtering, batch operations, detailed inspector, and drag-and-drop implemented in [`manager/ui/environment_library.py`](file:///C:/MVentX/manager/ui/environment_library.py). |
| **5** | Zero Regressions in Test Suite | **PASSED** | 264 passing tests across unit and integration suites. |

