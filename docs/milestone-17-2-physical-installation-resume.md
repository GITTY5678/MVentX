# Milestone 17.2: Resume Physical Installation After Successful Partitioning

## 1. Executive Summary

During physical USB installation on the target media (SanDisk Cruzer Blade 64GB, serial `03026216101525041338`), low-level partition allocation and filesystem formatting succeeded completely:
- **Partition 1 (`MVX_BOOT`)**: 256 MiB FAT32 partition with EFI System Partition GUID (`C12A7328-F81F-11D2-BA4B-00A0C93EC93B`), mounted as `E:\`.
- **Partition 2 (`MVX_DATA`)**: ~58.48 GiB exFAT partition with Microsoft Basic Data GUID (`EBD0A0A2-B9E5-4433-87C0-68B6B72699C7`), mounted as `F:\`.

However, the installation process stalled at approximately 58% during PowerShell output parsing before MVentX bootloader files could be deployed, leaving the media in a healthy, formatted, but unpopulated state (`GPT_FORMATTED_NO_GRUB`).

Milestone 17.2 resolves the PowerShell output parsing defect, implements dynamic target disk resolution by hardware serial number (never assuming Disk 1), provides strict partition layout revalidation, and establishes a safe resume mechanism that starts directly at Step 8 (GRUB deployment) without repartitioning or reformatting.

---

## 2. Root Cause Analysis & Technical Fixes

### 2.1 PowerShell Stdout Stream Pollution
- **Problem**: When PowerShell 5.1 runs cmdlets like `Format-Volume` and `Set-Partition`, it outputs `MSFT_Volume` and status objects to the pipeline (standard output). This table formatting precedes the final `ConvertTo-Json -Compress` string. When Python called `json.loads(raw_output)`, the parser encountered table headers before the JSON dictionary and raised `json.decoder.JSONDecodeError: Extra data`.
- **Fix**:
  1. **Pipeline Suppression**: In all PowerShell partition preparation scripts, cmdlets with pipeline output now pipe explicitly to `Out-Null` (`Format-Volume ... | Out-Null`, `Set-Partition ... | Out-Null`).
  2. **Robust JSON Extraction**: Implemented `WindowsPhysicalInstaller.extract_json_from_output()`. If direct JSON decoding fails, it scans the stream for the outermost `{ ... }` block and line-by-line single-line JSON records, cleanly ignoring any preceding banner text, table columns, or warnings.

### 2.2 Dynamic Disk Resolution by Hardware Serial
- **Problem**: Physical disk indices (`PhysicalDrive1`) can shift when external drives, virtual disks, or SD cards are attached or detached.
- **Fix**: Implemented `WindowsPhysicalInstaller.resolve_disk_number_by_serial(serial_number)`. It dynamically queries all attached disks via `Get-Disk`, normalizes the hardware serial numbers, and returns the live physical disk index.

### 2.3 Strict Partition Layout Revalidation
- **Problem**: Resuming deployment on pre-formatted media requires absolute certainty that the existing partitions match MVentX requirements.
- **Fix**: Implemented `WindowsPhysicalInstaller.revalidate_formatted_partition_layout()`. It strictly verifies:
  - Disk is GPT and Online (not system, boot, or read-only).
  - Exactly 2 partitions exist.
  - Partition 1: GptType is ESP GUID (`C12A7328-F81F-11D2-BA4B-00A0C93EC93B`), FileSystem is FAT32, FileSystemLabel is `MVX_BOOT`.
  - Partition 2: GptType is Basic Data GUID (`EBD0A0A2-B9E5-4433-87C0-68B6B72699C7`), FileSystem is exFAT, FileSystemLabel is `MVX_DATA`.
  - Validates active drive letter bindings (`E:\` and `F:\`).
  - **Zero-Tolerance Abort**: If ANY attribute deviates from this state, it raises `WindowsPhysicalInstallationError` and aborts immediately without attempting repair or modifying the drive.

---

## 3. Resume Deployment Architecture (`GPT_FORMATTED_NO_GRUB`)

When an existing USB target is verified in the `GPT_FORMATTED_NO_GRUB` state, `WindowsPhysicalInstaller.resume_deployment_on_formatted_disk()` executes only the file deployment and verification stages:

```
[Preserved] Clear-Disk        --> SKIPPED
[Preserved] Initialize-Disk   --> SKIPPED
[Preserved] New-Partition     --> SKIPPED
[Preserved] Format-Volume     --> SKIPPED
Step 8: Deploy GRUB 2.12 EFI bootloader binaries, modules, and fonts to E:\
Step 9: Initialize MVentX directory skeleton (ISO/, config/, metadata/, backups/) on F:\
Step 10: Write initial authoritative mventx.json configuration (0 environments)
Step 11: Compile initial mventx_menu.cfg fallback menu on E:\
Step 12: Run complete structural and file verification with InstallationVerifier
```

---

## 4. Verification & Test Suite Summary

- **Unit Tests**: **47 / 47 tests passed** in `tests/unit/test_windows_physical_installer.py`.
  - Verified `extract_json_from_output` extracts JSON when preceded by `Format-Volume` tables.
  - Verified `resolve_disk_number_by_serial` resolves physical disk indices dynamically.
  - Verified `revalidate_formatted_partition_layout` succeeds on valid layouts and aborts on serial/label mismatches.
  - Verified `resume_deployment_on_formatted_disk` never invokes `Clear-Disk`, `Initialize-Disk`, `New-Partition`, or `Format-Volume`.
- **Virtual Disk Integration Suite**: **10 / 10 tests passed** in `tests/integration/test_windows_physical_pipeline_virtual.py`.
  - Verified `test_resume_deployment_on_gpt_formatted_no_grub_virtual` executes resume from `GPT_FORMATTED_NO_GRUB` without reformatting and transitions to `COMPLETE_MVENTX` with 100% check pass rate.
- **Dedicated CLI Resume Script**: `scripts/resume_physical_installation.py` implemented with default `--dry-run` inspection and `--execute` flag.

