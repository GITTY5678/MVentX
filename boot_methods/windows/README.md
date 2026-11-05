# Windows Boot Method (`boot_methods/windows/`)

This directory documents boot strategies for Windows Setup and Windows PE ISO images.

## Architectural Approaches
Windows ISOs do not natively support direct kernel loopback loading in the same manner as Linux. MVentX plans two modular strategies:

1. **wimboot Boot Method**:
   - Uses the iPXE `wimboot` utility or GRUB `wimboot` module.
   - Extracts `bootmgr`, `bcd`, `boot.sdi`, and `boot.wim` into memory and executes `bootmgr`.

2. **Loopback EFI Chainloading**:
   - For modern Windows 10/11 UEFI ISOs with standard EFI bootloaders (`/efi/boot/bootx64.efi`).
   - GRUB maps the ISO and chainloads the EFI payload directly where firmware support permits.

No Windows boot entry is mandatory; users register Windows ISOs manually as needed.

