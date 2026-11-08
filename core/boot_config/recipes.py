"""Linux Boot Recipes and Loopback Command Generator for MVentX.

Encapsulates distribution-specific live ISO boot parameters, kernel paths,
initramfs locations, and command-line argument generators for GRUB 2 loopback execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Union


def infer_alpine_modloop_path(
    kernel_path: Optional[str] = None,
    initrd_path: Optional[str] = None,
) -> str:
    """Infer the matching Alpine /boot/modloop-<flavour> path from kernel or initramfs path."""
    for candidate in (kernel_path, initrd_path):
        if not candidate:
            continue
        norm = candidate.strip().replace("\\", "/")
        basename = norm.rsplit("/", 1)[-1]
        m = re.match(r"^(?:vmlinuz|initramfs|initrd)[-_]([a-zA-Z0-9._-]+)$", basename, re.IGNORECASE)
        if m:
            flavour = m.group(1)
            prefix_dir = norm.rsplit("/", 1)[0] if "/" in norm else "/boot"
            if not prefix_dir.startswith("/"):
                prefix_dir = "/" + prefix_dir
            return f"{prefix_dir}/modloop-{flavour}"
    return "/boot/modloop-lts"


def infer_dracut_volume_id(
    iso_path: str,
    volume_id: Optional[str] = None,
) -> str:
    """Infer the ISO Volume ID (CDLABEL) for Dracut live loopback booting."""
    if volume_id and volume_id.strip() and volume_id.strip() != "MVX_DATA":
        return volume_id.strip()

    # Try reading directly from the ISO file if it exists on disk
    try:
        norm = iso_path.strip().replace("\\", "/")
        for cand in (norm, norm.lstrip("/"), f"F:/{norm.lstrip('/')}", f"F:{norm}"):
            p = Path(cand)
            if p.is_file() and p.stat().st_size >= 32768 + 2048:
                with open(p, "rb") as f:
                    f.seek(16 * 2048)
                    pvd = f.read(2048)
                    if len(pvd) >= 72 and pvd[1:6] == b"CD001":
                        vol_raw = pvd[40:72].decode("latin-1", errors="replace").strip()
                        if vol_raw:
                            return vol_raw
                break
    except Exception:
        pass

    # Infer from ISO filename
    basename = iso_path.strip().replace("\\", "/").rsplit("/", 1)[-1]
    m_fed = re.search(r"Fedora-([A-Za-z0-9]+).*?-(\d{2})(?:[-.]|$)", basename, re.IGNORECASE)
    if m_fed:
        ed, ver = m_fed.group(1), m_fed.group(2)
        ed_abbr = {"workstation": "WS", "server": "S", "silverblue": "SB"}.get(ed.lower(), ed)
        return f"Fedora-{ed_abbr}-Live-{ver}"

    return volume_id.strip() if (volume_id and volume_id.strip()) else "Fedora-WS-Live-45"


@dataclass(frozen=True)
class LinuxBootRecipe:
    """Encapsulates kernel, initrd, and command-line parameters for a distribution family."""
    recipe_id: str
    name: str
    kernel_path: str
    initrd_path: str
    default_args: str
    description: str

    def format_args(
        self,
        iso_path: str,
        custom_args: Optional[str] = None,
        volume_id: Optional[str] = None,
        kernel_path: Optional[str] = None,
        initrd_path: Optional[str] = None,
    ) -> str:
        """Format the kernel command-line arguments string."""
        vol = volume_id or "MVX_DATA"
        # Quote iso_path if it contains spaces so Linux kernel cmdline does not split arguments
        safe_iso_path = f'"{iso_path}"' if " " in iso_path and not iso_path.startswith('"') else iso_path
        modloop_path = infer_alpine_modloop_path(
            kernel_path or self.kernel_path,
            initrd_path or self.initrd_path,
        )
        iso_vol_id = infer_dracut_volume_id(iso_path, volume_id)
        args = (
            self.default_args
            .replace("${iso_path}", safe_iso_path)
            .replace("${vol_id}", vol)
            .replace("${iso_vol_id}", iso_vol_id)
            .replace("${modloop_path}", modloop_path)
        )
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
    description="Ubuntu, Linux Mint, Pop!_OS, Elementary OS, Zorin OS, and derivatives using Casper initramfs",
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
    default_args="root=live:CDLABEL=${iso_vol_id} iso-scan/filename=${iso_path} rd.live.image rd.driver.pre=exfat quiet",
    description="Fedora, CentOS, RHEL, Rocky Linux, AlmaLinux, and derivatives using Dracut live module",
)

RECIPE_LIVE_BOOT = LinuxBootRecipe(
    recipe_id="live_boot",
    name="Debian / Clonezilla Live-Boot",
    kernel_path="/live/vmlinuz",
    initrd_path="/live/initrd.img",
    default_args="boot=live findiso=${iso_path} union=overlay config components quiet",
    description="Debian Live, Clonezilla, Kali Linux, Rescuezilla, and distributions using Debian live-boot",
)

RECIPE_OPENSUSE = LinuxBootRecipe(
    recipe_id="opensuse",
    name="openSUSE Leap / Tumbleweed Live",
    kernel_path="/boot/x86_64/loader/linux",
    initrd_path="/boot/x86_64/loader/initrd",
    default_args="root=live:CDLABEL=${iso_vol_id} iso-scan/filename=${iso_path} rd.live.image splash=silent quiet",
    description="openSUSE Leap and Tumbleweed Live images using Kiwi/Dracut initramfs",
)

RECIPE_MX_LINUX = LinuxBootRecipe(
    recipe_id="mx_linux",
    name="MX Linux / antiX Live",
    kernel_path="/antiX/vmlinuz",
    initrd_path="/antiX/initrd.gz",
    default_args="fromiso=${iso_path} bootdev=MVX_DATA quiet splash",
    description="MX Linux and antiX Live distributions using linux-live bootloader",
)

RECIPE_ALPINE = LinuxBootRecipe(
    recipe_id="alpine",
    name="Alpine Linux Virt / Standard",
    kernel_path="/boot/vmlinuz-lts",
    initrd_path="/boot/initramfs-lts",
    default_args="iso-scan/filename=${iso_path} modules=loop,squashfs,sd-mod,usb-storage,exfat modloop=${modloop_path} quiet",
    description="Alpine Linux installation and live environments using modloop",
)

RECIPE_SYSTEMRESCUE = LinuxBootRecipe(
    recipe_id="systemrescue",
    name="SystemRescue Live (ArchISO)",
    kernel_path="/sysrescue/boot/x86_64/vmlinuz",
    initrd_path="/sysrescue/boot/x86_64/sysrescue.img",
    default_args="img_dev=/dev/disk/by-label/MVX_DATA img_loop=${iso_path} archisobasedir=sysrescue earlymodules=loop copytoram=n",
    description="SystemRescue rescue environment (v6.x+ based on ArchISO)",
)

RECIPE_GENERIC_KERNEL = LinuxBootRecipe(
    recipe_id="generic_kernel",
    name="Generic Linux Kernel Loopback",
    kernel_path="/linux",
    initrd_path="/initrd.gz",
    default_args="quiet",
    description="Generic Linux netboot or custom kernel/initrd configurations",
)

REGISTERED_RECIPES: Dict[str, LinuxBootRecipe] = {
    # Casper family
    "casper": RECIPE_CASPER,
    "ubuntu": RECIPE_CASPER,
    "mint": RECIPE_CASPER,
    "pop": RECIPE_CASPER,
    "elementary": RECIPE_CASPER,
    "zorin": RECIPE_CASPER,
    "linux_casper": RECIPE_CASPER,
    # ArchISO family
    "archiso": RECIPE_ARCHISO,
    "arch": RECIPE_ARCHISO,
    "manjaro": RECIPE_ARCHISO,
    "endeavour": RECIPE_ARCHISO,
    "endeavouros": RECIPE_ARCHISO,
    "linux_archiso": RECIPE_ARCHISO,
    # Dracut family
    "dracut": RECIPE_DRACUT,
    "fedora": RECIPE_DRACUT,
    "rhel": RECIPE_DRACUT,
    "centos": RECIPE_DRACUT,
    "rocky": RECIPE_DRACUT,
    "alma": RECIPE_DRACUT,
    "almalinux": RECIPE_DRACUT,
    "linux_dracut": RECIPE_DRACUT,
    # Debian live-boot family
    "live_boot": RECIPE_LIVE_BOOT,
    "debian": RECIPE_LIVE_BOOT,
    "clonezilla": RECIPE_LIVE_BOOT,
    "kali": RECIPE_LIVE_BOOT,
    "rescuezilla": RECIPE_LIVE_BOOT,
    "tails": RECIPE_LIVE_BOOT,
    "linux_live_boot": RECIPE_LIVE_BOOT,
    # openSUSE
    "opensuse": RECIPE_OPENSUSE,
    "suse": RECIPE_OPENSUSE,
    "tumbleweed": RECIPE_OPENSUSE,
    "leap": RECIPE_OPENSUSE,
    "linux_opensuse": RECIPE_OPENSUSE,
    # MX Linux / antiX
    "mx_linux": RECIPE_MX_LINUX,
    "mx": RECIPE_MX_LINUX,
    "antix": RECIPE_MX_LINUX,
    "linux_mx": RECIPE_MX_LINUX,
    # Alpine Linux
    "alpine": RECIPE_ALPINE,
    "alpine_linux": RECIPE_ALPINE,
    "linux_alpine": RECIPE_ALPINE,
    # SystemRescue
    "systemrescue": RECIPE_SYSTEMRESCUE,
    "sysrescue": RECIPE_SYSTEMRESCUE,
    "linux_systemrescue": RECIPE_SYSTEMRESCUE,
    # Compatibility aliases
    "arch_loopback": RECIPE_ARCHISO,
    "debian_live_loopback": RECIPE_LIVE_BOOT,
    # Generic fallback
    "generic_kernel": RECIPE_GENERIC_KERNEL,
    "generic": RECIPE_GENERIC_KERNEL,
    "linux_generic_loopback": RECIPE_GENERIC_KERNEL,
    "iso_loopback": RECIPE_GENERIC_KERNEL,
}


@dataclass(frozen=True)
class BootMethodDescriptor:
    """Describes a user-facing boot method choice and its backend recipe mapping."""
    id: str
    display_name: str
    description: str
    recipe_id: str
    env_type: str = "iso"


SUPPORTED_BOOT_METHODS: Dict[str, BootMethodDescriptor] = {
    "casper": BootMethodDescriptor(
        id="casper",
        display_name="Ubuntu / Linux Mint (Casper)",
        description="Live ISO loopback for Ubuntu, Linux Mint, Pop!_OS, and Casper derivatives",
        recipe_id="casper",
        env_type="iso",
    ),
    "archiso": BootMethodDescriptor(
        id="archiso",
        display_name="Arch Linux / Manjaro (ArchISO)",
        description="Live ISO loopback for Arch Linux, Manjaro, and EndeavourOS via ArchISO",
        recipe_id="archiso",
        env_type="iso",
    ),
    "live_boot": BootMethodDescriptor(
        id="live_boot",
        display_name="Debian Live / Clonezilla (live-boot)",
        description="Live ISO loopback for Debian Live, Clonezilla, Kali, and Rescuezilla",
        recipe_id="live_boot",
        env_type="iso",
    ),
    "dracut": BootMethodDescriptor(
        id="dracut",
        display_name="Fedora / RHEL / CentOS (Dracut)",
        description="Live ISO loopback for Fedora, RHEL, CentOS, Rocky, Alma",
        recipe_id="dracut",
        env_type="iso",
    ),
    "opensuse": BootMethodDescriptor(
        id="opensuse",
        display_name="openSUSE Leap / Tumbleweed",
        description="Live ISO loopback for openSUSE Kiwi/Dracut live images",
        recipe_id="opensuse",
        env_type="iso",
    ),
    "mx_linux": BootMethodDescriptor(
        id="mx_linux",
        display_name="MX Linux / antiX (linux-live)",
        description="Live ISO loopback for MX Linux and antiX",
        recipe_id="mx_linux",
        env_type="iso",
    ),
    "alpine": BootMethodDescriptor(
        id="alpine",
        display_name="Alpine Linux (modloop)",
        description="Live ISO loopback for Alpine Linux live/virt images",
        recipe_id="alpine",
        env_type="iso",
    ),
    "systemrescue": BootMethodDescriptor(
        id="systemrescue",
        display_name="SystemRescue (ArchISO derivative)",
        description="Live ISO loopback for SystemRescue toolkit",
        recipe_id="systemrescue",
        env_type="iso",
    ),
    "linux_generic_loopback": BootMethodDescriptor(
        id="linux_generic_loopback",
        display_name="Generic Linux Loopback (generic_kernel)",
        description="Standard generic Linux loopback kernel/initrd loader",
        recipe_id="generic_kernel",
        env_type="iso",
    ),
    "windows_extracted_media": BootMethodDescriptor(
        id="windows_extracted_media",
        display_name="Windows Installation Media (Extracted)",
        description="Direct UEFI chainload of extracted Windows bootmgfw.efi",
        recipe_id="",
        env_type="windows",
    ),
}


def get_supported_boot_methods() -> List[BootMethodDescriptor]:
    """Return ordered list of supported boot methods for GUI and CLI dropdowns."""
    return list(SUPPORTED_BOOT_METHODS.values())


def get_boot_method_descriptor(method_id: str) -> Optional[BootMethodDescriptor]:
    """Retrieve boot method descriptor by identifier or alias."""
    key = method_id.strip().lower()
    if key in SUPPORTED_BOOT_METHODS:
        return SUPPORTED_BOOT_METHODS[key]
    alias_map = {
        "linux_casper": "casper",
        "ubuntu": "casper",
        "mint": "casper",
        "linux_archiso": "archiso",
        "arch_loopback": "archiso",
        "linux_live_boot": "live_boot",
        "debian_live_loopback": "live_boot",
        "linux_dracut": "dracut",
        "generic": "linux_generic_loopback",
        "generic_kernel": "linux_generic_loopback",
    }
    target = alias_map.get(key)
    if target:
        return SUPPORTED_BOOT_METHODS.get(target)
    return None


def get_recipe(identifier: str) -> LinuxBootRecipe:
    """Retrieve recipe by identifier or return generic kernel recipe as fallback."""
    key = identifier.strip().lower()
    return REGISTERED_RECIPES.get(key, RECIPE_GENERIC_KERNEL)


def detect_recipe_from_hints(*hints: Optional[str]) -> LinuxBootRecipe:
    """Infer the most appropriate boot recipe from ISO filename, volume ID, or method string."""
    combined = " ".join(h.lower() for h in hints if h)
    if any(k in combined for k in ("casper", "ubuntu", "mint", "pop-os", "elementary", "zorin")):
        return RECIPE_CASPER
    if any(k in combined for k in ("systemrescue", "sysrescue")):
        return RECIPE_SYSTEMRESCUE
    if any(k in combined for k in ("archiso", "archlinux", "arch-", "manjaro", "endeavour")):
        return RECIPE_ARCHISO
    if any(k in combined for k in ("opensuse", "suse", "tumbleweed", "leap")):
        return RECIPE_OPENSUSE
    if any(k in combined for k in ("mx-", "mx_linux", "antix", "mxlinux")):
        return RECIPE_MX_LINUX
    if any(k in combined for k in ("alpine", "alpine-")):
        return RECIPE_ALPINE
    if any(k in combined for k in ("dracut", "fedora", "rhel", "centos", "rocky", "alma")):
        return RECIPE_DRACUT
    if any(k in combined for k in ("live-boot", "clonezilla", "debian-live", "debian", "kali", "tails", "rescuezilla")):
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
    overlay_path: Optional[str] = None,
    boot_dev_var: str = "mventx_boot_dev",
) -> List[str]:
    """Generate resilient GRUB 2 script commands for loopback mounting and booting a Linux ISO.

    Guarantees that every emitted GRUB command is a single physical line with no embedded
    CR/LF characters.
    """
    # Strip any accidental CR/LF and normalize slashes so commands never split across lines
    clean_iso = iso_path.replace("\r", "").replace("\n", "").strip().replace("\\", "/")
    norm_iso_path = "/" + clean_iso.lstrip("/")

    if overlay_path and overlay_path.strip():
        clean_ov = overlay_path.replace("\r", "").replace("\n", "").strip().replace("\\", "/")
        norm_overlay_path: Optional[str] = "/" + clean_ov.lstrip("/")
    else:
        norm_overlay_path = None

    raw_k_path = (kernel_path_override or recipe.kernel_path).replace("\r", "").replace("\n", "").strip().replace("\\", "/")
    k_path = raw_k_path if raw_k_path.startswith("/") else "/" + raw_k_path

    raw_i_path = (initrd_path_override or recipe.initrd_path).replace("\r", "").replace("\n", "").strip().replace("\\", "/")
    i_path = raw_i_path if raw_i_path.startswith("/") else "/" + raw_i_path

    clean_custom_args = custom_args.replace("\r", " ").replace("\n", " ").strip() if custom_args else None
    k_args = recipe.format_args(
        norm_iso_path,
        custom_args=clean_custom_args,
        volume_id=volume_id,
        kernel_path=k_path,
        initrd_path=i_path,
    )

    commands = [
        f'    if [ -z "${{{data_dev_var}}}" ]; then',
        f'        search --no-floppy --label --set={data_dev_var} MVX_DATA',
        '    fi',
    ]

    if norm_overlay_path:
        commands.extend([
            f'    if [ -z "${{{boot_dev_var}}}" ]; then',
            f'        search --no-floppy --label --set={boot_dev_var} MVX_BOOT',
            '    fi',
        ])

    commands.extend([
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
    ])

    if norm_overlay_path:
        commands.extend([
            f'                if [ -n "${{{boot_dev_var}}}" ]; then',
            f'                    if [ -f "(${{{boot_dev_var}}}){norm_overlay_path}" ]; then',
            f'                        echo "Loading supplementary initramfs overlay: (${{{boot_dev_var}}}){norm_overlay_path}..."',
            f'                        initrd ({loop_dev}){i_path} (${{{boot_dev_var}}}){norm_overlay_path}',
            '                    else',
            f'                        initrd ({loop_dev}){i_path}',
            '                    fi',
            '                else',
            f'                    initrd ({loop_dev}){i_path}',
            '                fi',
        ])
    else:
        commands.append(f'                initrd ({loop_dev}){i_path}')

    commands.extend([
        '                echo "Booting kernel..."',
        '                boot',
        '            fi',
        '        fi',
        '    fi',
    ])
    return [cmd.replace("\r", "").replace("\n", "") for cmd in commands]

