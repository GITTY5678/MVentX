# MVentX (Multi-Environment Boot Experience)

MVentX is an open-source, customizable multiboot USB platform inspired by Ventoy.

It allows users to initialize a USB drive once and subsequently manage bootable environments—including ISO images and chainloaded operating systems—without needing to reformat or reinstall the drive. Users have granular control to manually add, register, rename, enable, disable, reorder, and remove bootable targets.

---

## Key Principles & Design Philosophy

1. **User Control**: Users have complete ownership over the boot menu. No operating system or distribution is mandatory, and no arbitrary boot entries are hardcoded.
2. **Explicit Registration**: MVentX adopts a manual registration model rather than arbitrary filesystem auto-discovery. Only environments explicitly registered by the user appear in the boot menu.
3. **Decoupled Architecture**: Boot-time components (GRUB 2 / UEFI) run entirely independently of the host-level management application (Python / PySide6). GRUB does not depend on Python or desktop UI runtimes.
4. **Self-Contained Storage**: All configuration, metadata, and registered ISO files reside on the target USB drive itself, ensuring portability across multiple host machines.
5. **Safety First**: Non-destructive operation is prioritized. Boot configurations are validated before deployment, and existing configurations are backed up prior to modification. Destructive USB formatting operations are restricted to dedicated initial installer routines.

---

## Technology Stack

- **Bootloader**: GRUB 2 (Modular EFI & BIOS configuration)
- **Firmware Integration**: UEFI (x86_64, with support for legacy BIOS/CSM chainloading)
- **Management Suite**: Python 3.10+
- **Desktop UI**: PySide6 (Qt 6)
- **Configuration & Metadata**: JSON with JSON Schema validation
- **Filesystem Layout**:
  - Partition 1 (`MVX_BOOT`): FAT32 for EFI bootloader, GRUB binaries, and generated configuration.
  - Partition 2 (`MVX_DATA`): exFAT (or NTFS/ext4) for user ISO storage and environment payload storage.
- **Testing & Verification**: QEMU / KVM virtualized boot testing

---

## Repository Structure

```
MVentX/
├── docs/             # Technical specifications, architecture, and guides
├── boot/             # GRUB 2 bootloader configs, EFI structures, and boot assets
├── core/             # Core Python business logic (ISO manager, storage, config)
├── manager/          # PySide6 desktop GUI and management service layer
├── installer/        # USB provisioning, partition manager, and bootloader installer
├── boot_methods/     # Modular boot strategies (Linux loopback, Windows, chainloading)
├── tests/            # Unit, integration, and QEMU-based boot test suites
├── scripts/          # Developer tooling and build scripts
└── resources/        # Icons, logos, and UI/GRUB themes
```

---

## Getting Started

### Prerequisites

- Python 3.10 or higher
- `pip` or modern package manager (e.g. `uv`, `poetry`)
- GRUB 2 utilities (`grub-install`, `grub-mkimage` for Linux/WSL environments)
- QEMU (`qemu-system-x86_64`) for virtual boot testing

### Installation & Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/mventx/mventx.git
   cd mventx
   ```

2. Run the development environment setup script:
   ```bash
   # On Linux/macOS/WSL:
   bash scripts/setup_dev.sh

   # Or manually with Python venv:
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   ```

3. Run test suite:
   ```bash
   pytest
   ```

---

## Documentation

- [Architecture Overview](docs/architecture.md)
- [Boot Process Specification](docs/boot-process.md)
- [Installation Guide](docs/installation.md)
- [Configuration Schema & Reference](docs/configuration.md)
- [Development Roadmap](docs/development-roadmap.md)
- [Technical Decisions & Rationale](docs/technical-decisions.md)

---

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE) (or see individual component licenses for GRUB-compatible licensing considerations).

