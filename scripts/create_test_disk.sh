#!/usr/bin/env bash
# ==============================================================================
# MVentX Virtual Disk Creator for UEFI + GRUB Testing (Milestone 2)
# ==============================================================================
# Creates a dual-partition GPT virtual disk with:
#   - Partition 1: MVX_BOOT (FAT32, 64 MB)
#   - Partition 2: MVX_DATA (exFAT, 192 MB)
#
# Target OS: Ubuntu 24.04 LTS / Debian Linux
# Required packages:
#   sudo apt install -y grub-efi-amd64-bin python3 qemu-system-x86 ovmf
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build"
DISK_IMAGE="${BUILD_DIR}/mventx_test.img"
ESP_DIR="${BUILD_DIR}/esp"
DATA_DIR="${BUILD_DIR}/data"
BOOT_EFI="${BUILD_DIR}/BOOTX64.EFI"

echo "=========================================================="
echo " MVentX Milestone 2: Dual-Partition Disk Preparation       "
echo "   Partition 1: MVX_BOOT (FAT32, 64 MB)                   "
echo "   Partition 2: MVX_DATA (exFAT, 192 MB)                  "
echo "=========================================================="

# 1. Dependency Checks
MISSING_TOOLS=()
for tool in grub-mkimage python3; do
    if ! command -v "$tool" &> /dev/null; then
        MISSING_TOOLS+=("$tool")
    fi
done

if [ ${#MISSING_TOOLS[@]} -ne 0 ]; then
    echo "Error: Missing required tools: ${MISSING_TOOLS[*]}" >&2
    echo "Install dependencies on Ubuntu 24.04 LTS with:" >&2
    echo "    sudo apt update && sudo apt install -y grub-efi-amd64-bin python3 qemu-system-x86 ovmf" >&2
    exit 1
fi

mkdir -p "${BUILD_DIR}"
mkdir -p "${ESP_DIR}/EFI/BOOT"
mkdir -p "${ESP_DIR}/boot/grub/fonts"
mkdir -p "${DATA_DIR}/ISO"
mkdir -p "${DATA_DIR}/config"
mkdir -p "${DATA_DIR}/metadata"
mkdir -p "${DATA_DIR}/backups"

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
python3 "${REPO_ROOT}/scripts/generate_test_menu.py" ${EMPTY_MENU_FLAG} --output "${ESP_DIR}/boot/grub/mventx_menu.cfg"

# 4. Copy primary grub.cfg to ESP staging
cp "${REPO_ROOT}/boot/grub/grub.cfg" "${ESP_DIR}/boot/grub/grub.cfg"

# 5. Build standalone GRUB 2 UEFI x86_64 image if not present
if [ ! -f "${BOOT_EFI}" ]; then
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
fi

cp "${BOOT_EFI}" "${ESP_DIR}/EFI/BOOT/BOOTX64.EFI"

# 6. Build Dual-Partition GPT Disk via Python Generator (Rootless & Standard)
echo "[*] Building 256MB Dual-Partition GPT disk image..."
python3 "${REPO_ROOT}/scripts/windows/make_dual_partition_disk.py" \
    --esp-source "${ESP_DIR}" \
    --output "${DISK_IMAGE}" \
    --size-mb 256 \
    --esp-size-mb 64

# 7. Validate generated disk
echo "[*] Validating dual-partition GPT structure..."
python3 -c "
from core.storage.disk_validator import DiskValidator
v = DiskValidator('${DISK_IMAGE}')
rep = v.validate_all()
print(f'Disk validation passed: {rep[\"disk_size_mb\"]} MB ({len(rep[\"partitions\"])} partitions)')
for p in rep['partitions']:
    print(f'  - Partition {p[\"index\"]}: {p[\"name\"]} ({p[\"size_mb\"]} MB, LBA {p[\"start_lba\"]}..{p[\"end_lba\"]})')
"

echo "=========================================================="
echo " Virtual disk prepared successfully: ${DISK_IMAGE}"
echo " To boot in QEMU, run:"
echo "     bash scripts/run_qemu.sh"
echo "=========================================================="
