# Milestone 17.1: Windows Physical USB Installer Reliability & Recovery Fix

## 1. Executive Summary

Milestone 17.1 addresses and resolves root causes identified during physical USB installation attempts on Windows 11 with Windows PowerShell 5.1 and target media (SanDisk Cruzer Blade 64GB USB, `\\.\PHYSICALDRIVE1`).

Prior physical installation attempts failed due to three subtle environment-specific issues:
1. **Empty Disk `Get-Partition` Exit Code 1**: When querying a cleared disk (`Clear-Disk`), `Get-Partition -DiskNumber 1` threw a non-terminating `CmdletizationQuery_NotFound_DiskNumber` error. Even with `-ErrorAction SilentlyContinue`, PowerShell sets `$? = $false`, causing the `powershell.exe` process to exit with code 1 and empty standard error, prematurely halting the installer during pre-write revalidation.
2. **PowerShell Variable Colon Reference Syntax Error**: The drive-letter string interpolation `"$bootLetter:"` caused a PowerShell parser error (`"Variable reference is not valid. ':' was not followed by a valid variable name character. Consider using ${} to delimit the name."`), failing at volume label verification.
3. **Redundant `Initialize-Disk` on Previously Cleared Disk**: `Clear-Disk` wipes partition entries but leaves the disk formatted as GPT. When retrying installation, calling `Initialize-Disk` threw `"Initialize-Disk : The disk has already been initialized."`

Milestone 17.1 implements a resilient, state-aware recovery architecture that eliminates these failure modes, validates PowerShell syntax before execution, classifies the storage media into one of 7 distinct installation states, and provides complete diagnostics.

---

## 2. Root Cause Analysis & Technical Solutions

### Issue 1: `Get-Partition` On Empty Disk Exit Code 1
- **Root Cause**: PowerShell 5.1's CIM-backed storage cmdlet `Get-Partition` raises an `ObjectNotFound` / `CmdletizationQuery_NotFound_DiskNumber` record when a disk has zero partitions. Because PowerShell treats unhandled non-terminating errors as setting `$? = $false`, the process exits with return code 1.
- **Solution**:
  - Encapsulated all `Get-Partition` queries in `try/catch` blocks targeting `CmdletizationQuery_NotFound` and `ObjectNotFound` explicitly.
  - Returns `[]` with `exit 0` when zero partitions exist.
  - Implemented `WindowsPhysicalInstaller.parse_partition_json()` to safely normalize empty strings, `"null"`, single dictionary objects, and JSON arrays.

### Issue 2: String Interpolation Syntax `$bootLetter:`
- **Root Cause**: In PowerShell, `$bootLetter:` is interpreted as a scope prefix (like `$env:` or `$global:`), triggering a parser failure.
- **Solution**:
  - Replaced all drive-letter interpolations with `${bootLetter}:` and `${dataLetter}:`.
  - Added `WindowsPhysicalInstaller.validate_powershell_syntax()` using `[System.Management.Automation.Language.Parser]::ParseInput` and regex pre-checks to reject any illegal drive-colon interpolations prior to execution.

### Issue 3: Duplicate `Initialize-Disk` & Clear-Disk Handling
- **Root Cause**: If a USB drive is already initialized as GPT with 0 partitions (the exact state left after an earlier `Clear-Disk`), attempting `Initialize-Disk` fails with code 1.
- **Solution**:
  - Added a state-aware classification mechanism before destructive operations.
  - When the target is in the `EMPTY_GPT` state, the installer skips redundant `Clear-Disk` and `Initialize-Disk` calls and directly executes `EmptyGptAllocate`, proceeding straight to partition creation and formatting.

---

## 3. Seven-State USB Installation & Recovery Classification Model

The `InstallationRecoveryService.diagnose_installation_state()` engine classifies any USB storage device into one of seven canonical states:

| State | Name | Condition | Recovery Action |
|---|---|---|---|
| 1 | `ORIGINAL_FILESYSTEM` | MBR or non-GPT partition scheme | Full wipe (`FullWipe`) with two-phase confirmation |
| 2 | `EMPTY_GPT` | GPT partition style with 0 partitions | Skip `Clear-Disk` and `Initialize-Disk`, allocate partitions directly |
| 3 | `GPT_ESP_ONLY` | GPT partition style with only 1 partition | Allocate Partition 2 or repartition |
| 4 | `GPT_UNFORMATTED` | 2 partitions exist, missing FAT32/exFAT | Format partitions directly without re-partitioning |
| 5 | `GPT_FORMATTED_NO_GRUB` | Both partitions formatted, no bootloader | Deploy GRUB 2.12 EFI binaries and configuration |
| 6 | `PARTIAL_GRUB` | BOOTX64.EFI exists, missing modules | Redeploy GRUB runtime modules and fonts |
| 7 | `COMPLETE_MVENTX` | All 12 installation milestones verified | Device ready for multiboot ISO registration |

---

## 4. Pre-Execution PowerShell Syntax & AST Validator

All generated PowerShell scripts pass through `WindowsPhysicalInstaller.validate_powershell_syntax(script)`:
1. **Regex Trap Scanner**: Scans for `$([a-zA-Z_][a-zA-Z0-9_]*):(?![a-zA-Z_])` to catch unescaped variable colons.
2. **PowerShell AST Parser**: On Windows hosts, executes `[System.Management.Automation.Language.Parser]::ParseInput` on Base64-encoded script content to guarantee syntax validity before running.

---

## 5. Partition Layout & Sizing (256 MiB ESP Standard)

Physical USB storage uses an expanded 256 MiB ESP partition size (`DEFAULT_PHYSICAL_ESP_SIZE_MB = 256`) to ensure ample room for GRUB 2.12 x86_64-efi runtime modules, localized fonts, diagnostic tools, and future kernel images:

- **Partition 1 (`MVX_BOOT`)**:
  - Size: 256 MiB (268,435,456 bytes)
  - Filesystem: FAT32
  - Label: `MVX_BOOT`
  - Partition Type GUID: `C12A7328-F81F-11D2-BA4B-00A0C93EC93B` (EFI System Partition)
  - Alignment: 1 MiB boundary (`Offset % 1048576 == 0`)
- **Partition 2 (`MVX_DATA`)**:
  - Size: Remaining capacity (~58.48 GiB on 64 GB media)
  - Filesystem: exFAT
  - Label: `MVX_DATA`
  - Partition Type GUID: `EBD0A0A2-B9E5-4433-87C0-68B6B72699C7` (Microsoft Basic Data)
  - Alignment: 1 MiB boundary (`Offset % 1048576 == 0`)

---

## 6. Verification & Test Suite Summary

- **Unit Test Suite**: 40 tests passed (`tests/unit/test_windows_physical_installer.py`).
- **Integration Test Suite**: 9 tests passed (`tests/integration/test_windows_physical_pipeline_virtual.py`).
- **Safety Guarantee**: Zero destructive physical disk operations occurred during all automated test runs.

