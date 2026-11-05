#!/usr/bin/env bash
# ==============================================================================
# MVentX Virtual Disk Creator for UEFI + GRUB Testing (Milestone 1)
# ==============================================================================
# Creates a partitioned GPT virtual disk with a FAT32 EFI boot partition (ESP),
# builds the GRUB 2 EFI binary (BOOTX64.EFI), deploys grub.cfg, and compiles
# the dynamic mventx_menu.cfg.
#
# Target OS: Ubuntu 24.04 LTS / Debian Linux
# Required packages:
#   sudo apt install -y grub-efi-amd64-bin dosfstools mtools parted python3
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build"
DISK_IMAGE="${BUILD_DIR}/mventx_test.img"
ESP_IMAGE="${BUILD_DIR}/esp.img"
BOOT_EFI="${BUILD_DIR}/BOOTX64.EFI"
GENERATED_MENU="${BUILD_DIR}/mventx_menu.cfg"

echo "=========================================================="
echo " MVentX Milestone 1: Virtual Disk Preparation             "
echo "=========================================================="

# 1. Dependency Checks
MISSING_TOOLS=()
for tool in parted mkfs.vfat mcopy mmd grub-mkimage python3 dd truncate; do
    if ! command -v "$tool" &> /dev/null; then
        MISSING_TOOLS+=("$tool")
    fi
done

if [ ${#MISSING_TOOLS[@]} -ne 0 ]; then
    echo "Error: Missing required tools: ${MISSING_TOOLS[*]}" >&2
    echo "" >&2
    echo "On Ubuntu 24.04 LTS, install dependencies with:" >&2
    echo "    sudo apt update" >&2
    echo "    sudo apt install -y grub-efi-amd64-bin dosfstools mtools parted python3 qemu-system-x86 ovmf" >&2
    exit 1
fi

mkdir -p "${BUILD_DIR}"

# 2. Parse arguments
EMPTY_MENU_FLAG=""
if [[ "${1:-}" == "--empty" ]]; then
    EMPTY_MENU_FLAG="--empty"
    echo "[*] Mode: Creating disk with EMPTY menu configuration (fallback verification)"
else
    echo "[*] Mode: Creating disk with SAMPLE registered environments"
fi

# 3. Generate dynamic menu configuration using Python core generator
echo "[*] Generating mventx_menu.cfg from configuration generator..."
python3 "${REPO_ROOT}/scripts/generate_test_menu.py" ${EMPTY_MENU_FLAG} --output "${GENERATED_MENU}"

# 4. Build standalone GRUB 2 UEFI x86_64 image
echo "[*] Building GRUB 2 UEFI binary (BOOTX64.EFI)..."
grub-mkimage \
    -O x86_64-efi \
    -o "${BOOT_EFI}" \
    -p "/boot/grub" \
    part_gpt \
    part_msdos \
    fat \
    exfat \
    ext2 \
    ntfs \
    iso9660 \
    search \
    search_fs_file \
    search_label \
    test \
    echo \
    normal \
    configfile \
    sleep \
    efi_gop \
    efi_uga \
    font \
    gfxterm \
    gfxmenu

# 5. Create blank 64MB raw disk image
echo "[*] Initializing 64MB raw disk image: ${DISK_IMAGE}..."
truncate -s 64M "${DISK_IMAGE}"

# 6. Create GPT partition table with an EFI System Partition (ESP)
# Sector 2048 is 1MiB offset. Partition size: 62MiB.
echo "[*] Partitioning disk with GPT (ESP at offset 1MiB)..."
parted -s "${DISK_IMAGE}" mklabel gpt
parted -s "${DISK_IMAGE}" mkpart ESP fat32 1MiB 63MiB
parted -s "${DISK_IMAGE}" set 1 esp on

# 7. Create standalone 62MB FAT32 filesystem labeled MVX_BOOT
echo "[*] Formatting 62MB FAT32 filesystem (MVX_BOOT)..."
truncate -s 62M "${ESP_IMAGE}"
mkfs.vfat -F 32 -n "MVX_BOOT" "${ESP_IMAGE}"

# 8. Populate filesystem using mtools (rootless operation)
echo "[*] Populating ESP directory structure and boot assets via mtools..."
mmd -i "${ESP_IMAGE}" ::EFI
mmd -i "${ESP_IMAGE}" ::EFI/BOOT
mmd -i "${ESP_IMAGE}" ::boot
mmd -i "${ESP_IMAGE}" ::boot/grub

mcopy -i "${ESP_IMAGE}" "${BOOT_EFI}" ::EFI/BOOT/BOOTX64.EFI
mcopy -i "${ESP_IMAGE}" "${REPO_ROOT}/boot/grub/grub.cfg" ::boot/grub/grub.cfg
mcopy -i "${ESP_IMAGE}" "${GENERATED_MENU}" ::boot/grub/mventx_menu.cfg

# 9. Burn ESP into disk image at sector 2048 (1MiB offset)
echo "[*] Burning ESP partition into disk image at 1MiB offset..."
dd if="${ESP_IMAGE}" of="${DISK_IMAGE}" bs=512 seek=2048 count=126976 conv=notrunc status=none
rm -f "${ESP_IMAGE}"

echo "=========================================================="
echo " Virtual disk prepared successfully: ${DISK_IMAGE}"
echo " To boot in QEMU, run:"
echo "     bash scripts/run_qemu.sh"
echo "=========================================================="

