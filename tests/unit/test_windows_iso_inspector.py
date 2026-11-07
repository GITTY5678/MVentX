"""Unit tests for Windows ISO Inspector (Milestone 11).

Tests ISO 9660 parsing, EFI bootloader detection, BCD discovery, WinPE
verification, and payload classification for Windows media.
"""

from io import BytesIO
from pathlib import Path
import struct
import tempfile
import unittest

from core.iso_manager.windows_iso_inspector import (
    WindowsArchitecture,
    WindowsISOInspector,
    WindowsInspectionResult,
    WindowsPayloadType,
)


def _build_synthetic_iso(files: dict[str, bytes], volume_id: str = "WIN11_INSTALL") -> bytes:
    """Construct a valid ISO 9660 byte stream with a full directory hierarchy.
    
    Paths in `files` are POSIX strings, e.g. 'efi/boot/bootx64.efi'.
    """
    SECTOR_SIZE = 2048

    class FSEntry:
        def __init__(self, name: str, is_dir: bool = False, data: bytes = b""):
            self.name = name.upper()
            self.is_dir = is_dir
            self.data = data
            self.children: dict[str, FSEntry] = {}
            self.lba = 0
            self.size = 0

    root = FSEntry("", is_dir=True)

    # Build tree
    for path, data in files.items():
        parts = [p.strip().upper() for p in path.strip("/").split("/") if p.strip()]
        cur = root
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                cur.children[part] = FSEntry(part, is_dir=False, data=data)
            else:
                if part not in cur.children:
                    cur.children[part] = FSEntry(part, is_dir=True)
                cur = cur.children[part]

    # Collect all directories (BFS) and all files
    dirs: list[FSEntry] = [root]
    all_files: list[FSEntry] = []

    idx = 0
    while idx < len(dirs):
        d = dirs[idx]
        for c in d.children.values():
            if c.is_dir:
                dirs.append(c)
            else:
                all_files.append(c)
        idx += 1

    # Allocate LBAs
    # 0..15: System area (16 sectors)
    # 16: PVD (1 sector)
    # 17: Terminator (1 sector)
    # 18 .. 18 + len(dirs) - 1: Directory sectors (1 sector each)
    # following: File data sectors
    cur_lba = 18
    for d in dirs:
        d.lba = cur_lba
        d.size = SECTOR_SIZE
        cur_lba += 1

    for f in all_files:
        f.lba = cur_lba
        f.size = len(f.data)
        sectors_needed = max(1, (len(f.data) + SECTOR_SIZE - 1) // SECTOR_SIZE)
        cur_lba += sectors_needed

    total_sectors = cur_lba

    out = BytesIO()
    # 16 system sectors
    out.write(b"\x00" * (16 * SECTOR_SIZE))

    # PVD
    pvd = bytearray(SECTOR_SIZE)
    pvd[0] = 0x01
    pvd[1:6] = b"CD001"
    pvd[6] = 0x01
    pvd[8:40] = b"WINDOWS".ljust(32, b" ")
    pvd[40:72] = volume_id.encode("ascii", "replace").ljust(32, b" ")

    struct.pack_into("<I", pvd, 80, total_sectors)
    struct.pack_into(">I", pvd, 84, total_sectors)
    struct.pack_into("<H", pvd, 128, SECTOR_SIZE)
    struct.pack_into(">H", pvd, 130, SECTOR_SIZE)

    # Root record in PVD
    root_rec = bytearray(34)
    root_rec[0] = 34
    struct.pack_into("<I", root_rec, 2, root.lba)
    struct.pack_into(">I", root_rec, 6, root.lba)
    struct.pack_into("<I", root_rec, 10, root.size)
    struct.pack_into(">I", root_rec, 14, root.size)
    root_rec[25] = 0x02
    root_rec[32] = 1
    root_rec[33] = 0x00
    pvd[156:190] = root_rec
    out.write(pvd)

    # Terminator
    term = bytearray(SECTOR_SIZE)
    term[0] = 0xFF
    term[1:6] = b"CD001"
    term[6] = 0x01
    out.write(term)

    # Write each directory sector
    for d in dirs:
        d_sector = bytearray(SECTOR_SIZE)
        off = 0

        # '.' record
        dot_rec = bytearray(34)
        dot_rec[0] = 34
        struct.pack_into("<I", dot_rec, 2, d.lba)
        struct.pack_into(">I", dot_rec, 6, d.lba)
        struct.pack_into("<I", dot_rec, 10, d.size)
        struct.pack_into(">I", dot_rec, 14, d.size)
        dot_rec[25] = 0x02
        dot_rec[32] = 1
        dot_rec[33] = 0x00
        d_sector[off : off + 34] = dot_rec
        off += 34

        # '..' record
        dotdot_rec = bytearray(34)
        dotdot_rec[0] = 34
        parent_lba = root.lba  # approximate parent
        struct.pack_into("<I", dotdot_rec, 2, parent_lba)
        struct.pack_into(">I", dotdot_rec, 6, parent_lba)
        struct.pack_into("<I", dotdot_rec, 10, SECTOR_SIZE)
        struct.pack_into(">I", dotdot_rec, 14, SECTOR_SIZE)
        dotdot_rec[25] = 0x02
        dotdot_rec[32] = 1
        dotdot_rec[33] = 0x01
        d_sector[off : off + 34] = dotdot_rec
        off += 34

        # Child entries
        for child in d.children.values():
            if child.is_dir:
                name_bytes = child.name.encode("ascii", "replace")
                flags = 0x02
                entry_lba = child.lba
                entry_size = child.size
            else:
                name_bytes = child.name.encode("ascii", "replace") + b";1"
                flags = 0x00
                entry_lba = child.lba
                entry_size = child.size

            r_len = 33 + len(name_bytes)
            if r_len % 2 != 0:
                r_len += 1

            rec = bytearray(r_len)
            rec[0] = r_len
            struct.pack_into("<I", rec, 2, entry_lba)
            struct.pack_into(">I", rec, 6, entry_lba)
            struct.pack_into("<I", rec, 10, entry_size)
            struct.pack_into(">I", rec, 14, entry_size)
            rec[25] = flags
            rec[32] = len(name_bytes)
            rec[33 : 33 + len(name_bytes)] = name_bytes

            if off + r_len <= SECTOR_SIZE:
                d_sector[off : off + r_len] = rec
                off += r_len

        out.write(d_sector)

    # Write file data
    for f in all_files:
        sectors_needed = max(1, (len(f.data) + SECTOR_SIZE - 1) // SECTOR_SIZE)
        total_bytes = sectors_needed * SECTOR_SIZE
        padded = f.data.ljust(total_bytes, b"\x00")
        out.write(padded)

    return out.getvalue()


class TestWindowsISOInspector(unittest.TestCase):
    """Test suite for WindowsISOInspector."""

    def test_nonexistent_file(self):
        inspector = WindowsISOInspector("/nonexistent/windows_setup.iso")
        res = inspector.inspect()
        self.assertFalse(res.is_windows_media)
        self.assertEqual(res.architecture, WindowsArchitecture.UNKNOWN)
        self.assertEqual(res.payload_type, WindowsPayloadType.NONE)
        self.assertTrue(any("does not exist" in w.lower() for w in res.warnings))

    def test_truncated_file(self):
        with tempfile.NamedTemporaryFile(suffix=".iso", delete=False) as f:
            f.write(b"CORRUPTED_SHORT_DATA")
            temp_path = Path(f.name)

        try:
            inspector = WindowsISOInspector(temp_path)
            res = inspector.inspect()
            self.assertFalse(res.is_windows_media)
            self.assertTrue(any("too small" in w.lower() for w in res.warnings))
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_standard_windows11_x64_iso(self):
        files = {
            "efi/boot/bootx64.efi": b"MOCK_EFI_BOOTLOADER",
            "efi/microsoft/boot/bcd": b"MOCK_BCD_DATABASE",
            "sources/boot.wim": b"MOCK_WINPE_IMAGE",
            "sources/install.wim": b"MOCK_INSTALL_WIM_PAYLOAD_DATA",
            "setup.exe": b"MOCK_SETUP_EXE",
        }
        iso_bytes = _build_synthetic_iso(files, volume_id="WIN11_INSTALL")

        with tempfile.NamedTemporaryFile(suffix=".iso", delete=False) as f:
            f.write(iso_bytes)
            temp_path = Path(f.name)

        try:
            inspector = WindowsISOInspector(temp_path)
            res = inspector.inspect()

            self.assertTrue(res.is_windows_media)
            self.assertEqual(res.architecture, WindowsArchitecture.X64)
            self.assertEqual(res.payload_type, WindowsPayloadType.WIM)
            self.assertEqual(res.primary_bootloader, "/efi/boot/bootx64.efi")
            self.assertTrue(res.has_bcd)
            self.assertTrue(res.has_winpe)
            self.assertIn("install.wim", res.payload_path)
            self.assertIn("x64", res.summary().lower())
            
            # Verify dictionary representation
            data_dict = res.to_dict()
            self.assertTrue(data_dict["is_windows_media"])
            self.assertEqual(data_dict["architecture"], "x64")
            self.assertEqual(data_dict["payload_type"], "wim")
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_windows_esd_arm64_iso(self):
        files = {
            "efi/boot/bootarm64.efi": b"MOCK_ARM64_EFI",
            "boot/bcd": b"MOCK_BCD",
            "sources/boot.wim": b"MOCK_WINPE",
            "sources/install.esd": b"MOCK_COMPRESSED_ESD_PAYLOAD",
        }
        iso_bytes = _build_synthetic_iso(files, volume_id="WIN_ARM64_ESD")

        with tempfile.NamedTemporaryFile(suffix=".iso", delete=False) as f:
            f.write(iso_bytes)
            temp_path = Path(f.name)

        try:
            inspector = WindowsISOInspector(temp_path)
            res = inspector.inspect()

            self.assertTrue(res.is_windows_media)
            self.assertEqual(res.architecture, WindowsArchitecture.ARM64)
            self.assertEqual(res.payload_type, WindowsPayloadType.ESD)
            self.assertEqual(res.primary_bootloader, "/efi/boot/bootarm64.efi")
            self.assertTrue(res.has_bcd)
            self.assertTrue(res.has_winpe)
            self.assertIn("install.esd", res.payload_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_split_wim_swm_iso(self):
        files = {
            "efi/boot/bootx64.efi": b"MOCK_BOOTX64",
            "boot/bcd": b"MOCK_BCD",
            "sources/boot.wim": b"MOCK_WINPE",
            "sources/install.swm": b"MOCK_SPLIT_WIM_PART1",
            "sources/install2.swm": b"MOCK_SPLIT_WIM_PART2",
        }
        iso_bytes = _build_synthetic_iso(files, volume_id="WIN_SPLIT_SWM")

        with tempfile.NamedTemporaryFile(suffix=".iso", delete=False) as f:
            f.write(iso_bytes)
            temp_path = Path(f.name)

        try:
            inspector = WindowsISOInspector(temp_path)
            res = inspector.inspect()

            self.assertTrue(res.is_windows_media)
            self.assertEqual(res.payload_type, WindowsPayloadType.SWM)
            self.assertIn("install.swm", res.payload_path)
            self.assertEqual(len(res.split_wim_parts), 2)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_linux_iso_not_identified_as_windows(self):
        files = {
            "boot/grub/grub.cfg": b"menuentry 'Linux' { linux /vmlinuz; }",
            "vmlinuz": b"KERNEL_CODE",
            "initrd.img": b"INITRD_CODE",
        }
        iso_bytes = _build_synthetic_iso(files, volume_id="UBUNTU_24_04")

        with tempfile.NamedTemporaryFile(suffix=".iso", delete=False) as f:
            f.write(iso_bytes)
            temp_path = Path(f.name)

        try:
            inspector = WindowsISOInspector(temp_path)
            res = inspector.inspect()

            self.assertFalse(res.is_windows_media)
            self.assertEqual(res.architecture, WindowsArchitecture.UNKNOWN)
            self.assertEqual(res.payload_type, WindowsPayloadType.NONE)
        finally:
            if temp_path.exists():
                temp_path.unlink()


if __name__ == "__main__":
    unittest.main()
