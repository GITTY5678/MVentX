"""Linux Boot Recipes and Loopback Command Generator for MVentX.

Encapsulates distribution-specific live ISO boot parameters, kernel paths,
initramfs locations, and command-line argument generators for GRUB 2 loopback execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class LinuxBootRecipe:
    """Encapsulates kernel, initrd, and command-line parameters for a distribution family."""
    recipe_id: str
    name: str
    kernel_path: str
    initrd_path: str
    default_args: str
    description: str

    def format_args(self, iso_path: str, custom_args: Optional[str] = None, volume_id: Optional[str] = None) -> str:
        """Format the kernel command-line arguments string."""
        vol = volume_id or "MVX_DATA"
        # Quote iso_path if it contains spaces so Linux kernel cmdline does not split arguments
        safe_iso_path = f'"{iso_path}"' if " " in iso_path and not iso_path.startswith('"') else iso_path
        args = self.default_args.replace("${iso_path}", safe_iso_path).replace("${vol_id}", vol)
        if custom_args:
            args = f"{args} {custom_args}".strip()
        return args


# ------------------------------------------------------------------------------
# Standard Distribution Recipes
# ------------------------------------------------------------------------------

RECIPE_CASPER = LinuxBootRecipe(
    recipe_id="casper",
    name="Ubuntu / Mint Casper Live",
    kernel_path="/casper/vmlinuz",
    initrd_path="/casper/initrd",
    default_args="iso-scan/filename=${iso_path} boot=casper file=/cdrom/preseed/ubuntu.seed quiet splash ---",
    description="Ubuntu, Linux Mint, Pop!_OS, Elementary OS, and derivatives using Casper initramfs",
)

RECIPE_ARCHISO = LinuxBootRecipe(
    recipe_id="archiso",
    name="Arch Linux ArchISO",
    kernel_path="/arch/boot/x86_64/vmlinuz-linux",
    initrd_path="/arch/boot/x86_64/initramfs-linux.img",
    default_args="img_dev=/dev/disk/by-label/MVX_DATA img_loop=${iso_path} earlymodules=loop copytoram=n",
    description="Arch Linux, Manjaro, EndeavourOS, and derivatives using ArchISO",
)

RECIPE_DRACUT = LinuxBootRecipe(
    recipe_id="dracut",
    name="Fedora / RHEL Dracut Live",
    kernel_path="/images/pxeboot/vmlinuz",
    initrd_path="/images/pxeboot/initrd.img",
    default_args="root=live:CDLABEL=${vol_id} iso-scan.filename=${iso_path} rd.live.image quiet",
    description="Fedora, CentOS, RHEL, and derivatives using Dracut live module",
)

RECIPE_LIVE_BOOT = LinuxBootRecipe(
    recipe_id="live_boot",
    name="Debian / Clonezilla Live-Boot",
    kernel_path="/live/vmlinuz",
    initrd_path="/live/initrd.img",
    default_args="boot=live findiso=${iso_path} union=overlay config components quiet",
    description="Debian Live, Clonezilla, Kali Linux, and distributions using Debian live-boot",
)

RECIPE_GENERIC_KERNEL = LinuxBootRecipe(
    recipe_id="generic_kernel",
    name="Generic Linux Kernel Loopback",
    kernel_path="/linux",
    initrd_path="/initrd.gz",
    default_args="quiet",
    description="Generic Linux netboot, Alpine Linux, or custom kernel/initrd configurations",
)

REGISTERED_RECIPES: Dict[str, LinuxBootRecipe] = {
    "casper": RECIPE_CASPER,
    "ubuntu": RECIPE_CASPER,
    "mint": RECIPE_CASPER,
    "pop": RECIPE_CASPER,
    "linux_casper": RECIPE_CASPER,
    "archiso": RECIPE_ARCHISO,
    "arch": RECIPE_ARCHISO,
    "manjaro": RECIPE_ARCHISO,
    "linux_archiso": RECIPE_ARCHISO,
    "dracut": RECIPE_DRACUT,
    "fedora": RECIPE_DRACUT,
    "rhel": RECIPE_DRACUT,
    "centos": RECIPE_DRACUT,
    "linux_dracut": RECIPE_DRACUT,
    "live_boot": RECIPE_LIVE_BOOT,
    "debian": RECIPE_LIVE_BOOT,
    "clonezilla": RECIPE_LIVE_BOOT,
    "kali": RECIPE_LIVE_BOOT,
    "linux_live_boot": RECIPE_LIVE_BOOT,
    "generic_kernel": RECIPE_GENERIC_KERNEL,
    "generic": RECIPE_GENERIC_KERNEL,
    "linux_generic_loopback": RECIPE_GENERIC_KERNEL,
    "iso_loopback": RECIPE_GENERIC_KERNEL,
}


def get_recipe(identifier: str) -> LinuxBootRecipe:
    """Retrieve recipe by identifier or return generic kernel recipe as fallback."""
    key = identifier.strip().lower()
    return REGISTERED_RECIPES.get(key, RECIPE_GENERIC_KERNEL)


def detect_recipe_from_hints(*hints: Optional[str]) -> LinuxBootRecipe:
    """Infer the most appropriate boot recipe from ISO filename, volume ID, or method string."""
    combined = " ".join(h.lower() for h in hints if h)
    if any(k in combined for k in ("casper", "ubuntu", "mint", "pop-os", "elementary")):
        return RECIPE_CASPER
    if any(k in combined for k in ("archiso", "archlinux", "arch-", "manjaro", "endeavour")):
        return RECIPE_ARCHISO
    if any(k in combined for k in ("dracut", "fedora", "rhel", "centos", "rocky", "alma")):
        return RECIPE_DRACUT
    if any(k in combined for k in ("live-boot", "clonezilla", "debian-live", "kali", "tails")):
        return RECIPE_LIVE_BOOT
    return RECIPE_GENERIC_KERNEL


def generate_loopback_boot_commands(
    recipe: LinuxBootRecipe,
    iso_path: str,
    kernel_path_override: Optional[str] = None,
    initrd_path_override: Optional[str] = None,
    custom_args: Optional[str] = None,
    volume_id: Optional[str] = None,
    loop_dev: str = "loop0",
    data_dev_var: str = "mventx_data_dev",
) -> List[str]:
    """Generate resilient GRUB 2 script commands for loopback mounting and booting a Linux ISO."""
    # Ensure iso_path starts with /
    norm_iso_path = "/" + iso_path.lstrip("/\\")

    k_path = kernel_path_override or recipe.kernel_path
    if not k_path.startswith("/"):
        k_path = "/" + k_path

    i_path = initrd_path_override or recipe.initrd_path
    if not i_path.startswith("/"):
        i_path = "/" + i_path

    k_args = recipe.format_args(norm_iso_path, custom_args=custom_args, volume_id=volume_id)

    commands = [
        f'    if [ -z "${{{data_dev_var}}}" ]; then',
        f'        search --no-floppy --label --set={data_dev_var} MVX_DATA',
        '    fi',
        f'    if [ -z "${{{data_dev_var}}}" ]; then',
        '        echo ""',
        '        echo "=========================================================="',
        '        echo "  [BOOT ERROR] MVX_DATA Storage Partition Not Found"',
        '        echo "=========================================================="',
        '        echo "  Could not locate the partition labeled \'MVX_DATA\'."',
        '        echo "  Please check that the USB drive is connected and valid."',
        '        echo ""',
        '        echo "  Press Enter or ESC to return to the MVentX menu."',
        '        sleep 5',
        '    else',
        f'        set iso_file="(${{{data_dev_var}}}){norm_iso_path}"',
        '        if [ ! -f "${iso_file}" ]; then',
        '            echo ""',
        '            echo "=========================================================="',
        '            echo "  [BOOT ERROR] Registered ISO File Not Found"',
        '            echo "=========================================================="',
        '            echo "  Target path: ${iso_file}"',
        '            echo "  The registered ISO file does not exist on storage."',
        '            echo "  Use the MVentX Manager to verify registered files."',
        '            echo ""',
        '            echo "  Press Enter or ESC to return to the MVentX menu."',
        '            sleep 5',
        '        else',
        f'            loopback -d {loop_dev}',
        f'            echo "Attaching loop device {loop_dev} to ${{iso_file}}..."',
        f'            loopback {loop_dev} "${{iso_file}}"',
        f'            if [ ! -f "({loop_dev}){k_path}" ]; then',
        '                echo ""',
        '                echo "=========================================================="',
        '                echo "  [BOOT ERROR] Kernel Binary Not Found Inside ISO"',
        '                echo "=========================================================="',
        f'                echo "  Searched path: ({loop_dev}){k_path}"',
        f'                echo "  Recipe:        {recipe.name}"',
        '                echo "  Verify or override kernel_path in environment settings."',
        '                echo ""',
        '                echo "  Press Enter or ESC to return to the MVentX menu."',
        f'                loopback -d {loop_dev}',
        '                sleep 5',
        f'            elif [ ! -f "({loop_dev}){i_path}" ]; then',
        '                echo ""',
        '                echo "=========================================================="',
        '                echo "  [BOOT ERROR] Initramfs Archive Not Found Inside ISO"',
        '                echo "=========================================================="',
        f'                echo "  Searched path: ({loop_dev}){i_path}"',
        f'                echo "  Recipe:        {recipe.name}"',
        '                echo "  Verify or override initrd_path in environment settings."',
        '                echo ""',
        '                echo "  Press Enter or ESC to return to the MVentX menu."',
        f'                loopback -d {loop_dev}',
        '                sleep 5',
        '            else',
        f'                echo "Loading Linux kernel: ({loop_dev}){k_path}..."',
        f'                linux ({loop_dev}){k_path} {k_args}',
        f'                echo "Loading initramfs: ({loop_dev}){i_path}..."',
        f'                initrd ({loop_dev}){i_path}',
        '                echo "Booting kernel..."',
        '                boot',
        '            fi',
        '        fi',
        '    fi',
    ]
    return commands

