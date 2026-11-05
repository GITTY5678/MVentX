# Manager Services Layer (`manager/services/`)

This directory houses the asynchronous service and worker layer bridging the PySide6 UI thread with the backend `core` modules.

## Planned Services
- `drive_watcher.py`: Background thread monitoring USB plug/unplug events and signaling UI updates.
- `iso_worker.py`: Background worker executing checksum verification and metadata extraction without blocking the main event loop.
- `sync_service.py`: Orchestrator handling the commit pipeline:
  1. Validate in-memory environment list against JSON Schema.
  2. Create timestamped backup of existing `mventx.json` and `mventx_menu.cfg`.
  3. Atomically write new `mventx.json`.
  4. Generate and write `mventx_menu.cfg` to the boot partition.
  5. Sync filesystem buffers (`flush` / `fsync`).

