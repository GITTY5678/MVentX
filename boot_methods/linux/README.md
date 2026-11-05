# Linux Boot Method (`boot_methods/linux/`)

This directory contains boot templates and logic for booting Linux distribution ISOs.

## Method Architecture
Linux ISOs typically boot in GRUB via loopback mounting:

```grub
menuentry "${DISPLAY_NAME}" --id "${ENV_ID}" {
    set iso_path="${ISO_PATH}"
    loopback loop ($mventx_data_dev)${iso_path}
    linux (loop)/${KERNEL_PATH} ${BOOT_PARAM} iso-scan/filename=${iso_path} ${CUSTOM_ARGS}
    initrd (loop)/${INITRD_PATH}
    boot
}
```

## Supported Flavors & Recipes (Modular Strategy)
- **Casper / Ubuntu family**: Uses `iso-scan/filename=` or `findiso=`.
- **Dracut / Fedora / RHEL family**: Uses `iso-scan/filename=` or `root=live:CDLABEL=...`.
- **Arch / Archiso**: Uses `img_dev=$mventx_data_dev img_loop=${iso_path}`.
- **Debian Live**: Uses `findiso=${iso_path}`.

Templates will be loaded dynamically by `core.boot_config.generator`.

