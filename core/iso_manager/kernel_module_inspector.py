"""Linux ELF Kernel Module and vmlinuz Inspector for MVentX.

Parses:
1. Linux x86 setup header to extract the authoritative kernel release string.
2. ELF 64-bit Relocatable (.ko) module headers and .modinfo section.
3. Cryptographic signature footers appended to kernel modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import struct
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class KernelModuleInfo:
    """Parsed metadata for a Linux kernel module (.ko)."""
    name: str
    vermagic: str
    kernel_version: str
    license: str
    aliases: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    has_signature: bool = False
    signer: Optional[str] = None
    file_size_bytes: int = 0
    description: Optional[str] = None
    author: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "vermagic": self.vermagic,
            "kernel_version": self.kernel_version,
            "license": self.license,
            "aliases": list(self.aliases),
            "dependencies": list(self.dependencies),
            "has_signature": self.has_signature,
            "signer": self.signer,
            "file_size_bytes": self.file_size_bytes,
            "description": self.description,
            "author": self.author,
        }


class KernelModuleInspectionError(Exception):
    """Raised when ELF parsing or module validation fails."""


class KernelModuleInspector:
    """Inspects Linux kernel binaries and ELF kernel modules (.ko)."""

    SIG_MAGIC = b"~Module signature appended~\n"
    SIG_MAGIC_ALT = b"~Module signature append~"

    @classmethod
    def _extract_pkcs7_common_name(cls, sig_der: bytes) -> Optional[str]:
        """Extract X.509 CommonName (OID 2.5.4.3: 06 03 55 04 03) from a PKCS#7 SignedData DER blob."""
        oid_cn = b"\x06\x03\x55\x04\x03"
        idx = sig_der.find(oid_cn)
        if idx < 0 or idx + len(oid_cn) + 2 > len(sig_der):
            return None
        val_off = idx + len(oid_cn)
        tag = sig_der[val_off]
        length = sig_der[val_off + 1]
        # ASN.1 UTF8String (0x0C), PrintableString (0x13), IA5String (0x16), TeletexString (0x14)
        if tag in (0x0C, 0x13, 0x14, 0x16) and length < 128:
            str_start = val_off + 2
            str_end = str_start + length
            if str_end <= len(sig_der):
                return sig_der[str_start:str_end].decode("utf-8", errors="replace").strip() or None
        return None

    @classmethod
    def extract_vmlinuz_version(cls, kdata: bytes) -> Optional[str]:
        """Extract authoritative kernel version string from Linux x86 vmlinuz boot header.

        Reads the x86 setup header (offset 0x200..0x206 magic b'HdrS') and the 16-bit
        pointer to the kernel version string at offset 0x20E. Falls back to regex search.
        """
        if len(kdata) > 0x220 and kdata[0x202:0x206] == b"HdrS":
            kver_ptr, = struct.unpack_from("<H", kdata, 0x20E)
            if 0 < kver_ptr < len(kdata) - 0x200:
                str_off = 0x200 + kver_ptr
                chunk = kdata[str_off : str_off + 256].split(b"\x00")[0]
                tokens = chunk.split(b" ")
                if tokens and tokens[0]:
                    try:
                        return tokens[0].decode("ascii")
                    except UnicodeDecodeError:
                        pass

        # Fallback regex search for b"Linux version <version>"
        match = re.search(rb"Linux version ([0-9]+\.[0-9]+\.[0-9]+-[0-9]+-[a-zA-Z0-9_.-]+)", kdata)
        if match:
            return match.group(1).decode("ascii")

        return None

    @classmethod
    def inspect_elf_module(cls, data: bytes) -> KernelModuleInfo:
        """Parse ELF 64-bit Relocatable object (.ko) and extract module metadata."""
        if len(data) < 64:
            raise KernelModuleInspectionError(f"Module file too small to be valid ELF ({len(data)} bytes)")

        if data[:4] != b"\x7fELF":
            raise KernelModuleInspectionError("Invalid ELF magic bytes; expected b'\\x7fELF'")

        ei_class = data[4]
        if ei_class != 2:
            raise KernelModuleInspectionError(f"Unsupported ELF class {ei_class}; expected 2 (64-bit)")

        ei_data = data[5]
        if ei_data != 1:
            raise KernelModuleInspectionError(f"Unsupported ELF endianness {ei_data}; expected 1 (Little-Endian)")

        e_type, = struct.unpack_from("<H", data, 16)
        if e_type != 1:  # ET_REL
            raise KernelModuleInspectionError(f"ELF object type {e_type} is not ET_REL (1)")

        e_shoff, = struct.unpack_from("<Q", data, 40)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 58)

        if e_shoff + (e_shnum * e_shentsize) > len(data):
            raise KernelModuleInspectionError("Section header table exceeds module file boundary")

        # Read section header string table (.shstrtab)
        str_hdr_off = e_shoff + e_shstrndx * e_shentsize
        sh_offset, sh_size = struct.unpack_from("<QQ", data, str_hdr_off + 24)
        if sh_offset + sh_size > len(data):
            raise KernelModuleInspectionError(".shstrtab offset exceeds file bounds")
        shstrtab = data[sh_offset : sh_offset + sh_size]

        # Locate .modinfo section
        modinfo_bytes: Optional[bytes] = None
        for i in range(e_shnum):
            sec_off = e_shoff + i * e_shentsize
            sh_name, sh_type, sh_flags, sh_addr, sec_offset, sec_size = struct.unpack_from(
                "<IIQQQQ", data, sec_off
            )
            if sh_name < len(shstrtab):
                name = shstrtab[sh_name:].split(b"\x00")[0].decode("latin-1", errors="ignore")
                if name == ".modinfo":
                    if sec_offset + sec_size <= len(data):
                        modinfo_bytes = data[sec_offset : sec_offset + sec_size]
                    break

        if not modinfo_bytes:
            raise KernelModuleInspectionError("Missing required .modinfo section in kernel module")

        # Parse .modinfo null-separated key=value strings
        info_dict: Dict[str, str] = {}
        aliases: List[str] = []
        for raw_item in modinfo_bytes.split(b"\x00"):
            if not raw_item:
                continue
            text = raw_item.decode("latin-1", errors="replace")
            if "=" in text:
                k, v = text.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k == "alias":
                    aliases.append(v)
                else:
                    info_dict[k] = v

        name = info_dict.get("name", "unknown")
        vermagic = info_dict.get("vermagic", "")
        if not vermagic:
            raise KernelModuleInspectionError("Module .modinfo is missing required 'vermagic' field")

        kernel_version = vermagic.split(" ")[0].strip()
        license_str = info_dict.get("license", "unknown")
        description = info_dict.get("description")
        author = info_dict.get("author")

        depends_raw = info_dict.get("depends", "")
        dependencies = [d.strip() for d in depends_raw.split(",") if d.strip()]

        # Check for cryptographic signature footer (standard 28-byte magic or legacy test magic)
        has_sig = False
        signer: Optional[str] = None
        matched_magic: Optional[bytes] = None
        if data.endswith(cls.SIG_MAGIC):
            matched_magic = cls.SIG_MAGIC
        elif data.endswith(cls.SIG_MAGIC_ALT):
            matched_magic = cls.SIG_MAGIC_ALT

        if matched_magic is not None:
            has_sig = True
            sig_magic_len = len(matched_magic)
            # 12-byte struct module_signature sits directly before the magic
            if len(data) >= sig_magic_len + 12:
                sig_info_off = len(data) - (sig_magic_len + 12)
                algo, hash_algo, id_type, signer_len, key_id_len, sig_len = struct.unpack(
                    ">BBBBII", data[sig_info_off : sig_info_off + 12]
                )
                total_sig_block = sig_magic_len + 12 + signer_len + key_id_len + sig_len
                if len(data) >= total_sig_block:
                    sig_start = len(data) - total_sig_block
                    if signer_len > 0:
                        signer_bytes = data[sig_start : sig_start + signer_len]
                        signer = signer_bytes.decode("latin-1", errors="replace").strip() or None
                    elif sig_len > 0:
                        # PKCS#7 (id_type == 2) embeds signer CommonName inside ASN.1 DER SignedData
                        pkcs7_der = data[sig_start : sig_start + sig_len]
                        signer = cls._extract_pkcs7_common_name(pkcs7_der)

        return KernelModuleInfo(
            name=name,
            vermagic=vermagic,
            kernel_version=kernel_version,
            license=license_str,
            aliases=aliases,
            dependencies=dependencies,
            has_signature=has_sig,
            signer=signer,
            file_size_bytes=len(data),
            description=description,
            author=author,
        )

    @classmethod
    def inspect_elf_file(cls, path: Path) -> KernelModuleInfo:
        """Inspect an ELF kernel module file on disk."""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Kernel module not found: {p}")
        return cls.inspect_elf_module(p.read_bytes())

    @classmethod
    def verify_module_compatibility(
        cls, module_info: KernelModuleInfo, target_kernel_version: str
    ) -> Tuple[bool, List[str]]:
        """Verify whether an extracted kernel module is compatible with target kernel."""
        reasons: List[str] = []

        # 1. Vermagic kernel version check
        clean_target = target_kernel_version.strip()
        if module_info.kernel_version != clean_target and not module_info.vermagic.startswith(clean_target):
            reasons.append(
                f"Kernel version mismatch: module built for '{module_info.kernel_version}', "
                f"target kernel is '{clean_target}'"
            )

        # 2. Module identity
        if module_info.name not in ("exfat", "fs-exfat"):
            if "fs-exfat" not in module_info.aliases:
                reasons.append(f"Unexpected module identity: name='{module_info.name}', expected 'exfat'")

        return len(reasons) == 0, reasons

