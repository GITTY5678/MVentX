# EFI Boot Directory (`boot/efi/`)

This directory documents and stages the UEFI binary payload structures deployed to the `MVX_BOOT` (ESP) partition.

## Standard Deployment Layout
When MVentX is installed to a USB drive, the ESP partition contains:

```
MVX_BOOT (FAT32)
├── EFI/
│   └── BOOT/
│       ├── BOOTX64.EFI   # 64-bit UEFI GRUB binary
│       ├── BOOTIA32.EFI  # 32-bit UEFI fallback (optional)
│       └── grub.cfg      # Minimal stub pointing to /boot/grub/grub.cfg
└── boot/
    └── grub/
        ├── grub.cfg
        ├── mventx_menu.cfg
        └── ...
```

Binaries are generated using `grub-mkimage` during the build phase and deployed by `installer.bootloader_installer`.
Third-party binaries are never checked into version control.

