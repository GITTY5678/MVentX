# Integration Test Suite (`tests/integration/`)

This directory contains integration tests verifying end-to-end interactions across MVentX components:
- Round-trip JSON configuration save and reload.
- Full compilation of `mventx.json` into GRUB syntax (`mventx_menu.cfg`).
- Validation that generated GRUB scripts adhere to GRUB 2 parser syntax.
- Backup creation and atomic write safety verification.

