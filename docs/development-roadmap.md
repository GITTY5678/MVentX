# MVentX Development Roadmap

This document outlines the phased development plan for MVentX from initial project scaffolding to production release.

---

## Phase 1: Repository Foundation & Architecture (Current Phase)
- [x] Establish repository directory structure and naming conventions.
- [x] Define JSON configuration schema (`schema.json`) and defaults (`defaults.json`).
- [x] Author core architecture, boot process, and technical decisions documentation.
- [x] Configure Python build system (`pyproject.toml`) and development dependencies.
- [x] Define module boundaries, stub interfaces, and docstrings.

---

## Phase 2: Core Subsystems Implementation
- [ ] **Config Engine**:
  - Implement JSON schema validator using `jsonschema`.
  - Implement atomic read/write and backup mechanisms.
  - Implement migration helpers for schema version upgrades.
- [ ] **ISO Manager**:
  - ISO header inspection (detecting El Torito, volume descriptors, kernels).
  - SHA-256 integrity verification.
  - Manual registration API (add, update, delete, reorder).
- [ ] **Storage Layer**:
  - Cross-platform drive detection (`pyudev` on Linux, `WMI` / `diskpart` on Windows).
  - Partition mount resolution and drive health checks.
- [ ] **Boot Config Generator**:
  - Template engine for compiling JSON environments into standard `grub.cfg` syntax.
  - Validation of output GRUB scripts.

---

## Phase 3: Desktop Management UI (PySide6)
- [ ] Main window with USB drive selector and capacity indicator.
- [ ] Table/List view of registered environments with drag-and-drop reordering.
- [ ] Registration modal for new ISOs with metadata autodetection hints.
- [ ] Inline editing of display names, boot methods, and kernel parameters.
- [ ] Safe commit workflow: validate -> backup -> write JSON -> generate `mventx_menu.cfg`.

---

## Phase 4: Installer Subsystem & Partitioning
- [ ] USB formatting workflow (warning dialogues, disk locking).
- [ ] Partition creation (FAT32 EFI partition + exFAT storage partition).
- [ ] GRUB EFI binary bundling and deployment.
- [ ] First-boot validation test in QEMU.

---

## Phase 5: Boot Methods & Compatibility Matrix
- [ ] Linux generic loopback boot method (Ubuntu, Debian, Fedora, Arch).
- [ ] Specialized Linux boot recipes (distros requiring custom loopback parameters).
- [ ] Windows ISO boot integration (using UEFI chainloading or wimboot).
- [ ] Windows Boot Manager chainload recipe.
- [ ] Comprehensive hardware compatibility testing matrix across Intel/AMD UEFI platforms.

---

## Phase 6: Polishing & Community Release
- [ ] Theme customization engine for GRUB.
- [ ] Multi-language UI localization.
- [ ] Automated CI/CD pipelines with QEMU integration testing.
- [ ] Installer packages (AppImage, RPM, DEB, Windows MSI installer).

