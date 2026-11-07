# MVentX – Milestone 11: Windows Boot Compatibility Report

## Executive Summary

Milestone 11 establishes dedicated Windows boot compatibility for the MVentX multi-environment boot ecosystem. Unlike Linux distributions that can mount a virtual loopback device via kernel command-line arguments (e.g., `iso-scan/filename`, `boot=live findiso=`), Microsoft Windows utilizes a distinct bootloader and storage driver lifecycle (`bootmgfw.efi` -> BCD -> `boot.wim` -> `ntoskrnl.exe` -> `setup.exe`).

This milestone implemented, tested, and verified:
1. **Windows ISO Inspection Subsystem** (`core/iso_manager/windows_iso_inspector.py`): Non-destructive ISO 9660 directory traversal detecting EFI bootloaders (`bootx64.efi`, `bootmgr.efi`, `cdboot.efi`, ARM64/x86 variants), BCD stores, WinPE images (`sources/boot.wim`), and install payloads (`install.wim`, `install.esd`, split `install*.swm`).
2. **Strategy Evaluation & Verification Taxonomy** (`core/boot_config/windows_boot_strategy.py`): A rigorous six-tier verification model distinguishing ISO inspection, media extraction, menu generation, Boot Manager execution, WinPE kernel initialization, and Windows Setup interactive UI reach. Primary strategy is **staged / extracted media with direct UEFI chainloading** (pending physical validation, avoiding unverified 100% claims).
3. **Resilient GRUB Boot Script Generation** (`core/boot_config/windows_boot_generator.py`): Space-escaped, partition-discovering, defensive GRUB 2 script blocks with descriptive error screens if files or partitions are missing.
4. **Media Staging & Storage Sizing** (`core/iso_manager/windows_media_manager.py`): Exact capacity calculations accounting for raw size, 5% cluster padding on exFAT, and a configurable 500 MiB safety margin, with non-destructive deregistration (removing registration never silently deletes staged user media).
5. **Desktop Manager UI Integration** (`manager/ui/add_environment.py` & `manager/services/iso_service.py`): Automatic detection of Windows media, visual feedback badge, architecture tags, and automated configuration.
6. **Testing & QEMU OVMF Verification**: 29 new automated tests (25 unit + 4 integration) expanding the regression baseline from 207 to **236 passing automated tests (100% pass rate)**. Real UEFI boot execution verified in QEMU with OVMF firmware.

---

## 1. Technical Architecture & Investigation

### Why Pure GRUB Loopback Fails for Windows Installation Media

A frequent misconception in multi-boot engineering is attempting to boot Windows ISOs using standard GRUB loopback commands:
```grub
# INEFFECTIVE APPROACH FOR WINDOWS INSTALLATION MEDIA
loopback loop (hd0,gpt2)/ISO/Win11.iso
chainloader (loop)/efi/boot/bootx64.efi
boot
```
While GRUB can read the loopback device in UEFI firmware memory, this fails during early boot for fundamental architectural reasons:
1. **UEFI Boot Services Termination**: When `bootmgr.efi` loads `boot.wim` and invokes `ntoskrnl.exe`, the Windows kernel calls `ExitBootServices()`.
2. **Loss of Virtual Loop Device**: At the moment `ExitBootServices()` executes, all GRUB firmware memory mappings and virtual disc devices disappear.
3. **Storage Stack Discovery Failure**: When Windows PE user-mode initializes and `setup.exe` runs, it polls standard PCI/NVMe/USB storage controllers for an attached optical disc or folder containing `\sources\install.wim`. Because the ISO is just a flat file on an exFAT partition without an in-kernel virtual CD filter driver (like Ventoy's closed-source `ventoy.sys` hook driver), `setup.exe` displays the classic error:
   > *"A media driver your computer needs is missing. This could be a DVD, USB or Hard disk driver."*

### Why Extracted / Staged Media Works Reliably on MVentX

MVentX's dual-partition architecture provides a clean solution:
- **Partition 1 (`MVX_BOOT`)**: FAT32 EFI System Partition hosting GRUB 2.12 and configuration.
- **Partition 2 (`MVX_DATA`)**: exFAT Basic Data Partition supporting files $>4\text{ GiB}$ (such as modern Windows 11 `install.wim` payloads which often exceed 5–6 GiB).
- **Windows PE exFAT Driver**: Windows PE natively includes `exfat.sys`.
- **Root and Subfolder Resolution**:
  - `bootmgfw.efi` queries its own EFI Loaded Image handle to determine the volume it was loaded from.
  - It searches `\EFI\Microsoft\Boot\BCD` or `\Boot\BCD` relative to that volume root.
  - When Windows files reside on `MVX_DATA`, Windows Boot Manager natively reads BCD, loads `sources\boot.wim`, and `setup.exe` scans attached exFAT volumes, successfully discovering `\sources\install.wim`.

---

## 2. Six-Tier Verification Taxonomy

To eliminate ambiguity and prevent conflating menu creation with OS execution, Milestone 11 adopts a strict 6-tier verification taxonomy:

| Tier | Name | Scope / Definition | Verification Status |
| :--- | :--- | :--- | :--- |
| **Level 1** | **ISO Inspected** | Non-destructive parsing of ISO 9660 structures, EFI bootloader detection (`bootx64.efi`), BCD discovery, and WIM payload classification (`install.wim`, `install.esd`, split `.swm`). | **VERIFIED (Automated)** |
| **Level 2** | **Media Staged** | ISO extraction to `MVX_DATA` with free-space validation, cluster alignment, safety margin, and post-extraction payload integrity verification. | **VERIFIED (Automated)** |
| **Level 3** | **Menu Generated** | GRUB 2 script generation with partition discovery (`search --label`), path quoting, and fallback error screens. | **VERIFIED (Automated)** |
| **Level 4** | **Boot Manager Executed** | Windows EFI bootloader executed by UEFI firmware via GRUB `chainloader` from an exFAT volume. | **VERIFIED (Automated QEMU + OVMF)** |
| **Level 5** | **WinPE Initialized** | Windows PE kernel (`ntoskrnl.exe`) initialized, `exfat.sys` mounted, WinPE command prompt reachable. | *Requires full Microsoft proprietary image on hardware/VM* |
| **Level 6** | **Setup UI Interactive** | Windows Setup interactive graphical wizard reached with payload discovered. | *Requires full interactive Windows session* |

> [!IMPORTANT]
> MVentX explicitly reports Level 4 as the verified boundary in automated CI/test environments, pending end-to-end Level 5/6 validation on physical hardware.

---

## 3. Implementation Details

### 3.1. Windows ISO Inspector (`core/iso_manager/windows_iso_inspector.py`)
- Non-destructive ISO 9660 traversal reading Primary Volume Descriptors (PVD) and directory records up to depth 6.
- Discovers EFI bootloaders (`/efi/boot/bootx64.efi`, `/efi/boot/bootarm64.efi`, `/efi/boot/bootia32.efi`, `/bootmgr.efi`, `/efi/microsoft/boot/cdboot.efi`).
- Detects BCD databases (`/efi/microsoft/boot/bcd`, `/boot/bcd`).
- Classifies payload types (`WindowsPayloadType.WIM`, `WindowsPayloadType.ESD`, `WindowsPayloadType.SWM`).
- Detects CPU architectures (`x64`, `arm64`, `x86`, `multi`).

### 3.2. Windows Boot Generator (`core/boot_config/windows_boot_generator.py`)
Generates native GRUB 2 script blocks formatted defensively:
```grub
menuentry "Windows 11 Setup x64" --id "win11" {
    echo "Locating MVentX storage partition (MVX_DATA)..."
    set win_root=""
    if search --no-floppy --label --set=win_root MVX_DATA; then
        true
    elif [ -n "$mventx_data_dev" ]; then
        set win_root="$mventx_data_dev"
    else
        echo "[ERROR] Storage Partition Not Found: MVX_DATA"
        sleep 5
        return
    fi

    echo "  Partition found: ($win_root)"
    echo "  Inspecting Windows bootloader: /windows/win11/efi/boot/bootx64.efi"
    if [ ! -f "($win_root)/windows/win11/efi/boot/bootx64.efi" ]; then
        echo "[ERROR] Windows Bootloader Missing"
        sleep 5
        return
    fi

    echo "  Bootloader verified. Handing execution to Windows Boot Manager..."
    set root="($win_root)"
    chainloader "($win_root)/windows/win11/efi/boot/bootx64.efi"
    boot
}
```

### 3.3. Windows Media Staging & Sizing (`core/iso_manager/windows_media_manager.py`)
- **Capacity Formula**:
  $$\text{Total Required} = \text{Raw Payload} + (\text{Raw Payload} \times 0.05) + 500\text{ MiB (Safety Margin)}$$
- **Integrity Validation**: Verifies presence and non-zero sizes of `bootx64.efi`, `sources/boot.wim`, and `sources/install.wim` / `install.esd` / `install*.swm`.
- **Cancellation & Cleanup**: Atomic staging directory cleaned up if cancelled or aborted.
- **Data Protection**: `deregister_windows_media` preserves files by default.

### 3.4. Desktop Manager Integration (`manager/ui/add_environment.py`)
- Automatically inspects candidate ISOs for Windows markers.
- Displays cyan status banner: `WINDOWS MEDIA DETECTED: X64`.
- Auto-selects `windows_extracted_media` boot method.
- Automatically prompts user with required storage capacity before staging.

---

## 4. Test Suite & Verification Results

### Baseline Expansion
- Prior Baseline (Milestone 10): 207 tests (165 unit + 42 integration).
- New Tests Added:
  - `test_windows_iso_inspector.py`: 6 unit tests
  - `test_windows_boot_strategy.py`: 6 unit tests
  - `test_windows_boot_generator.py`: 6 unit tests
  - `test_windows_media_manager.py`: 7 unit tests
  - `test_windows_boot_integration.py`: 3 integration tests
  - `test_windows_qemu_boot.py`: 1 integration test (QEMU OVMF)
- **Current Baseline: 236 passing tests (190 unit + 46 integration, 100% pass rate).**

```
python -m unittest discover tests/unit
Ran 190 tests in 52.546s - OK

python -m unittest discover tests/integration
Ran 46 tests in 233.561s - OK
```

---

## 5. Summary & Limitations

1. **Host Safety Preserved**: No host partition tables, host BCD configurations, or physical storage devices were modified.
2. **Strategy Viability**: Direct UEFI chainloading of staged media from exFAT avoids the loopback driver missing error, provided the BCD and bootloader paths resolve correctly.
3. **Verification Scope**: Level 4 (UEFI firmware chainloading transition) is fully verified under QEMU and OVMF. Level 5 and Level 6 remain subject to hardware testing with official retail/OEM Windows media.

