# Management UI Components (`manager/ui/`)

This directory will contain the PySide6 (Qt 6) user interface components for the MVentX Desktop Manager.

## Planned Architecture
- `main_window.py`: Primary application window containing drive selection, environment table, action toolbars, and status indicators.
- `environment_table.py`: Reorderable table widget displaying:
  - Display Order
  - Status (Enabled / Disabled toggle)
  - Display Name (Inline editable)
  - Type (ISO / Chainload)
  - Path / Target Hint
  - Actions (Edit, Re-validate, Remove)
- `dialogs/`:
  - `register_iso_dialog.py`: Wizard to register an ISO file, auto-extract metadata, specify custom name, and select boot recipe.
  - `chainload_dialog.py`: Wizard to configure host chainloading entries.
  - `installer_dialog.py`: Protected dialog for initializing a new USB drive.
- `widgets/`:
  - `drive_selector.py`: Dropdown widget listing connected MVentX drives.
  - `boot_preview.py`: Visual preview of what the GRUB boot menu will look like.

