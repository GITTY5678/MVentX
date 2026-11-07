# MVentX — Milestone 15: Recovery, Diagnostics & Failure Handling Report

## 1. Executive Summary

Milestone 15 hardens MVentX against storage faults, configuration corruption, sudden power failure, interrupted writes, and malformed files.

The core objective is **uncompromising resilience**:
- All configuration modifications use atomic two-stage write protocols (`.tmp` staging + `fsync` + atomic filesystem replace).
- Before any destructive or updating operation, a timestamped backup is recorded in `MVX_DATA/backups/` with automated rotation (retaining 10 backups by default).
- If `mventx.json` is truncated mid-write (simulating unexpected power loss or USB disconnection), the [`ConfigRepairer`](file:///C:/MVentX/core/config/repair.py) automatically identifies the corruption, scans `backups/`, and restores the latest intact configuration.
- If individual environment entries in `mventx.json` are malformed, `ConfigRepairer` salvages all valid environments, discards unrecoverable records, re-numbers boot order sequentially, and persists the repaired configuration.
- Under catastrophic failure where no valid backup exists, the system reinitializes a valid default configuration while strictly guaranteeing that files in `MVX_DATA/ISO/` are never touched or deleted.
- A multi-attribute [`DiagnosticReportGenerator`](file:///C:/MVentX/core/diagnostics/diagnostic_report.py) compiles deep technical snapshots (host OS, privileges, partition RW status, directory integrity, schema compliance, missing ISO files, bootloader binaries) exportable as JSON or formatted Markdown via CLI (`scripts/generate_diagnostic_report.py`) and GUI Settings.

All **271 automated tests** (225 unit tests + 46 integration tests) are passing with 100% pass rate.

---

## 2. Configuration Backup & Rotation Subsystem

All persistent state changes in MVentX are governed by [`core/storage/storage_manager.py`](file:///C:/MVentX/core/storage/storage_manager.py):

### 2.1 Atomic Write Protocol
1. Configuration JSON is serialized to a hidden temporary file `.mventx.json.<pid>.tmp` in `MVX_DATA/config/`.
2. `f.flush()` and `os.fsync(f.fileno())` force dirty cache lines from host RAM directly to flash media.
3. An atomic filesystem replace (`os.replace`) swaps the temporary file into `config/mventx.json`. This guarantees that an abrupt disconnection during writing leaves either the old intact file or the new intact file, never a corrupted hybrid.

### 2.2 Backup Rotation
- Before overwriting `config/mventx.json`, a timestamped backup (`mventx_YYYYMMDD_HHMMSS.json`) is stored in `MVX_DATA/backups/`.
- Sub-second collision counters prevent timestamp overwrite conflicts.
- Rotation prunes the oldest backups when count exceeds `max_retained_backups` (default: 10).

---

## 3. Configuration Repair and Salvage Engine

The repair engine ([`core/config/repair.py`](file:///C:/MVentX/core/config/repair.py)) implements an automated recovery cascade:

```
                         [ Read config/mventx.json ]
                                     |
                         Is file missing or empty?
                         /                       \
                      YES                         NO
                      /                             \
        [ Scan backups/ for valid JSON ]      [ Try json.loads() ]
           /                        \            /              \
        FOUND                    NOT FOUND    SUCCESS          PARSE ERROR
        /                            \          |                   |
[ Restore latest intact ]      [ Write clean ]  |       [ Scan backups/ for valid JSON ]
                               [ default cfg ]  |          /                        \
                                                |       FOUND                    NOT FOUND
                                                |       /                            \
                               [ Validate Schema ] [ Restore latest intact ]  [ Write clean ]
                                   /           \                              [ default cfg ]
                                VALID        INVALID
                                  |             |
                               [ OK ]    [ Salvage valid entries ]
                                         [ Discard broken items  ]
                                         [ Re-number boot order  ]
                                         [ Save repaired JSON    ]
```

### 3.1 Partial Environment Salvage
When `mventx.json` contains valid environments mixed with corrupted items (e.g. malformed JSON objects, missing required identifiers, or broken path strings), `ConfigRepairer.repair_structure`:
- Retains intact top-level properties (`version`, `meta.title`, `meta.timeout`).
- Filters the `environments` array, salvaging any entry possessing valid ISO/target paths and display names.
- Drops unrecoverable broken items.
- Re-indexes the `order` property sequentially starting from `1`.
- Atomically saves the repaired configuration and synchronizes the GRUB boot menu.

### 3.2 Data Preservation Guarantee
Under all failure, corruption, and recovery conditions, **files in `MVX_DATA/ISO/` are immutable**. MVentX never deletes, modifies, or truncates ISO images during configuration recovery or reset operations.

---

## 4. Multi-Attribute Diagnostic Report Generator

The diagnostic engine ([`core/diagnostics/diagnostic_report.py`](file:///C:/MVentX/core/diagnostics/diagnostic_report.py)) provides complete troubleshooting instrumentation:

### 4.1 Collected Diagnostic Categories
1. **Host Environment**: Platform (`win32`, `linux`), OS release and build, CPU architecture, Python runtime version, administrative / root execution status (`is_admin`).
2. **Target Device**: Device ID, bus type (USB, Virtual, NVMe), capacity in bytes and formatted units, removable flag, and MVentX classification status (`VALID_MVENTX`, `CORRUPTED`, `INCOMPLETE`, etc.).
3. **Partition Health & Access Permissions**: Mount points for `MVX_BOOT` and `MVX_DATA`, live Read-Write vs. Read-Only access verification (`os.access(..., os.W_OK)`), and directory skeleton integrity (`ISO/`, `config/`, `metadata/`, `backups/`).
4. **Configuration Health**: Schema compliance check via `ConfigValidator`, configuration version, menu title, countdown timeout, and detailed schema validation errors.
5. **Environment Library Inventory**: Complete list of registered environments, cross-referenced against files physically present in `MVX_DATA/ISO/`. Flags missing ISO files, file sizes, and SHA-256 integrity hashes.
6. **Backups Archive Status**: Total backup count, intact backup count, corrupted backup count, and newest backup timestamp.
7. **Bootloader Binaries on ESP**: Verifies presence of `/EFI/BOOT/BOOTX64.EFI`, `/boot/grub/grub.cfg`, and `/boot/grub/mventx_menu.cfg`.
8. **Actionable Recommendations**: Automatically highlights detected problems (e.g. missing files, read-only mounts, broken schema) with specific remediation steps.

### 4.2 Diagnostic Access Points
- **CLI Utility**:
  ```bash
  python scripts/generate_diagnostic_report.py --data E:\ --boot F:\ --format markdown -o report.md
  python scripts/generate_diagnostic_report.py --data E:\ --format json -o report.json
  ```
- **Desktop Manager GUI**: Under the **Settings** screen, the "Troubleshooting & System Diagnostics" card provides one-click export buttons (`Export Diagnostic Report (.md)...` and `Export Diagnostic Report (.json)...`).

---

## 5. Fault-Injection Test Results

A dedicated fault-injection test suite ([`tests/unit/test_recovery_diagnostics.py`](file:///C:/MVentX/tests/unit/test_recovery_diagnostics.py)) verified all failure modes:

| Test Case | Simulated Failure Scenario | Expected Behavior | Result |
| :--- | :--- | :--- | :--- |
| `test_truncated_json_recovers_from_latest_backup` | Power-loss truncates `mventx.json` mid-stream | Syntax error caught; latest valid backup restored automatically | **PASSED** |
| `test_truncated_json_with_no_backups_creates_clean_default` | Truncated JSON with empty `backups/` directory | Clean default schema initialized; ISO files preserved | **PASSED** |
| `test_missing_config_restores_from_backup_or_initializes` | `mventx.json` deleted out-of-band | Discovered missing file; restored from intact backup | **PASSED** |
| `test_salvage_corrupted_environments_array` | Broken array elements mixed with valid records | Valid records salvaged; corrupt items dropped; order re-sequenced | **PASSED** |
| `test_skips_corrupted_backup_files` | Newest backup corrupted; older backup valid | Corrupt backup skipped; older intact backup selected | **PASSED** |
| `test_diagnostic_report_generation` | Missing ISO files on disk and read-write target | Comprehensive JSON & Markdown generated; issues flagged | **PASSED** |
| `test_settings_screen_diagnostic_export` | User triggers report export from Desktop GUI | Report generated and written to target file cleanly | **PASSED** |

```
Ran 225 unit tests + 46 integration tests: 271 total tests, 100% pass rate.
```

---

## 6. Milestone 15 Acceptance Checklist

| Item | Requirement | Status | Evidence |
| :--- | :--- | :--- | :--- |
| **1** | Configuration Backup & Rotation | **PASSED** | Atomic temporary write, timestamped backups in `backups/`, and 10-backup rotation in [`core/storage/storage_manager.py`](file:///C:/MVentX/core/storage/storage_manager.py). |
| **2** | Schema Validation & Automatic Repair | **PASSED** | `ConfigRepairer` in [`core/config/repair.py`](file:///C:/MVentX/core/config/repair.py) repairs schema, salvages environments, and auto-recovers from backups. |
| **3** | Multi-Attribute Diagnostic Report | **PASSED** | Implemented in [`core/diagnostics/diagnostic_report.py`](file:///C:/MVentX/core/diagnostics/diagnostic_report.py), CLI script [`scripts/generate_diagnostic_report.py`](file:///C:/MVentX/scripts/generate_diagnostic_report.py), and Settings GUI. |
| **4** | Fault-Injection & Recovery Testing | **PASSED** | Verified in [`tests/unit/test_recovery_diagnostics.py`](file:///C:/MVentX/tests/unit/test_recovery_diagnostics.py). |
| **5** | Zero Regressions in Test Suite | **PASSED** | 271 passing tests (225 unit + 46 integration). |

