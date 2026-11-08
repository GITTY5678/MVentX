# Milestone 18: Official GNU GRUB Command Integration & System Utility Reliability

**Project:** MVentX (Multi-Environment Boot Experience)  
**Repository:** `C:\MVentX`  
**Bootloader Base:** Official GNU GRUB 2.12 (`x86_64-efi`)  
**Host Environment:** Windows 11, PowerShell 5.1, Python 3.14  
**Date:** November 2026  
**Status:** Implemented, Unit-Tested, and QEMU/OVMF Verified  

---

## 1. Executive Summary & Root Cause Analysis

Testing MVentX on physical HP laptop hardware from the SanDisk Cruzer Blade USB (`03026216101525041338`) successfully booted to the primary GRUB gfxterm menu. However, attempting to use the **MVentX System Utilities** submenu produced runtime errors:
- Selecting **Reboot System** returned: `error: can't find command 'reboot'`
- Selecting **Halt / Power Off System** returned: `error: can't find command 'halt'`
- Invoking `exit` failed to hand off control cleanly to the UEFI firmware dispatcher.
- When booting under certain firmware environments, dynamic modules reported: `error: prohibited by secure boot policy` or `Secure Boot Violation: Invalid signature detected.`

### Root Causes Discovered

| Failure | Root Cause | Mechanism in Official GNU GRUB 2.12 |
|---|---|---|
| **Unknown command `reboot`** | `reboot` is not a core built-in command in `BOOTX64.EFI`; it is implemented by dynamic module `reboot.mod`. `BootloaderInstaller.deploy_modules()` only copied `*.mod` files and **omitted `command.lst`**. Without `command.lst`, GRUB has no autoload table and does not know that command `reboot` maps to `reboot.mod`. Furthermore, `grub.cfg` did not issue an explicit `insmod reboot`. | Dynamic command autoload table missing from `$prefix/x86_64-efi/command.lst`. |
| **Unknown command `halt`** | `halt` resides in `halt.mod`, which depends on `acpi.mod` (`extcmd` + `mmap`). Because `moddep.lst` and `command.lst` were not copied to the target USB, GRUB could neither autoload `halt` nor resolve its ACPI dependency chain. | Missing `command.lst` and `moddep.lst` on ESP partition. |
| **Exit command failure** | `exit` is implemented by `minicmd.mod`. Without `minicmd.mod` loaded or `command.lst` present, `exit` was an unrecognized command. In UEFI, `exit` calls the UEFI boot service `Exit()`, which returns control to whatever firmware manager launched the bootloader. | Missing `minicmd.mod` autoloading. |
| **`prohibited by secure boot policy`** | `BOOTX64.EFI` and the `.mod` dynamic modules in the distribution are unsigned (Certificate Table Size = 0x0). When UEFI Secure Boot is active in firmware, GRUB enters **lockdown mode** (`grub_is_lockdown() == 1`), which strictly forbids loading unsigned dynamic ELF modules from disk. | GRUB UEFI lockdown mode triggered by NVRAM variable `SecureBoot == 1`. |
| **`invalid arch-independent ELF magic` (Virtual disks)** | In `make_dual_partition_disk.py`, directory cluster allocation used `ceil(len(name)/13)` instead of `ceil((len(name)+1)/13)` (missing the null terminator for VFAT). Across 290+ files in `/boot/grub/x86_64-efi/`, the directory entry chain underestimated its size and overflowed into the cluster space of the first files (`acpi.mod`), overwriting their ELF headers. | VFAT null-terminator cluster sizing defect in disk image builder. |

---

## 2. Command Capability Registry Architecture

MVentX introduces the **Command Capability Registry** (`core/boot_config/grub_capabilities.py`), enforcing the rule that the boot menu must never advertise operations that the runtime cannot execute.

```
                           +--------------------------------------+
                           |       GRUBCapabilityRegistry        |
                           +--------------------------------------+
                                      |                |
             +------------------------+                +-------------------------+
             |                                                                   |
             v                                                                   v
+--------------------------+                                        +--------------------------+
|   PE32+ Header Parser    |                                        |     Manifest Parsers     |
|   (inspect_efi_binary)   |                                        | (command.lst/moddep.lst) |
+--------------------------+                                        +--------------------------+
             |                                                                   |
             |                                                                   |
             +--------------------> +---------------------+ <--------------------+
                                    |  CapabilityReport   |
                                    +---------------------+
                                               |
                  +----------------------------+----------------------------+
                  |                                                         |
                  v                                                         v
   +------------------------------+                          +------------------------------+
   |   BootloaderInstaller.       |                          |    GrubMenuGenerator.        |
   |   deploy_all()               |                          |    generate_menu_script()    |
   |   Validates deployed assets  |                          |    Emits only verified       |
   |   and dependency closure     |                          |    System Utility submenus   |
   +------------------------------+                          +------------------------------+
```

### Command Types & Delivery Classification

1. **`BUILTIN` (Compiled directly into `BOOTX64.EFI` core image):**
   - `echo`, `sleep`, `test`, `true`, `false`, `search`, `configfile`, `source`, `normal`, `menuentry`, `submenu`, `insmod`, `ls`, `set`, `unset`, `export`, `terminal_output`, `terminal_input`, `loadfont`, `boot`.
   - Guaranteed executable regardless of filesystem state, dynamic module availability, or Secure Boot lockdown.

2. **`MODULE` (Delivered via official `.mod` files in `/boot/grub/x86_64-efi/`):**
   - `reboot` (`reboot.mod`)
   - `halt`, `poweroff` (`halt.mod`)
   - `exit` (`minicmd.mod`)
   - `fwsetup` (`efifwsetup.mod`)
   - `exfat` (`exfat.mod`)
   - `fat` (`fat.mod`)
   - `loopback` (`loopback.mod`)
   - `linux`, `initrd` (`linux.mod`)
   - `chainloader` (`chain.mod`)

3. **`FIRMWARE_DEPENDENT` (Requires UEFI firmware runtime support):**
   - `fwsetup`: Supported when the motherboard firmware advertises `EFI_OS_INDICATIONS_BOOT_TO_FW_UI` support in `OsIndicationsSupported`. If unsupported, GRUB displays a friendly notification without pretending it succeeded.
   - `halt`: Uses ACPI sleep state D3/S5 via UEFI runtime services.

4. **`SECURITY_RESTRICTED` (Subject to Secure Boot lockdown):**
   - Dynamic module loading from disk is blocked when UEFI Secure Boot is active. Requires running with Secure Boot disabled in BIOS, or using a signed shim + signed GRUB core image.

---

## 3. Module Dependency Resolution Manifest

GNU GRUB 2.12 relies on `moddep.lst` to resolve inter-module dependencies. The capability registry dynamically parses and enforces these dependency chains:

| Module | Direct Dependencies (`moddep.lst`) | Transitive Closure (All required modules) |
|---|---|---|
| `reboot.mod` | *None* | `reboot` |
| `halt.mod` | `acpi` | `halt` -> `acpi` -> `extcmd`, `mmap` |
| `minicmd.mod` | *None* | `minicmd` (`exit`) |
| `efifwsetup.mod` | *None* | `efifwsetup` (`fwsetup`) |
| `exfat.mod` | `fshelp` | `exfat` -> `fshelp` |
| `fat.mod` | `fshelp` | `fat` -> `fshelp` |
| `loopback.mod` | `extcmd` | `loopback` -> `extcmd` |
| `linux.mod` | `boot`, `mmap`, `relocator`, `video` | `linux` -> `boot`, `mmap`, `relocator`, `video` |
| `chain.mod` | `boot`, `efinet`, `net` | `chain` -> `boot`, `efinet`, `net` |

### Staging & Deployment Changes

In `installer/bootloader_installer.py`:
- `deploy_modules()` now deploys **both `*.mod` binaries AND all `*.lst` manifests** (`command.lst`, `moddep.lst`, `fs.lst`, `partmap.lst`, `terminal.lst`, `video.lst`, `crypto.lst`, `parttool.lst`).
- `deploy_all()` runs `GRUBCapabilityRegistry.validate_staged_assets()` across the target ESP partition and reports missing dependencies or unsigned binary status.

---

## 4. Repaired MVentX System Utilities

The System Utilities submenu is now fully integrated both into static `grub.cfg` and into dynamic `mventx_menu.cfg`:

```grub
# Essential modules pre-loaded during initialization:
insmod reboot
insmod acpi
insmod halt
insmod minicmd
if [ "${grub_platform}" = "efi" ]; then
    insmod efifwsetup
fi

# MVentX System Utilities Submenu:
submenu "MVentX System Utilities" --id "mventx-system-utils" {
    if [ "${grub_platform}" = "efi" ]; then
        menuentry "UEFI Firmware Settings" --id "uefi-firmware" {
            echo "Entering UEFI Firmware Settings..."
            if fwsetup; then
                true
            else
                echo ""
                echo "Notice: Entering UEFI Firmware Settings is not supported"
                echo "by this computer's UEFI firmware implementation."
                echo ""
                echo "Press Enter or ESC to return to menu."
                sleep 5
            fi
        }
    fi

    menuentry "Reboot System" --id "system-reboot" {
        echo "Rebooting system..."
        reboot
    }

    menuentry "Halt / Power Off System" --id "system-halt" {
        echo "Powering off system..."
        halt
    }

    menuentry "Exit GRUB (Return to UEFI Boot Manager)" --id "system-exit" {
        echo "Returning to UEFI Boot Manager..."
        exit
    }
}
```

---

## 5. Verification Results

### Level 1: Build & Static Validation
- `BOOTX64.EFI`: Verified x86_64 PE32+ (Machine: 0x8664). Verified unsigned (Cert table size = 0).
- Runtime modules: 270 `.mod` files and 8 `.lst` files verified present in `boot/grub/x86_64-efi/`.
- Dependency manifest `moddep.lst` verified intact with 269 dependency entries.

### Level 2: Automated Unit Tests
- `tests/unit/test_grub_capabilities.py`: 10/10 passed.
  - Standard catalog verification.
  - `command.lst` and `moddep.lst` parsing.
  - Recursive dependency resolution.
  - PE32+ header inspection on real `BOOTX64.EFI`.
  - Corrupt binary rejection.
  - Asset staging audit & missing-module detection.
  - Menu script generation and unsupported utility filtering.
- `tests/unit/test_bootloader_installer.py`: 6/6 passed.
  - Verified directory layout, EFI binary copying, configuration deployment, module and manifest staging, initial menu compilation, and capability reporting.
- **Full Unit Test Suite:** **304/304 tests passed cleanly.**

### Level 3: QEMU + OVMF Headless Testing
- `tests/integration/test_grub_command_execution.py`: 5/5 passed.
  1. `test_qemu_reboot_command_execution`: Verified cold reset. GRUB called `reboot`, and UEFI BDS restarted the boot sequence.
  2. `test_qemu_halt_command_execution`: Verified ACPI poweroff. GRUB called `halt`, and QEMU terminated with exit code 0.
  3. `test_qemu_exit_command_execution`: Verified UEFI handoff. GRUB called `exit`, and the firmware invoked `UiApp`.
  4. `test_qemu_exfat_module_loading_and_discovery`: Verified `insmod exfat`, searched for `MVX_DATA` by label, and accessed filesystem.
  5. `test_qemu_dynamic_menu_with_system_utilities`: Verified dynamic `mventx_menu.cfg` generation, sourcing via `configfile`, and submenu rendering.
- **Virtual Pipeline Integration:** `test_windows_physical_pipeline_virtual.py` and `test_usb_installation_e2e.py` passed with 0 errors.

---

## 6. Secure Boot Guidance & Firmware Target

- **Current Status:** `BOOTX64.EFI` is an unsigned open-source GNU GRUB 2.12 EFI application.
- **Observed Behavior on Physical HP Laptop:**
  - With **Secure Boot Enabled**: Firmware reports `Secure Boot Violation: Invalid signature detected.` or GRUB displays `error: prohibited by secure boot policy` due to lockdown mode.
  - With **Secure Boot Disabled**: Unsigned EFI binaries and `.mod` dynamic modules load without restriction.
- **Physical Testing Prerequisite:** The user must configure their HP laptop BIOS/UEFI settings with **Secure Boot: Disabled** before testing.

---

## 7. Instructions for Physical Hardware Verification

To verify Milestone 18 on the physical HP laptop with the dedicated SanDisk Cruzer Blade USB (`PhysicalDrive1`):

1. **Deploy to Physical USB:**
   Run the MVentX resume tool to deploy the updated bootloader, modules, manifests, and menus to the formatted partitions:
   ```powershell
   python scripts/resume_physical_installation.py --serial 03026216101525041338
   ```

2. **Configure HP Laptop Firmware:**
   - Power on the HP laptop while tapping **F10** to enter Computer Setup (BIOS).
   - Navigate to **Security** or **Boot Options**.
   - Set **Secure Boot** to **Disabled**.
   - Save changes and exit (**F10** -> Yes).

3. **Boot from SanDisk USB:**
   - Power on while tapping **F9** to enter the Boot Device Options menu.
   - Select the **SanDisk Cruzer Blade** USB entry.

4. **Execute Hardware Tests in GRUB Menu:**
   - Verify the main menu displays **MVentX System Utilities**.
   - Navigate into **MVentX System Utilities**:
     - **Reboot System**: Select and verify that the machine immediately performs a hardware reboot.
     - **Halt / Power Off System**: Select and verify that the machine shuts down and powers off.
     - **Exit GRUB**: Select and verify that control returns to the HP Startup / Boot Menu.
     - **UEFI Firmware Settings**: Select and verify whether the HP laptop supports OS-initiated firmware setup or displays the fallback notice.

