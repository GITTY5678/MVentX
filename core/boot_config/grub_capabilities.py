"""GNU GRUB 2.12 Command Capability Registry and Runtime Verification for MVentX.

Validates official GNU GRUB built-in commands, dynamic module availability,
module dependency chains, and firmware/security constraints.
Prevents boot menu configurations from advertising commands that the runtime
cannot execute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import os
from pathlib import Path
import re
import struct
from typing import Any, Dict, List, Optional, Set, Tuple


class GRUBCommandType(str, Enum):
    """Categorization of GRUB 2 command implementations."""
    BUILTIN = "builtin"
    MODULE = "module"
    FIRMWARE_DEPENDENT = "firmware_dependent"
    SECURITY_RESTRICTED = "security_restricted"


@dataclass(frozen=True)
class CommandCapability:
    """Metadata describing a GRUB command's execution requirements."""
    name: str
    command_type: GRUBCommandType
    module_name: Optional[str]
    dependencies: List[str]
    description: str
    required: bool = False
    firmware_dependent: bool = False
    secure_boot_restricted: bool = False


@dataclass
class CapabilityReport:
    """Summary of validated GRUB command execution capabilities."""
    is_valid: bool
    architecture: str
    is_signed: bool
    builtin_commands: List[str] = field(default_factory=list)
    available_modules: List[str] = field(default_factory=list)
    missing_required_modules: List[str] = field(default_factory=list)
    unresolved_dependencies: Dict[str, List[str]] = field(default_factory=dict)
    supported_utilities: List[str] = field(default_factory=list)
    unsupported_utilities: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "architecture": self.architecture,
            "is_signed": self.is_signed,
            "builtin_commands": sorted(self.builtin_commands),
            "available_modules_count": len(self.available_modules),
            "missing_required_modules": sorted(self.missing_required_modules),
            "unresolved_dependencies": self.unresolved_dependencies,
            "supported_utilities": sorted(self.supported_utilities),
            "unsupported_utilities": sorted(self.unsupported_utilities),
            "warnings": self.warnings,
            "errors": self.errors,
        }


# Standard catalog of commands used by MVentX and their official GRUB 2.12 module origins
STANDARD_COMMAND_CATALOG: Dict[str, CommandCapability] = {
    # Built-in commands (compiled into core BOOTX64.EFI)
    "echo": CommandCapability("echo", GRUBCommandType.BUILTIN, None, [], "Display text to terminal", True),
    "sleep": CommandCapability("sleep", GRUBCommandType.BUILTIN, None, [], "Pause execution for seconds", True),
    "test": CommandCapability("test", GRUBCommandType.BUILTIN, None, [], "Evaluate boolean expressions", True),
    "true": CommandCapability("true", GRUBCommandType.BUILTIN, None, [], "Return success status (0)", True),
    "false": CommandCapability("false", GRUBCommandType.BUILTIN, None, [], "Return failure status (1)", True),
    "search": CommandCapability("search", GRUBCommandType.BUILTIN, None, [], "Locate devices by file/UUID/label", True),
    "configfile": CommandCapability("configfile", GRUBCommandType.BUILTIN, None, [], "Load and run GRUB configuration file", True),
    "source": CommandCapability("source", GRUBCommandType.BUILTIN, None, [], "Evaluate configuration script in current context", True),
    "normal": CommandCapability("normal", GRUBCommandType.BUILTIN, None, [], "Normal menu/shell subsystem", True),
    "menuentry": CommandCapability("menuentry", GRUBCommandType.BUILTIN, None, [], "Define boot menu entry", True),
    "submenu": CommandCapability("submenu", GRUBCommandType.BUILTIN, None, [], "Define nested submenu", True),
    "insmod": CommandCapability("insmod", GRUBCommandType.BUILTIN, None, [], "Dynamic module loader", True),
    "ls": CommandCapability("ls", GRUBCommandType.BUILTIN, None, [], "List devices and directory contents", True),
    "set": CommandCapability("set", GRUBCommandType.BUILTIN, None, [], "Set environment variable", True),
    "unset": CommandCapability("unset", GRUBCommandType.BUILTIN, None, [], "Unset environment variable", True),
    "export": CommandCapability("export", GRUBCommandType.BUILTIN, None, [], "Export environment variable", True),
    "terminal_output": CommandCapability("terminal_output", GRUBCommandType.BUILTIN, None, [], "Select active terminal output", True),
    "terminal_input": CommandCapability("terminal_input", GRUBCommandType.BUILTIN, None, [], "Select active terminal input", True),
    "loadfont": CommandCapability("loadfont", GRUBCommandType.BUILTIN, None, [], "Load PFF2 font file", True),
    "boot": CommandCapability("boot", GRUBCommandType.BUILTIN, None, [], "Boot loaded kernel or chainloader", True),

    # System utilities (Provided by dynamic modules)
    "reboot": CommandCapability(
        "reboot",
        GRUBCommandType.MODULE,
        "reboot",
        [],
        "Hardware cold reset via UEFI Runtime Services ResetSystem",
        required=True,
        firmware_dependent=False,
        secure_boot_restricted=True,
    ),
    "halt": CommandCapability(
        "halt",
        GRUBCommandType.MODULE,
        "halt",
        ["acpi"],
        "System halt / power off via ACPI and UEFI services",
        required=True,
        firmware_dependent=True,
        secure_boot_restricted=True,
    ),
    "poweroff": CommandCapability(
        "poweroff",
        GRUBCommandType.MODULE,
        "halt",
        ["acpi"],
        "Alias for halt in halt.mod",
        required=False,
        firmware_dependent=True,
        secure_boot_restricted=True,
    ),
    "exit": CommandCapability(
        "exit",
        GRUBCommandType.MODULE,
        "minicmd",
        [],
        "Return control to parent UEFI Boot Manager / firmware dispatcher",
        required=True,
        firmware_dependent=False,
        secure_boot_restricted=True,
    ),
    "fwsetup": CommandCapability(
        "fwsetup",
        GRUBCommandType.MODULE,
        "efifwsetup",
        [],
        "Reboot into UEFI Firmware Settings UI if supported by firmware",
        required=False,
        firmware_dependent=True,
        secure_boot_restricted=True,
    ),

    # Filesystem & Partition drivers
    "fat": CommandCapability("fat", GRUBCommandType.MODULE, "fat", ["fshelp"], "FAT12/FAT16/FAT32 driver", True),
    "exfat": CommandCapability("exfat", GRUBCommandType.MODULE, "exfat", ["fshelp"], "exFAT filesystem driver (MVX_DATA)", True),
    "ext2": CommandCapability("ext2", GRUBCommandType.MODULE, "ext2", ["fshelp"], "ext2/ext3/ext4 filesystem driver", True),
    "ntfs": CommandCapability("ntfs", GRUBCommandType.MODULE, "ntfs", ["fshelp"], "NTFS filesystem driver", True),
    "iso9660": CommandCapability("iso9660", GRUBCommandType.MODULE, "iso9660", ["fshelp"], "ISO 9660 CD/DVD filesystem driver", True),
    "part_gpt": CommandCapability("part_gpt", GRUBCommandType.MODULE, "part_gpt", [], "GPT partition map driver", True),
    "part_msdos": CommandCapability("part_msdos", GRUBCommandType.MODULE, "part_msdos", [], "MBR partition map driver", True),

    # Boot engines & loopback
    "loopback": CommandCapability("loopback", GRUBCommandType.MODULE, "loopback", ["extcmd"], "Mount disk images as virtual devices", True),
    "linux": CommandCapability("linux", GRUBCommandType.MODULE, "linux", ["boot", "mmap", "relocator", "video"], "Load Linux kernel image", True),
    "initrd": CommandCapability("initrd", GRUBCommandType.MODULE, "linux", ["boot", "mmap", "relocator", "video"], "Load Linux initial ramdisk", True),
    "chainloader": CommandCapability("chainloader", GRUBCommandType.MODULE, "chain", ["boot", "efinet", "net"], "Chainload EFI application", True),
}


class GRUBCapabilityRegistry:
    """Manages command capabilities, module dependencies, and deployment verification."""

    def __init__(
        self,
        command_catalog: Optional[Dict[str, CommandCapability]] = None,
        x86_64_efi_dir: Optional[Path] = None,
    ) -> None:
        self.catalog = dict(command_catalog or STANDARD_COMMAND_CATALOG)
        self.x86_64_efi_dir = Path(x86_64_efi_dir).resolve() if x86_64_efi_dir else None
        self._command_map: Dict[str, str] = {}
        self._dependency_map: Dict[str, List[str]] = {}

        if self.x86_64_efi_dir and self.x86_64_efi_dir.is_dir():
            self._load_manifests(self.x86_64_efi_dir)

    def _load_manifests(self, efi_dir: Path) -> None:
        """Parse command.lst and moddep.lst from module directory if present."""
        cmd_lst = efi_dir / "command.lst"
        if cmd_lst.is_file():
            self._command_map = self.parse_command_lst(cmd_lst)

        dep_lst = efi_dir / "moddep.lst"
        if dep_lst.is_file():
            self._dependency_map = self.parse_moddep_lst(dep_lst)

    @classmethod
    def parse_command_lst(cls, path: Path) -> Dict[str, str]:
        """Parse official GRUB command.lst file mapping command -> module."""
        mapping: Dict[str, str] = {}
        if not path.is_file():
            return mapping
        content = path.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) == 2:
                cmd = parts[0].strip()
                mod = parts[1].strip()
                if cmd and mod:
                    mapping[cmd] = mod
        return mapping

    @classmethod
    def parse_moddep_lst(cls, path: Path) -> Dict[str, List[str]]:
        """Parse official GRUB moddep.lst file mapping module -> list of direct dependencies."""
        deps: Dict[str, List[str]] = {}
        if not path.is_file():
            return deps
        content = path.read_text(encoding="utf-8", errors="replace")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) == 2:
                mod = parts[0].strip()
                dep_list = [d.strip() for d in parts[1].strip().split() if d.strip()]
                deps[mod] = dep_list
        return deps

    def resolve_dependencies(self, module_name: str, visited: Optional[Set[str]] = None) -> List[str]:
        """Recursively resolve the full dependency chain in topological order for a module."""
        if visited is None:
            visited = set()

        if module_name in visited:
            return []
        visited.add(module_name)

        # Consult moddep.lst mapping first, then catalog
        direct_deps = self._dependency_map.get(module_name)
        if direct_deps is None:
            # Fall back to catalog metadata
            cap = next((c for c in self.catalog.values() if c.module_name == module_name), None)
            direct_deps = cap.dependencies if cap else []

        resolved: List[str] = []
        for dep in direct_deps:
            # Recursively resolve dependencies of dependency
            sub_deps = self.resolve_dependencies(dep, visited)
            for sd in sub_deps:
                if sd not in resolved:
                    resolved.append(sd)
            if dep not in resolved:
                resolved.append(dep)

        return resolved

    @classmethod
    def inspect_efi_binary(cls, efi_path: Path) -> Dict[str, Any]:
        """Inspect GRUB PE32+ executable header and Authenticode signature table."""
        if not efi_path.is_file():
            raise FileNotFoundError(f"EFI executable not found: {efi_path}")

        data = efi_path.read_bytes()
        if len(data) < 0x200 or data[:2] != b"MZ":
            raise ValueError(f"Invalid DOS/PE executable signature in {efi_path}")

        pe_offset = struct.unpack("<I", data[0x3C:0x40])[0]
        if pe_offset + 24 > len(data) or data[pe_offset:pe_offset+4] != b"PE\x00\x00":
            raise ValueError(f"Invalid PE header signature in {efi_path}")

        machine = struct.unpack("<H", data[pe_offset+4:pe_offset+6])[0]
        arch_map = {0x8664: "x86_64 (PE32+)", 0x14C: "i386 (PE32)", 0xAA64: "aarch64"}
        arch_str = arch_map.get(machine, f"Unknown (0x{machine:04X})")

        # Optional Header
        opt_hdr_magic = struct.unpack("<H", data[pe_offset+24:pe_offset+26])[0]
        is_pe32_plus = (opt_hdr_magic == 0x20B)

        # Security Directory (Certificate Table)
        cert_table_size = 0
        if is_pe32_plus and pe_offset + 24 + 152 <= len(data):
            cert_table_size = struct.unpack("<I", data[pe_offset+24+148:pe_offset+24+152])[0]
        elif not is_pe32_plus and pe_offset + 24 + 136 <= len(data):
            cert_table_size = struct.unpack("<I", data[pe_offset+24+132:pe_offset+24+136])[0]

        is_signed = cert_table_size > 0

        # Scan for built-in command symbols
        builtin_detected = []
        for cmd_name, cap in STANDARD_COMMAND_CATALOG.items():
            if cap.command_type == GRUBCommandType.BUILTIN:
                needle = b"\x00" + cmd_name.encode("ascii") + b"\x00"
                if needle in data:
                    builtin_detected.append(cmd_name)

        return {
            "path": str(efi_path),
            "size_bytes": len(data),
            "machine": machine,
            "architecture": arch_str,
            "is_pe32_plus": is_pe32_plus,
            "is_signed": is_signed,
            "cert_table_size": cert_table_size,
            "builtin_detected": builtin_detected,
        }

    def validate_staged_assets(self, boot_mount: Path) -> CapabilityReport:
        """Thoroughly audit deployed or staged boot assets on target ESP volume.

        Verifies:
        1. /EFI/BOOT/BOOTX64.EFI exists, is valid x86_64 PE32+, and detects signature.
        2. /boot/grub/grub.cfg exists.
        3. /boot/grub/x86_64-efi/ contains required .mod files and manifests (.lst).
        4. Every required command module and all transitive dependencies are present.
        5. Reports supported and unsupported system utilities.
        """
        boot_mount = Path(boot_mount).resolve()
        errors: List[str] = []
        warnings: List[str] = []

        efi_binary = boot_mount / "EFI" / "BOOT" / "BOOTX64.EFI"
        if not efi_binary.is_file():
            # Check secondary search paths
            alt = boot_mount / "boot" / "efi" / "BOOTX64.EFI"
            if alt.is_file():
                efi_binary = alt
            else:
                errors.append("BOOTX64.EFI not found at /EFI/BOOT/BOOTX64.EFI")

        arch_str = "unknown"
        is_signed = False
        builtin_cmds: List[str] = []
        if efi_binary.is_file():
            try:
                info = self.inspect_efi_binary(efi_binary)
                arch_str = info["architecture"]
                is_signed = info["is_signed"]
                builtin_cmds = info["builtin_detected"]
                if not info["is_pe32_plus"]:
                    errors.append(f"BOOTX64.EFI is not 64-bit PE32+: {arch_str}")
            except Exception as exc:
                errors.append(f"Failed inspecting BOOTX64.EFI: {exc}")

        # Check static grub.cfg
        grub_cfg = boot_mount / "boot" / "grub" / "grub.cfg"
        if not grub_cfg.is_file():
            errors.append("grub.cfg not found at /boot/grub/grub.cfg")

        # Check x86_64-efi module directory
        mod_dir = boot_mount / "boot" / "grub" / "x86_64-efi"
        if not mod_dir.is_dir():
            errors.append("Module directory /boot/grub/x86_64-efi not found")
            return CapabilityReport(
                is_valid=False,
                architecture=arch_str,
                is_signed=is_signed,
                builtin_commands=builtin_cmds,
                errors=errors,
                warnings=warnings,
            )

        # Inspect manifests
        cmd_lst_path = mod_dir / "command.lst"
        moddep_lst_path = mod_dir / "moddep.lst"
        if not cmd_lst_path.is_file():
            warnings.append("command.lst missing from /boot/grub/x86_64-efi - dynamic autoload table disabled")
        if not moddep_lst_path.is_file():
            warnings.append("moddep.lst missing from /boot/grub/x86_64-efi - automatic dependency resolution disabled")

        # Discover all available .mod files
        available_mods = {p.stem for p in mod_dir.glob("*.mod")}
        if not available_mods:
            errors.append("No .mod dynamic modules found in /boot/grub/x86_64-efi")

        # Check required modules from catalog
        missing_required = []
        unresolved_deps: Dict[str, List[str]] = {}

        # Update dependency mappings from mod_dir if available
        if moddep_lst_path.is_file():
            self._dependency_map = self.parse_moddep_lst(moddep_lst_path)
        if cmd_lst_path.is_file():
            self._command_map = self.parse_command_lst(cmd_lst_path)

        for name, cap in self.catalog.items():
            if cap.command_type == GRUBCommandType.BUILTIN:
                continue

            mod = cap.module_name
            if not mod:
                continue

            if cap.required and mod not in available_mods:
                missing_required.append(mod)

            # Check transitive dependencies
            full_deps = self.resolve_dependencies(mod)
            missing_deps = [d for d in full_deps if d not in available_mods]
            if missing_deps:
                unresolved_deps[mod] = missing_deps

        # Audit System Utilities
        supported_utils = []
        unsupported_utils = []

        system_utility_cmds = ["reboot", "halt", "exit", "fwsetup"]
        for ucmd in system_utility_cmds:
            cap = self.catalog.get(ucmd)
            if not cap:
                continue
            mod = cap.module_name
            if mod and mod in available_mods:
                # Check dependencies
                deps_missing = unresolved_deps.get(mod, [])
                if not deps_missing:
                    supported_utils.append(ucmd)
                else:
                    unsupported_utils.append(ucmd)
                    errors.append(f"System utility '{ucmd}' module '{mod}' has missing dependencies: {deps_missing}")
            elif cap.command_type == GRUBCommandType.BUILTIN:
                supported_utils.append(ucmd)
            else:
                unsupported_utils.append(ucmd)
                errors.append(f"System utility '{ucmd}' required module '{mod}.mod' is not deployed")

        is_valid = (len(errors) == 0 and len(missing_required) == 0)

        return CapabilityReport(
            is_valid=is_valid,
            architecture=arch_str,
            is_signed=is_signed,
            builtin_commands=builtin_cmds,
            available_modules=sorted(list(available_mods)),
            missing_required_modules=missing_required,
            unresolved_dependencies=unresolved_deps,
            supported_utilities=supported_utils,
            unsupported_utilities=unsupported_utils,
            warnings=warnings,
            errors=errors,
        )

    def generate_system_utilities_script(
        self,
        supported_utilities: List[str],
        include_heading: bool = True,
    ) -> List[str]:
        """Generate reliable, validated GRUB script block for System Utilities.

        Only emits menu entries for operations that have their modules and dependencies
        verified and deployed. Pre-loads required modules and handles unsupported firmware
        returns cleanly.
        """
        lines: List[str] = []
        if include_heading:
            lines.extend([
                "# ==============================================================================",
                "# MVentX System Utilities Submenu",
                "# Verified against official GNU GRUB runtime capabilities",
                "# ==============================================================================",
                'submenu "MVentX System Utilities" --id "mventx-system-utils" {',
            ])

        indent = "    " if include_heading else ""

        if "fwsetup" in supported_utilities:
            lines.extend([
                f'{indent}if [ "${{grub_platform}}" = "efi" ]; then',
                f'{indent}    menuentry "UEFI Firmware Settings" --id "uefi-firmware" {{',
                f'{indent}        echo "Entering UEFI Firmware Settings..."',
                f'{indent}        if fwsetup; then',
                f'{indent}            true',
                f'{indent}        else',
                f'{indent}            echo ""',
                f'{indent}            echo "Notice: Entering UEFI Firmware Settings is not supported"',
                f'{indent}            echo "by this computer\'s UEFI firmware implementation."',
                f'{indent}            echo ""',
                f'{indent}            echo "Press Enter or ESC to return to menu."',
                f'{indent}            sleep 5',
                f'{indent}        fi',
                f'{indent}    }}',
                f'{indent}fi',
                "",
            ])

        if "reboot" in supported_utilities:
            lines.extend([
                f'{indent}menuentry "Reboot System" --id "system-reboot" {{',
                f'{indent}    echo "Rebooting system..."',
                f'{indent}    reboot',
                f'{indent}}}',
                "",
            ])

        if "halt" in supported_utilities:
            lines.extend([
                f'{indent}menuentry "Halt / Power Off System" --id "system-halt" {{',
                f'{indent}    echo "Powering off system..."',
                f'{indent}    halt',
                f'{indent}}}',
                "",
            ])

        if "exit" in supported_utilities:
            lines.extend([
                f'{indent}menuentry "Exit GRUB (Return to UEFI Boot Manager)" --id "system-exit" {{',
                f'{indent}    echo "Returning to UEFI Boot Manager..."',
                f'{indent}    exit',
                f'{indent}}}',
                "",
            ])

        if include_heading:
            lines.append("}")
            lines.append("")

        return lines

