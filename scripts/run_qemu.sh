#!/usr/bin/env bash
# ==============================================================================
# MVentX QEMU/KVM Virtual Boot Script (Milestone 1 Prototype)
# ==============================================================================
# Boots the prepared virtual disk in UEFI mode using OVMF firmware.
#
# Target OS: Ubuntu 24.04 LTS
# Required packages:
#   sudo apt install -y qemu-system-x86 ovmf
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build"
DISK_IMAGE="${BUILD_DIR}/mventx_test.img"

echo "=========================================================="
echo " MVentX Milestone 1: QEMU UEFI Boot Execution             "
echo "=========================================================="

# 1. Verify QEMU is installed
if ! command -v qemu-system-x86_64 &> /dev/null; then
    echo "Error: qemu-system-x86_64 is not installed." >&2
    echo "Install it on Ubuntu 24.04 LTS using:" >&2
    echo "    sudo apt update && sudo apt install -y qemu-system-x86 ovmf" >&2
    exit 1
fi

# 2. Locate OVMF UEFI firmware code file on Ubuntu 24.04 / Debian / Arch
OVMF_CODE=""
for candidate in \
    "/usr/share/OVMF/OVMF_CODE_4M.fd" \
    "/usr/share/OVMF/OVMF_CODE.fd" \
    "/usr/share/ovmf/OVMF.fd" \
    "/usr/share/edk2/ovmf/OVMF_CODE.fd" \
    "/usr/share/edk2-ovmf/x64/OVMF_CODE.fd"; do
    if [ -f "$candidate" ]; then
        OVMF_CODE="$candidate"
        break
    fi
done

if [ -z "$OVMF_CODE" ]; then
    echo "Error: Could not locate OVMF UEFI firmware image." >&2
    echo "Install the ovmf package on Ubuntu 24.04 LTS:" >&2
    echo "    sudo apt install -y ovmf" >&2
    exit 1
fi

echo "[*] Using OVMF Firmware: ${OVMF_CODE}"

# 3. Ensure test disk image exists
if [ ! -f "${DISK_IMAGE}" ]; then
    echo "[*] Disk image not found at ${DISK_IMAGE}. Generating test disk..."
    bash "${REPO_ROOT}/scripts/create_test_disk.sh" "$@"
fi

# 4. Determine KVM acceleration availability
KVM_ARGS=()
if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
    echo "[*] Hardware acceleration (/dev/kvm) detected. Enabling KVM."
    KVM_ARGS+=("-enable-kvm" "-cpu" "host")
else
    echo "[*] KVM acceleration not available. Using standard software emulation."
    KVM_ARGS+=("-cpu" "qemu64")
fi

# 5. Display mode options
EXTRA_ARGS=()
if [[ "${1:-}" == "--nographic" || "${1:-}" == "--text" ]]; then
    echo "[*] Running in headless / nographic mode..."
    EXTRA_ARGS+=("-nographic")
fi

echo "=========================================================="
echo " Starting QEMU UEFI Virtual Machine..."
echo " Booting from ${DISK_IMAGE}..."
echo " Press Ctrl+Alt+G to release mouse cursor."
echo " Close window or press Ctrl+A then X in nographic mode to exit."
echo "=========================================================="

qemu-system-x86_64 \
    "${KVM_ARGS[@]}" \
    -m 512M \
    -drive if=pflash,format=raw,readonly=on,file="${OVMF_CODE}" \
    -drive file="${DISK_IMAGE}",format=raw,index=0,media=disk \
    -net none \
    "${EXTRA_ARGS[@]}"

