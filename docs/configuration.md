# MVentX Configuration Specification

This document defines the configuration model, schema rules, and synchronization mechanisms between the JSON representation on disk and the generated GRUB menu script.

---

## 1. Storage Location

The configuration file resides directly on the MVentX USB drive:
```
[MVX_DATA Partition]/mventx/mventx.json
```

Because the configuration file lives on the USB drive, any computer with the MVentX manager installed can manage the drive, preserving complete portability.

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

## 4. Menu Generation Workflow

1. User modifies configuration via `mventx-manager` (adds ISO, reorders, enables/disables).
2. The manager validates input against `schema.json`.
3. The manager writes `mventx.json` with an atomic rename (writing to `mventx.json.tmp` first, and keeping `mventx.json.bak`).
4. `core.boot_config.generator` parses the JSON and outputs `/boot/grub/mventx_menu.cfg` on the `MVX_BOOT` partition.
5. Next boot, GRUB parses `mventx_menu.cfg` and renders the exact list of enabled environments.

