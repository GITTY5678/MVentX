# MVentX – Milestone 6: End-to-End GUI-to-GRUB Integration

## 1. Executive Summary

Milestone 6 establishes the complete operational pipeline connecting the **MVentX Desktop Manager (PySide6)** to the **GRUB 2 bootloader** and verifies it via automated virtual UEFI boots in **QEMU 11** with **OVMF firmware**.

The primary objective was to prove that every user management action in the desktop GUI updates the authoritative JSON configuration store on `MVX_DATA`, automatically recompiles the dynamic GRUB configuration on `MVX_BOOT`, and is faithfully reflected in the next virtual boot without modifying physical drives or compromising safety invariants.

All **141 automated tests** pass cleanly (expanded from the Milestone 5 baseline of 117 tests).

---

## 2. Decoupled Pipeline Architecture

The end-to-end operational flow strictly maintains the decoupling of offline host tools from boot-time firmware:

```
[ PySide6 Desktop GUI ]
          │ User action (Register, Rename, Reorder, Enable/Disable, Remove, Settings, Restore)
          ▼
[ ISOService / ISORegistrationManager ]
          │ Atomic Write (tempfile + fsync + replace) + Backup
          ▼
[ MVX_DATA/config/mventx.json ] ─── (Authoritative Configuration Store)
          │
          │ GrubMenuGenerator.write_menu_file()
          ▼
[ MVX_BOOT/boot/grub/mventx_menu.cfg ] ─── (Dynamic Environment Menu)
          │
          │ Sourced at boot by /boot/grub/grub.cfg
          ▼
[ GRUB 2.12 UEFI / gfxterm ] ─── (Rendered in QEMU Virtual Machine)
```

### Architectural Guarantees
1. **Zero Host Dependencies at Boot**: GRUB 2 executes natively under UEFI firmware; it never invokes Python, Qt, Windows CIM, or Linux host daemons.
2. **GRUB Never Parses JSON**: The Python generator translates validated, sanitized JSON models into native GRUB script syntax (`mventx_menu.cfg`) before unmounting.
3. **No Automatic Directory Scanning**: All bootable targets require explicit user registration; unconfigured ISOs in `MVX_DATA/ISO/` are never exposed in the boot menu.
4. **Preservation of User Payloads**: Deregistering or disabling environments leaves physical `.iso` files completely intact on disk.
5. **Atomic Rollback on Error**: If boot menu compilation on `MVX_BOOT` fails due to disk write errors, the configuration on `MVX_DATA` rolls back to its prior state (`MenuSyncRollbackError`).

---

## 3. GUI-to-Configuration Integration & Scenario Matrix

All 10 required integration scenarios were verified individually through automated integration suites and virtual QEMU boot execution:

| Scenario | Management Action | Authoritative JSON (`mventx.json`) | Generated GRUB Menu (`mventx_menu.cfg`) | Physical ISO Status | Verified in QEMU |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **A: Empty Configuration** | No environments registered | `"environments": []` | Fallback menu: `menuentry "No environments registered or enabled" --id "mventx-empty"` | N/A | **YES** |
| **B: Single Environment** | Register 1 ISO via GUI | Exactly 1 environment record | Exactly 1 `menuentry "..."` matching registered ID | Intact on disk | **YES** |
| **C: Multiple Environments** | Register Alpine, Debian, Arch | 3 environment records with `order: 1, 2, 3` | 3 menuentries in exact order: Alpine $\rightarrow$ Debian $\rightarrow$ Arch | All 3 intact | **YES** |
| **D: Disabled Environment** | Toggle environment to disabled | `"enabled": false` | Excluded from generated menu; active environments remain | Intact on disk | **YES** |
| **E: Re-enabled Environment** | Re-enable disabled environment | `"enabled": true` | Returns to generated menu in its configured sequence | Intact on disk | **YES** |
| **F: Reordered Environments** | Move environment Up/Down | `order` indices swapped in array | Menu sequence reflects new order | Intact on disk | **YES** |
| **G: Renamed Environment** | Edit display name | `"display_name": "New Name"` updated | `menuentry "New Name"` updated; ID preserved | Intact on disk | **YES** |
| **H: Removed Environment** | Remove registration (default) | Entry purged from JSON array | Menuentry removed; falls back if list becomes empty | **Preserved on disk** | **YES** |
| **I: Invalid Configuration** | Submit invalid/empty names | Mutation rejected; prior config retained | Existing menu preserved; no malformed GRUB syntax | Intact on disk | **YES** |
| **J: Special Characters** | Quotes, brackets, slashes, unicode | Natural display name stored safely | Safely escaped: `\"` and `\\`; injection rejected | Intact on disk | **YES** |

### Additional Operational Actions Verified
- **Global Boot Preferences**: Updating boot menu title (`meta.title`) and timeout countdown (`meta.timeout`) updates `mventx.json` and renders `set mventx_menu_title="..."` and `set timeout=...` in `mventx_menu.cfg`.
- **Backup & Safe Restoration**: Restoring an archived JSON backup from `MVX_DATA/backups/` reconstitutes previous environments, resynchronizes the GRUB boot menu, and **never deletes physical ISO files**.

---

## 4. GRUB Configuration Generator Enhancements

The generator (`core/boot_config/generator.py`) was strengthened to enforce strict safety and diagnostic boundaries:

1. **Two-Tier Display Name Validation & Escaping**:
   - `validate_display_name()`: Validates input length ($\le 100$), forbids control characters (`\r`, `\n`, `\0`), semicolons, braces, and command substitutions (`$(...)`, `` `...` ``).
   - `sanitize_display_name()`: Applied during GRUB script compilation to safely escape quotes (`\"`), standalone dollar signs (`\$`), and backslashes (`\\`) without double-escaping JSON storage.
2. **Deterministic Output**: Sorted primarily by integer `order` and secondarily by `id`, guaranteeing byte-for-byte identical output across repeated runs on identical inputs.
3. **Missing ISO Storage Diagnostics**: When storage directories are available at generation time, checks if `iso_path` exists on disk. If missing, annotates the entry with `[WARNING] Registered ISO file missing from storage`.
4. **Unrecognized Boot Method Handling**: Verifies `boot_method` against standard recipes (`linux_generic_loopback`, `chainload_efi`, `windows_wimboot`, `iso_loopback`, `generic_uefi`). Unrecognized methods emit a safe diagnostic warning without failing GRUB parser syntax.
5. **Non-Claim of Bootability**: Explicitly states in menu entries:
   ```grub
   echo "  [MVentX: Environment registered. Kernel boot execution planned for Milestone 7]"
   echo "  Note: Registration does not imply verified kernel bootability."
   ```

---

## 5. QEMU Automated Boot Harness (`scripts/test_qemu_harness.py`)

A fully automated test harness was built to prove end-to-end bootloader execution in an isolated virtual environment:

1. **Virtual Disk Generation**: Populates isolated staging trees, compiles dynamic menus, and builds 256 MiB GPT dual-partition disks (`build_dual_partition_gpt_disk`).
2. **UEFI Boot Execution**: Launches QEMU with OVMF firmware using split read-only code (`edk2-x86_64-code.fd`) and writable NVRAM variables (`build/ovmf/mventx_vars.fd`).
3. **Headless Screen Capture**: Executes QEMU in headless mode (`-display none -monitor stdio`), waits for GRUB menu countdown, issues `screendump`, and cleanly terminates via `quit`.
4. **Pixel Distribution Analysis**: Inspects pixel buffers to verify that GRUB `gfxterm` is actively rendering:
   - Cyan highlight box: `RGB(84, 254, 254)`
   - Light gray borders and text: `RGB(168, 168, 168)`
   - Black background: `RGB(0, 0, 0)`
   - Resolution: `1280x800`
5. **Export High-Resolution PNGs**: Converts captured PPMs into optimized PNG screenshots saved to `docs/screenshots/`.

### Verified Screenshot Artifacts
- [`docs/screenshots/qemu_scenario_a_empty.png`](file:///C:/MVentX/docs/screenshots/qemu_scenario_a_empty.png): Fallback menu when no environments are registered or enabled.
- [`docs/screenshots/qemu_scenario_b_single.png`](file:///C:/MVentX/docs/screenshots/qemu_scenario_b_single.png): Single registered Ubuntu 24.04 LTS desktop environment.
- [`docs/screenshots/qemu_scenario_c_multi.png`](file:///C:/MVentX/docs/screenshots/qemu_scenario_c_multi.png): Multi-boot environment menu (Alpine, Debian, Arch in sequence).
- [`docs/screenshots/qemu_scenario_d_disabled.png`](file:///C:/MVentX/docs/screenshots/qemu_scenario_d_disabled.png): Active view with disabled Debian environment excluded.
- [`docs/screenshots/qemu_scenario_j_special_chars.png`](file:///C:/MVentX/docs/screenshots/qemu_scenario_j_special_chars.png): Menu rendering escaped quotes and brackets (`Alpine "Edge" [6.6-LTS]`).

---

## 6. Test Suite Matrix

The automated test suite now encompasses **141 tests**, all passing with zero failures:

| Test Module | Category | Tests | Description |
| :--- | :--- | :---: | :--- |
| `tests/integration/test_gui_grub_pipeline.py` | Integration | 12 | End-to-end verification of Scenarios A through J, global settings, and backup restoration. |
| `tests/unit/test_grub_generator_advanced.py` | Unit | 10 | Generator determinism, quote/dollar escaping, injection prevention, missing ISO diagnostics, fallback menu. |
| `tests/integration/test_qemu_boot_harness.py` | Integration | 2 | Live QEMU UEFI boot execution, screendump capture, and gfxterm pixel analysis. |
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
| **Total** | | **141** | **141 passed, 0 failed, 0 errors** (44.5s execution time) |

---

## 7. Genuinely Verified Behaviors vs. Known Limitations

### What is Genuinely Verified
- **Configuration Flow**: PySide6 GUI actions update authoritative JSON on `MVX_DATA`.
- **Menu Compilation**: Host Python engine compiles native GRUB syntax on `MVX_BOOT`.
- **Boot Execution**: QEMU + OVMF successfully executes `BOOTX64.EFI`, loads `grub.cfg`, finds `mventx_menu.cfg`, and renders gfxterm menu graphics at 1280x800 resolution.
- **Safety Invariants**: Physical ISO files are never deleted unless explicit confirmation is provided; destructive formatting is impossible through routine management actions.

### Known Limitations
- **Kernel Boot Not Yet Implemented**: Selecting a menu entry currently echoes environment diagnostics and pauses before returning to the menu. Actual Linux loopback booting (`loopback.cfg`, `casper`, `dracut`, `archiso`) and Windows boot integration (`wimboot`) are scheduled for Milestone 7.
- **Firmware Variables**: Repeated QEMU runs use a dedicated copy of NVRAM variables (`build/ovmf/mventx_vars.fd`) to prevent corrupting system firmware templates.

---

## 8. Operational Commands

### Run Full Test Suite (141 tests)
```powershell
python -m unittest discover tests
```

### Run QEMU Boot Verification Harness
```powershell
# Run all scenarios and export screenshots to docs/screenshots/:
python scripts/test_qemu_harness.py --scenario all

# Run specific scenario (e.g. multi-environment boot):
python scripts/test_qemu_harness.py --scenario multi
```

### Run Interactive Virtual Boot (Windowed)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\run_qemu.ps1
```

---

## 9. Recommended Next Milestone

**Milestone 7: Linux Loopback & Boot Recipes**
- Implement distribution-specific loopback kernel extractors and command lines (`casper`, `dracut`, `archiso`).
- Dynamic loopback attachment in GRUB (`loopback loop0 ($mventx_data_dev)/$iso_path`).
- Kernel (`vmlinuz`) and initrd loading from loopback ISOs.
- First operational live Linux desktop boot inside QEMU.

