# MVentX Configuration Specification

This document defines the configuration model, schema rules, and synchronization mechanisms between the JSON representation on disk and the generated GRUB menu script.

---

## 1. Storage Location & Directory Architecture

The configuration and state directories reside directly on the `MVX_DATA` partition:
```
MVX_DATA/
├── ISO/                    # User storage for bootable ISO files
├── config/
│   └── mventx.json         # Authoritative multi-environment configuration store
├── metadata/
│   └── <env_id>.json       # Cached metadata (hashes, architectures, volume labels)
└── backups/
    └── mventx_*.json       # Timestamped atomic backups prior to mutations
```

Because the authoritative configuration lives on the USB drive, any computer with the MVentX manager or CLI installed can manage the drive, preserving complete portability.

---

## 2. Configuration Model (`mventx.json`)

The configuration file uses JSON format versioning to ensure backward and forward compatibility.

### Sample Configuration

```json
{
  "version": "1.0.0",
  "meta": {
    "title": "MVentX Multiboot System",
    "theme": "mventx-modern",
    "timeout": 10,
    "default_entry_id": "ubuntu-desktop-2404"
  },
  "environments": [
    {
      "id": "ubuntu-desktop-2404",
      "display_name": "Ubuntu 24.04 LTS Desktop",
      "type": "iso",
      "iso_path": "isos/ubuntu-24.04-desktop-amd64.iso",
      "boot_method": "linux_generic_loopback",
      "enabled": true,
      "order": 1,
      "description": "Ubuntu 24.04 LTS (Noble Numbat)",
      "custom_args": "quiet splash",
      "checksum": {
        "algorithm": "sha256",
        "value": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
      }
    },
    {
      "id": "win-boot-chainload",
      "display_name": "Windows Boot Manager (Internal SSD)",
      "type": "chainload",
      "iso_path": null,
      "boot_method": "chainload_efi",
      "target_path": "/EFI/Microsoft/Boot/bootmgfw.efi",
      "target_hint": "hd1,gpt1",
      "enabled": false,
      "order": 2,
      "description": "Chainloads host internal Windows installation"
    }
  ]
}
```

---

## 3. Schema Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `version` | string (semver) | Yes | Schema version for migration logic. |
| `meta.title` | string | Yes | Menu title displayed in GRUB. |
| `meta.theme` | string | Yes | GRUB theme name in `/boot/grub/themes/`. |
| `meta.timeout` | integer | Yes | Menu timeout in seconds before booting default entry. |
| `meta.default_entry_id` | string \| null | No | ID of environment selected by default. |
| `environments[].id` | string | Yes | Unique alphanumeric ID for the environment. |
| `environments[].display_name` | string | Yes | User-defined title shown in the GRUB menu. |
| `environments[].type` | enum | Yes | `"iso"` or `"chainload"`. |
| `environments[].iso_path` | string \| null | Conditional | Path relative to `MVX_DATA` root (e.g. `isos/image.iso`). Required if `type == "iso"`. |
| `environments[].boot_method` | string | Yes | Identifier for the boot method template (e.g., `linux_generic_loopback`, `arch_loopback`, `windows_wimboot`, `chainload_efi`). |
| `environments[].enabled` | boolean | Yes | Controls whether entry is rendered in GRUB menu. |
| `environments[].order` | integer | Yes | 1-based display sequence order in the boot menu. |
| `environments[].description` | string | No | Optional human-readable description. |
| `environments[].custom_args` | string | No | Optional kernel command-line overrides. |

---

## 4. Menu Generation & Synchronization Workflow

1. User registers or modifies an environment via `ISORegistrationManager` (or `mventx-manager` GUI).
2. The manager validates input against `schema.json` and security constraints (rejecting injection attempts, directory traversal).
3. The manager creates a timestamped backup of `mventx.json` in `backups/`.
4. The manager writes `mventx.json` via atomic file replacement (`.tmp` -> rename).
5. `StorageManager.sync_boot_menu()` compiles `/boot/grub/mventx_menu.cfg` on `MVX_BOOT`.
6. **Rollback Safety**: If menu compilation fails, `mventx.json` is immediately restored to its prior state and newly created metadata files are cleaned up.
7. During target boot, GRUB parses `mventx_menu.cfg` directly without evaluating Python or scanning directories.

---

## 5. Metadata Cache Records (`metadata/<env_id>.json`)

When an ISO is registered, detailed non-invasive header hints and metrics are cached in `metadata/<env_id>.json`:

```json
{
  "file_name": "ubuntu-24.04-desktop-amd64.iso",
  "file_size_bytes": 6215168000,
  "volume_identifier": "UBUNTU_24_04_X86_64",
  "system_identifier": "LINUX",
  "publisher_identifier": "CANONICAL",
  "detected_architecture": "x86_64",
  "suggested_boot_method": "linux_generic_loopback",
  "registered_at": "2026-11-05T19:55:00.000000+00:00",
  "has_el_torito_boot": true,
  "checksum": {
    "algorithm": "sha256",
    "value": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  }
}
```

