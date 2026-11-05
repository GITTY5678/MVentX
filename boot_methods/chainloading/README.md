# Chainloading Boot Method (`boot_methods/chainloading/`)

This directory documents chainloading recipes for pre-existing operating systems and secondary bootloaders.

## Overview
Chainloading enables MVentX to pass execution control to another bootloader located on internal or external storage media.

## Common Use Cases
- Chainloading the host machine's internal Windows Boot Manager (`bootmgfw.efi`).
- Chainloading a secondary GRUB, systemd-boot, or rEFInd bootloader.
- Direct partition boot in legacy BIOS environments.

## Sample GRUB Template
```grub
menuentry "${DISPLAY_NAME}" --id "${ENV_ID}" {
    search --no-floppy --set=target_dev --file ${TARGET_PATH}
    chainloader ($target_dev)${TARGET_PATH}
    boot
}
```

## Architectural Rule
Chainloading entries are strictly optional and must never be injected without explicit user registration.

