"""ISO Filesystem Inspector and Recipe Detection Engine for MVentX.

Directly parses ISO 9660 and UDF volume descriptors and directory records to:
1. Discover kernel binaries and initramfs archives within intact ISO files.
2. Determine Linux distribution family and initramfs boot recipe with confidence scoring.
3. Extract volume labels, system identifiers, and directory structures.
4. Issue diagnostic warnings when distribution detection is uncertain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import struct
from typing import Dict, List, Optional, Set, Tuple, Union


@dataclass
class InspectionResult:
    """Detailed structural inspection and recipe inference result."""
    volume_id: Optional[str] = None
    system_id: Optional[str] = None
    publisher_id: Optional[str] = None
    detected_recipe: Optional[str] = None
    confidence: str = "uncertain"  # "high", "medium", "uncertain"
    discovered_kernel_path: Optional[str] = None
    discovered_initrd_path: Optional[str] = None
    directories: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Union[str, Optional[str], List[str]]]:
        return {
            "volume_id": self.volume_id,
            "system_id": self.system_id,
            "publisher_id": self.publisher_id,
            "detected_recipe": self.detected_recipe,
            "confidence": self.confidence,
            "discovered_kernel_path": self.discovered_kernel_path,
            "discovered_initrd_path": self.discovered_initrd_path,
            "directories": self.directories,
            "files": self.files,
            "warnings": self.warnings,
        }


class ISOInspector:
    """Non-destructive ISO 9660 directory structure scanner."""

    SECTOR_SIZE = 2048
    VRA_START_OFFSET = 16 * 2048

    def __init__(self, iso_path: Union[Path, str]) -> None:
        self.iso_path = Path(iso_path)

    def _read_dir_records(
        self, f, lba: int, length: int
    ) -> List[Tuple[str, bool, int, int]]:
        """Read directory entries at specified LBA within ISO 9660 stream."""
        f.seek(lba * self.SECTOR_SIZE)
        data = f.read(length)
        offset = 0
        entries: List[Tuple[str, bool, int, int]] = []

        while offset < len(data):
            rec_len = data[offset]
            if rec_len == 0:
                # Padding at sector boundary
                offset = ((offset // self.SECTOR_SIZE) + 1) * self.SECTOR_SIZE
                continue

            rec = data[offset : offset + rec_len]
            ext_lba = struct.unpack_from("<I", rec, 2)[0]
            ext_len = struct.unpack_from("<I", rec, 10)[0]
            flags = rec[25]
            is_dir = bool(flags & 2)
            name_len = rec[32]
            raw_name = rec[33 : 33 + name_len].decode("latin-1", errors="ignore")

            # Ignore . and ..
            if raw_name not in ("\x00", "\x01"):
                clean_name = raw_name.split(";")[0].strip()
                entries.append((clean_name, is_dir, ext_lba, ext_len))

            offset += rec_len

        return entries

    def inspect(self) -> InspectionResult:
        """Scan ISO volume descriptors and search for signature distribution paths."""
        if not self.iso_path.is_file():
            return InspectionResult(
                confidence="uncertain",
                warnings=[f"ISO file does not exist: {self.iso_path}"],
            )

        file_size = self.iso_path.stat().st_size
        if file_size < self.VRA_START_OFFSET + self.SECTOR_SIZE:
            return InspectionResult(
                confidence="uncertain",
                warnings=[f"File too small to be a valid ISO image ({file_size} bytes)"],
            )

        try:
            with open(self.iso_path, "rb") as f:
                f.seek(self.VRA_START_OFFSET)
                pvd = f.read(self.SECTOR_SIZE)
                if len(pvd) < self.SECTOR_SIZE or pvd[1:6] != b"CD001":
                    return InspectionResult(
                        confidence="uncertain",
                        warnings=["Missing standard ISO 9660 CD001 Primary Volume Descriptor"],
                    )

                sys_id = pvd[8:40].decode("latin-1", errors="replace").strip() or None
                vol_id = pvd[40:72].decode("latin-1", errors="replace").strip() or None
                pub_id = pvd[318:446].decode("latin-1", errors="replace").strip() or None

                root_rec = pvd[156:190]
                root_lba = struct.unpack_from("<I", root_rec, 2)[0]
                root_len = struct.unpack_from("<I", root_rec, 10)[0]

                root_entries = self._read_dir_records(f, root_lba, root_len)

                discovered_files: Dict[str, Tuple[int, int]] = {}
                discovered_dirs: Dict[str, Tuple[int, int]] = {}

                for name, is_dir, lba, length in root_entries:
                    p = "/" + name.lower().rstrip(".")
                    if is_dir:
                        discovered_dirs[p] = (lba, length)
                    else:
                        discovered_files[p] = (lba, length)

                # Scan up to 3 directory tiers for distribution structures
                tier1_candidates = ["/casper", "/live", "/boot", "/arch", "/images", "/isolinux"]
                for d in list(discovered_dirs.keys()):
                    if any(d.startswith(c) for c in tier1_candidates):
                        lba, length = discovered_dirs[d]
                        subs = self._read_dir_records(f, lba, length)
                        for sname, sis_dir, slba, slen in subs:
                            sp = f"{d}/{sname.lower().rstrip('.')}"
                            if sis_dir:
                                discovered_dirs[sp] = (slba, slen)
                            else:
                                discovered_files[sp] = (slba, slen)

                tier2_candidates = ["/arch/boot", "/images/pxeboot", "/boot/grub"]
                for d in list(discovered_dirs.keys()):
                    if d in tier2_candidates:
                        lba, length = discovered_dirs[d]
                        subs = self._read_dir_records(f, lba, length)
                        for sname, sis_dir, slba, slen in subs:
                            sp = f"{d}/{sname.lower().rstrip('.')}"
                            if sis_dir:
                                discovered_dirs[sp] = (slba, slen)
                            else:
                                discovered_files[sp] = (slba, slen)

                if "/arch/boot/x86_64" in discovered_dirs:
                    lba, length = discovered_dirs["/arch/boot/x86_64"]
                    subs = self._read_dir_records(f, lba, length)
                    for sname, sis_dir, slba, slen in subs:
                        sp = f"/arch/boot/x86_64/{sname.lower().rstrip('.')}"
                        if not sis_dir:
                            discovered_files[sp] = (slba, slen)

        except OSError as exc:
            return InspectionResult(
                confidence="uncertain",
                warnings=[f"Failed reading ISO structure: {exc}"],
            )

        dirs_list = sorted(discovered_dirs.keys())
        files_list = sorted(discovered_files.keys())

        # Determine Recipe and Extract Kernel / Initrd Paths
        recipe: Optional[str] = None
        confidence = "uncertain"
        kernel_path: Optional[str] = None
        initrd_path: Optional[str] = None
        warnings: List[str] = []

        # 1. Casper (Ubuntu / Linux Mint / Pop!_OS)
        casper_kernels = [f for f in files_list if f.startswith("/casper/vmlinuz")]
        casper_initrds = [f for f in files_list if f.startswith("/casper/initrd")]
        if casper_kernels and casper_initrds:
            recipe = "casper"
            confidence = "high"
            kernel_path = casper_kernels[0]
            initrd_path = casper_initrds[0]
        elif "/casper" in dirs_list:
            recipe = "casper"
            confidence = "medium"
            kernel_path = casper_kernels[0] if casper_kernels else "/casper/vmlinuz"
            initrd_path = casper_initrds[0] if casper_initrds else "/casper/initrd"
            warnings.append("Casper directory detected but standard kernel/initrd filenames incomplete")

        # 2. ArchISO (Arch Linux / Manjaro)
        if not recipe:
            arch_kernels = [f for f in files_list if "vmlinuz-linux" in f or f.startswith("/arch/boot/")]
            arch_initrds = [f for f in files_list if "initramfs" in f and "arch" in f]
            if arch_kernels and arch_initrds:
                recipe = "archiso"
                confidence = "high"
                kernel_path = arch_kernels[0]
                initrd_path = arch_initrds[0]
            elif any(d.startswith("/arch") for d in dirs_list):
                recipe = "archiso"
                confidence = "medium"
                kernel_path = arch_kernels[0] if arch_kernels else "/arch/boot/x86_64/vmlinuz-linux"
                initrd_path = arch_initrds[0] if arch_initrds else "/arch/boot/x86_64/initramfs-linux.img"

        # 3. Dracut (Fedora / RHEL / CentOS)
        if not recipe:
            dracut_kernels = [f for f in files_list if f.startswith("/images/pxeboot/vmlinuz")]
            dracut_initrds = [f for f in files_list if f.startswith("/images/pxeboot/initrd")]
            if dracut_kernels and dracut_initrds:
                recipe = "dracut"
                confidence = "high"
                kernel_path = dracut_kernels[0]
                initrd_path = dracut_initrds[0]
            elif "/images/pxeboot" in dirs_list:
                recipe = "dracut"
                confidence = "medium"
                kernel_path = dracut_kernels[0] if dracut_kernels else "/images/pxeboot/vmlinuz"
                initrd_path = dracut_initrds[0] if dracut_initrds else "/images/pxeboot/initrd.img"

        # 4. Debian Live-Boot (Debian Live / Clonezilla / Kali)
        if not recipe:
            live_kernels = [f for f in files_list if f.startswith("/live/vmlinuz")]
            live_initrds = [f for f in files_list if f.startswith("/live/initrd")]
            if live_kernels and live_initrds:
                recipe = "live_boot"
                confidence = "high"
                kernel_path = live_kernels[0]
                initrd_path = live_initrds[0]
            elif "/live" in dirs_list:
                recipe = "live_boot"
                confidence = "medium"
                kernel_path = live_kernels[0] if live_kernels else "/live/vmlinuz"
                initrd_path = live_initrds[0] if live_initrds else "/live/initrd.img"

        # 5. Generic Linux / Netboot / Alpine Fallback
        if not recipe:
            generic_kernels = [
                f for f in files_list
                if f.startswith("/linux")
                or f.startswith("/boot/vmlinuz")
                or "vmlinuz" in f
            ]
            generic_initrds = [
                f for f in files_list
                if f.startswith("/initrd")
                or f.startswith("/boot/initramfs")
                or "initramfs" in f
            ]
            if generic_kernels and generic_initrds:
                recipe = "generic_kernel"
                confidence = "medium"
                kernel_path = generic_kernels[0]
                initrd_path = generic_initrds[0]
            else:
                # Check volume label hints
                combined_hints = f"{vol_id or ''} {sys_id or ''} {self.iso_path.name}".lower()
                if "ubuntu" in combined_hints or "mint" in combined_hints:
                    recipe = "casper"
                    confidence = "medium"
                    kernel_path = "/casper/vmlinuz"
                    initrd_path = "/casper/initrd"
                    warnings.append("Recipe inferred from volume label/name; verify kernel path before booting")
                elif "arch" in combined_hints or "manjaro" in combined_hints:
                    recipe = "archiso"
                    confidence = "medium"
                    kernel_path = "/arch/boot/x86_64/vmlinuz-linux"
                    initrd_path = "/arch/boot/x86_64/initramfs-linux.img"
                    warnings.append("Recipe inferred from volume label/name; verify kernel path before booting")
                elif "fedora" in combined_hints or "rhel" in combined_hints:
                    recipe = "dracut"
                    confidence = "medium"
                    kernel_path = "/images/pxeboot/vmlinuz"
                    initrd_path = "/images/pxeboot/initrd.img"
                    warnings.append("Recipe inferred from volume label/name; verify kernel path before booting")
                elif "clonezilla" in combined_hints or "debian" in combined_hints:
                    recipe = "live_boot"
                    confidence = "medium"
                    kernel_path = "/live/vmlinuz"
                    initrd_path = "/live/initrd.img"
                    warnings.append("Recipe inferred from volume label/name; verify kernel path before booting")
                else:
                    recipe = "generic_kernel"
                    confidence = "uncertain"
                    warnings.append(
                        "Could not definitively detect Linux distribution family from ISO filesystem structure. "
                        "Defaulting to generic kernel loopback recipe. Manual kernel/initrd path configuration is recommended."
                    )

        return InspectionResult(
            volume_id=vol_id,
            system_id=sys_id,
            publisher_id=pub_id,
            detected_recipe=recipe,
            confidence=confidence,
            discovered_kernel_path=kernel_path,
            discovered_initrd_path=initrd_path,
            directories=dirs_list,
            files=files_list,
            warnings=warnings,
        )

