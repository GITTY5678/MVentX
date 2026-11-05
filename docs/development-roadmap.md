# MVentX Development Roadmap

This document outlines the phased development plan for MVentX from initial project scaffolding to production release.

---

## Phase 1: Repository Foundation & Architecture (Completed)
- [x] Establish repository directory structure and naming conventions.
- [x] Define JSON configuration schema (`schema.json`) and defaults (`defaults.json`).
- [x] Author core architecture, boot process, and technical decisions documentation.
- [x] Configure Python build system (`pyproject.toml`) and development dependencies.
- [x] Define module boundaries, stub interfaces, and docstrings.

---

## Milestone 1: UEFI + GRUB Boot Prototype (Completed)
- [x] **GRUB Configuration Generator** (`core/boot_config/generator.py`):
  - Read `mventx.json` configuration store.
  - Render native GRUB `menuentry` blocks using custom user-defined display names.
  - Filter entries strictly based on `enabled: true`.
  - Validate all fields and guard against GRUB configuration injection (control characters, semicolons, command substitutions).
  - Deterministic entry sorting (primary: `order`, secondary: `id`).
  - Atomic write mechanism to prevent configuration corruption.
  - Fallback entry generation for empty configurations.
- [x] **Primary GRUB Configuration** (`boot/grub/grub.cfg`):
  - Dynamic discovery and loading of `mventx_menu.cfg`.
  - Fallback recovery entry when configuration is absent.
  - Terminal output and palette branding.
  - Modular system utilities submenu (Reboot, Halt, UEFI Firmware Setup).
- [x] **Virtual Disk & UEFI Boot Tooling**:
  - `scripts/create_test_disk.sh`: Automated GPT/FAT32 raw disk image builder with GRUB EFI compilation.
  - `scripts/run_qemu.sh`: OVMF UEFI boot runner for Ubuntu 24.04 LTS.
  - `scripts/generate_test_menu.py`: CLI testing and menu inspection utility.
- [x] **Automated Test Suites**:
  - 16 unit and integration tests covering schema compliance, field validation, injection prevention, atomic file writing, and determinism.

---

## Milestone 2: Multi-Partition USB Layout & ISO Loopback Engine (Next Phase)
- [ ] Implement dual-partition layout creation (`MVX_BOOT` FAT32 + `MVX_DATA` exFAT).
- [ ] Implement Linux loopback boot recipes (`casper`, `dracut`, `archiso`).
- [ ] ISO header inspection and SHA-256 verification in `core/iso_manager`.
- [ ] Mount resolution and cross-platform partition label detection (`core/storage`).

---

## Milestone 3: Desktop Management Interface (PySide6)
- [ ] Main window with USB drive selector and capacity indicator.
- [ ] Table/List view of registered environments with drag-and-drop reordering.
- [ ] Registration modal for new ISOs with metadata autodetection hints.
- [ ] Inline editing of display names, boot methods, and kernel parameters.
- [ ] Safe commit workflow: validate -> backup -> write JSON -> generate `mventx_menu.cfg`.

---

## Milestone 4: Windows ISOs & Advanced Boot Methods
- [ ] Windows ISO boot integration (wimboot / EFI chainloading).
- [ ] Internal disk chainloading for host Windows Boot Manager.
- [ ] Custom GRUB visual theme packs.
- [ ] Hardware compatibility validation matrix.
