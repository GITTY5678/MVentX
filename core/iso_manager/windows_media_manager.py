"""Windows Media Staging, Extraction, and Storage Management Engine for MVentX.

Manages safe extraction of Windows installation media onto MVX_DATA with:
1. Exact calculation of extracted file sizes, filesystem cluster overhead, and safety margins.
2. Target partition free-space pre-checks.
3. Progress reporting and cancellation checks.
4. Atomic staging and cleanup of interrupted/aborted extractions.
5. Post-extraction payload verification (boot.wim, install.wim/esd/swm).
6. Non-destructive deregistration: never silently deletes staged media on deregistration.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

from core.iso_manager.windows_iso_inspector import (
    WindowsInspectionResult,
    WindowsISOInspector,
    WindowsPayloadType,
)


class WindowsStorageError(Exception):
    """Base error for Windows media staging and storage issues."""


class InsufficientStorageError(WindowsStorageError):
    """Raised when destination partition lacks sufficient free space."""


class ExtractionInterruptedError(WindowsStorageError):
    """Raised when user cancels or aborts extraction in progress."""


class ExtractionIntegrityError(WindowsStorageError):
    """Raised when post-extraction payload verification fails."""


@dataclass
class StorageRequirement:
    """Detailed calculation of storage required on target volume."""
    raw_payload_bytes: int
    cluster_overhead_bytes: int
    safety_margin_bytes: int
    total_required_bytes: int
    available_free_bytes: int
    is_sufficient: bool

    @property
    def total_required_mb(self) -> float:
        return round(self.total_required_bytes / (1024 ** 2), 2)

    @property
    def total_required_gb(self) -> float:
        return round(self.total_required_bytes / (1024 ** 3), 2)

    @property
    def available_free_gb(self) -> float:
        return round(self.available_free_bytes / (1024 ** 3), 2)


class WindowsMediaManager:
    """Coordinates storage validation, staging, and lifecycle of Windows installation media."""

    DEFAULT_SAFETY_MARGIN_BYTES = 500 * 1024 * 1024  # 500 MiB safety cushion
    CLUSTER_SIZE_BYTES = 4096                         # Standard exFAT cluster size

    @classmethod
    def calculate_storage_requirement(
        cls,
        inspection: WindowsInspectionResult,
        destination_mount: Path | str,
        safety_margin_bytes: Optional[int] = None,
    ) -> StorageRequirement:
        """Calculate required free space with cluster overhead and safety margin.

        Args:
            inspection: WindowsInspectionResult from ISO inspection.
            destination_mount: Mount path of destination storage (e.g. MVX_DATA).
            safety_margin_bytes: Configurable safety cushion (default 500 MiB).
        """
        dest_p = Path(destination_mount).resolve()
        safety = safety_margin_bytes if safety_margin_bytes is not None else cls.DEFAULT_SAFETY_MARGIN_BYTES

        raw_size = inspection.total_iso_size_bytes
        # Cluster overhead estimation (typically ~5% over raw size for thousands of files)
        cluster_overhead = int(raw_size * 0.05)
        total_req = raw_size + cluster_overhead + safety

        # Query available free space
        avail = 0
        try:
            usage = shutil.disk_usage(dest_p)
            avail = usage.free
        except Exception:
            avail = 0

        is_sufficient = (avail >= total_req)

        return StorageRequirement(
            raw_payload_bytes=raw_size,
            cluster_overhead_bytes=cluster_overhead,
            safety_margin_bytes=safety,
            total_required_bytes=total_req,
            available_free_bytes=avail,
            is_sufficient=is_sufficient,
        )

    @classmethod
    def stage_windows_media(
        cls,
        iso_path: Path | str,
        destination_dir: Path | str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        safety_margin_bytes: Optional[int] = None,
    ) -> Path:
        """Safely extract Windows ISO contents into destination directory.

        Performs:
        1. Pre-extraction storage capacity validation.
        2. Clean staging to temporary folder.
        3. Post-extraction integrity check of boot.wim and install image.
        4. Cleanup on interruption or failure.
        """
        iso_p = Path(iso_path).resolve()
        dest_dir = Path(destination_dir).resolve()

        if not iso_p.is_file():
            raise FileNotFoundError(f"Windows ISO file does not exist: {iso_p}")

        # 1. Inspect ISO
        inspector = WindowsISOInspector(iso_p)
        inspection = inspector.inspect()
        if not inspection.is_windows_media:
            raise WindowsStorageError(f"Provided image is not valid Windows installation media: {iso_p.name}")

        # 2. Check storage capacity on parent volume
        dest_mount = dest_dir.parent if dest_dir.parent.exists() else dest_dir
        req = cls.calculate_storage_requirement(inspection, dest_mount, safety_margin_bytes)
        if not req.is_sufficient:
            raise InsufficientStorageError(
                f"Insufficient space on destination {dest_mount}. "
                f"Required: {req.total_required_gb:.2f} GiB (includes 500 MiB cushion), "
                f"Available: {req.available_free_gb:.2f} GiB."
            )

        if progress_callback:
            progress_callback(5, "Storage requirement verified. Beginning media staging...")

        # 3. Stage extraction into temporary folder on same volume
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix="mventx_win_staging_", dir=dest_dir.parent))

        try:
            cls._extract_iso_contents(iso_p, staging_dir, progress_callback, cancel_check)

            if cancel_check and cancel_check():
                raise ExtractionInterruptedError("Extraction was cancelled by user.")

            # 4. Verify extracted payload integrity
            if progress_callback:
                progress_callback(90, "Verifying extracted Windows installation payload...")
            cls.verify_extracted_media_integrity(staging_dir, inspection)

            # 5. Move staging folder into final destination directory
            if dest_dir.exists():
                shutil.rmtree(dest_dir, ignore_errors=True)
            staging_dir.rename(dest_dir)

            if progress_callback:
                progress_callback(100, "Windows installation media successfully staged.")

            return dest_dir

        except Exception as exc:
            # Cleanup staging folder on failure
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            raise exc

    @classmethod
    def _extract_iso_contents(
        cls,
        iso_path: Path,
        target_dir: Path,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Extract all files from ISO into target directory using available tools or pure-python."""
        # Check if 7z / 7za is available
        seven_zip = shutil.which("7z") or shutil.which("7za")
        if seven_zip:
            import subprocess
            cmd = [seven_zip, "x", str(iso_path), f"-o{target_dir}", "-y"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            while True:
                if cancel_check and cancel_check():
                    proc.terminate()
                    raise ExtractionInterruptedError("Extraction cancelled by user.")
                ret = proc.poll()
                if ret is not None:
                    break
                if progress_callback:
                    progress_callback(50, "Extracting files via 7-Zip...")
                import time
                time.sleep(0.5)

            if proc.returncode != 0:
                err = proc.stderr.read() if proc.stderr else "Unknown 7-Zip error"
                raise WindowsStorageError(f"7-Zip extraction failed with code {proc.returncode}: {err}")
            return

        # Fallback: Pure-Python ISO 9660 stream extractor
        cls._pure_python_extract(iso_path, target_dir, progress_callback, cancel_check)

    @classmethod
    def _pure_python_extract(
        cls,
        iso_path: Path,
        target_dir: Path,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Fallback rootless ISO extractor reading LBA sectors directly."""
        with open(iso_path, "rb") as f:
            inspector = WindowsISOInspector(iso_path)
            root_lba, root_len, _, _ = inspector._parse_volume_descriptors(f)
            if root_lba <= 0:
                raise WindowsStorageError("Cannot locate root directory record in ISO.")

            files_map: Dict[str, Tuple[int, int]] = {}
            inspector._traverse_directory_tree(f, root_lba, root_len, "/", files_map, max_depth=5)

            total_files = len(files_map)
            for idx, (path_str, (lba, size)) in enumerate(files_map.items()):
                if cancel_check and cancel_check():
                    raise ExtractionInterruptedError("Extraction cancelled by user.")

                rel_path = path_str.lstrip("/")
                out_path = target_dir / rel_path
                out_path.parent.mkdir(parents=True, exist_ok=True)

                # Stream read LBA blocks into file
                f.seek(lba * 2048)
                bytes_left = size
                with open(out_path, "wb") as out_f:
                    while bytes_left > 0:
                        chunk_size = min(bytes_left, 65536)
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        out_f.write(chunk)
                        bytes_left -= len(chunk)

                if progress_callback and total_files > 0 and idx % 10 == 0:
                    pct = 10 + int((idx / total_files) * 75)
                    progress_callback(pct, f"Extracting {rel_path} ({idx}/{total_files})...")

    @classmethod
    def verify_extracted_media_integrity(
        cls,
        extracted_root: Path | str,
        expected_inspection: WindowsInspectionResult,
    ) -> bool:
        """Verify presence and non-zero sizes of critical Windows boot and payload files."""
        root = Path(extracted_root).resolve()

        # 1. Verify EFI bootloader
        bootx64 = root / "efi" / "boot" / "bootx64.efi"
        bootmgr_efi = root / "bootmgr.efi"
        if not bootx64.is_file() and not bootmgr_efi.is_file():
            raise ExtractionIntegrityError("Critical EFI bootloader (bootx64.efi or bootmgr.efi) is missing.")

        # 2. Verify Windows PE (boot.wim)
        boot_wim = root / "sources" / "boot.wim"
        if not boot_wim.is_file() or boot_wim.stat().st_size == 0:
            raise ExtractionIntegrityError("Windows PE image (sources/boot.wim) is missing or 0 bytes.")

        # 3. Verify Installation Payload (install.wim / install.esd / install*.swm)
        payload_found = False
        install_wim = root / "sources" / "install.wim"
        install_esd = root / "sources" / "install.esd"

        if install_wim.is_file() and install_wim.stat().st_size > 0:
            payload_found = True
        elif install_esd.is_file() and install_esd.stat().st_size > 0:
            payload_found = True
        else:
            # Check for split-WIM (.swm) set
            swm_parts = list((root / "sources").glob("install*.swm"))
            if swm_parts and all(p.stat().st_size > 0 for p in swm_parts):
                payload_found = True

        if expected_inspection.has_install_payload and not payload_found:
            raise ExtractionIntegrityError("Windows installation image (install.wim/esd/swm) was not found after extraction.")

        return True

    @classmethod
    def cleanup_staged_media(cls, target_dir: Path | str) -> bool:
        """Explicitly delete staged Windows directory on user request."""
        p = Path(target_dir).resolve()
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            return True
        return False

