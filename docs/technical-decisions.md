# MVentX Technical Decisions Record (TDR)

This document records the architectural and technical decisions made during the design of MVentX, along with their context, trade-offs, and rationale.

---

## TDR 001: Separation of Bootloader and Management Application

### Context
Multiboot solutions frequently bundle boot-time logic with host configuration tools, resulting in fragile dependencies or non-standard boot stages.

### Decision
Strictly decouple the bootloader (GRUB 2) and host application (Python 3 / PySide6). GRUB 2 must function exclusively via static configuration files (`grub.cfg`, `mventx_menu.cfg`) with zero runtime dependency on Python, Qt, or external daemons.

### Consequences
- **Pros**: Extreme boot-time reliability; universal compatibility on any x86_64 UEFI machine; no host runtime required at boot time.
- **Cons**: Configuration changes require the desktop app to recompile the JSON state into GRUB script format before unmounting.

---

## TDR 002: Manual ISO Registration vs. Automatic Directory Scanning

### Context
Ventoy uses dynamic directory scanning at boot time to display all `.iso` files found on disk.

### Decision
MVentX mandates **explicit manual registration**. Files copied to the USB are not displayed in the boot menu until the user explicitly registers and enables them in `mventx.json`.

### Consequences
- **Pros**:
  - Eliminates unwanted ISO entries (e.g., partial downloads, rescue tools, auxiliary utility images).
  - Allows full customization of display names, descriptions, and boot arguments without altering ISO filenames.
  - Allows fine-grained ordering and toggling (enabled/disabled).
  - Faster bootloader initialization (no real-time recursive filesystem scans at boot).
- **Cons**: Users must use the MVentX Manager (or edit JSON) after copying a new ISO to make it bootable.

---

## TDR 003: Dual Partition Scheme (FAT32 + exFAT)

### Context
UEFI firmware natively expects FAT32 for the EFI System Partition (ESP), but FAT32 cannot store files larger than 4 GB. Modern OS installation ISOs often exceed 4 GB (e.g. Windows 11 is > 5 GB).

### Decision
Format the USB drive with two partitions:
1. `MVX_BOOT` (FAT32, ~256 MB): Houses EFI bootloader, GRUB core, fonts, themes, and configuration files.
2. `MVX_DATA` (exFAT, remaining space): Stores ISO payloads and the JSON database.

### Consequences
- **Pros**:
  - Conforms to standard UEFI boot specs without hacks.
  - Accommodates large (>4 GB) ISOs.
  - exFAT is natively read/writable on Windows, macOS, and Linux without proprietary drivers.
- **Cons**: Requires GRUB's `exfat` module to read the payload partition at boot time.

---

## TDR 004: Versioned JSON Configuration

### Context
Configuration structures will expand as new boot methods, verification schemes, and themes are added.

### Decision
Store all user and environment state in a versioned JSON file (`mventx.json`) governed by a formal JSON Schema (`schema.json`).

### Consequences
- **Pros**: Human-readable, machine-validatable, easy to back up, simple to migrate across schema versions.
- **Cons**: Slight parsing overhead compared to binary blobs, but trivial for the scale of boot menus (< 100 entries).

---

## TDR 005: Optional Chainloading Support

### Context
Users often want quick access to chainload existing local disk installations (e.g., host Windows or Linux installations) from their rescue USB.

### Decision
Support chainloading as a first-class environment type alongside ISO boot, but keep it strictly optional. No Windows or internal OS entries are injected automatically.

### Consequences
- **Pros**: Complete user sovereignty over their boot menu.
- **Cons**: Users must manually specify or enable chainload targets if desired.

