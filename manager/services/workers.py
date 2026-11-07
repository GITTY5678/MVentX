"""Background Thread Workers for Long-Running Disk I/O Operations in MVentX Manager.

Provides asynchronous, cancellable QThread workers for:
- Copying large ISO files with progress and speed reporting
- Calculating SHA-256 checksums with progress
- Asynchronous boot menu regeneration and verification
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
import shutil
import time
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB chunks


class ISOCopyWorker(QThread):
    """Asynchronous worker for copying ISO files to the MVentX data partition."""

    progress = Signal(int, int, int, float)  # (bytes_copied, total_bytes, percent, speed_mb_s)
    finished_success = Signal(Path)          # destination path
    failed = Signal(str)                     # error message
    cancelled = Signal()

    def __init__(
        self,
        source_path: Path | str,
        destination_dir: Path | str,
        dest_filename: Optional[str] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.source_path = Path(source_path)
        self.destination_dir = Path(destination_dir)
        self.dest_filename = dest_filename or self.source_path.name
        self.chunk_size = chunk_size
        self._is_cancelled = False

    def cancel(self) -> None:
        """Signal the worker to halt copy and clean up partial file."""
        self._is_cancelled = True

    def run(self) -> None:
        """Execute chunked file copy in background thread."""
        dest_path = self.destination_dir / self.dest_filename

        try:
            if not self.source_path.is_file():
                self.failed.emit(f"Source file not found: {self.source_path}")
                return

            self.destination_dir.mkdir(parents=True, exist_ok=True)
            total_bytes = self.source_path.stat().st_size

            # Check available disk space
            disk_usage = shutil.disk_usage(self.destination_dir)
            if disk_usage.free < total_bytes + (10 * 1024 * 1024):  # 10 MB margin
                self.failed.emit(
                    f"Insufficient disk space on target partition. "
                    f"Required: {total_bytes / (1024**2):.1f} MiB, Available: {disk_usage.free / (1024**2):.1f} MiB."
                )
                return

            bytes_copied = 0
            start_time = time.monotonic()
            last_speed_check = start_time
            bytes_since_last_check = 0
            speed_mb_s = 0.0

            with open(self.source_path, "rb") as src, open(dest_path, "wb") as dst:
                while True:
                    if self._is_cancelled:
                        break

                    chunk = src.read(self.chunk_size)
                    if not chunk:
                        break

                    dst.write(chunk)
                    bytes_copied += len(chunk)
                    bytes_since_last_check += len(chunk)

                    now = time.monotonic()
                    dt = now - last_speed_check
                    if dt >= 0.25:  # Update speed every 250ms
                        speed_mb_s = (bytes_since_last_check / (1024 * 1024)) / dt
                        last_speed_check = now
                        bytes_since_last_check = 0

                    percent = int((bytes_copied / total_bytes) * 100) if total_bytes > 0 else 0
                    self.progress.emit(bytes_copied, total_bytes, percent, speed_mb_s)

                if not self._is_cancelled:
                    dst.flush()

            if self._is_cancelled:
                # Clean up partial destination file
                if dest_path.is_file():
                    try:
                        dest_path.unlink()
                    except Exception as clean_err:
                        logger.warning("Could not delete partial copy %s: %s", dest_path, clean_err)
                self.cancelled.emit()
            else:
                self.progress.emit(total_bytes, total_bytes, 100, 0.0)
                self.finished_success.emit(dest_path)

        except Exception as exc:
            logger.exception("ISO copy error: %s", exc)
            if dest_path.is_file():
                try:
                    dest_path.unlink()
                except Exception:
                    pass
            self.failed.emit(f"Failed to copy ISO: {exc}")


class ChecksumWorker(QThread):
    """Asynchronous worker for calculating SHA-256 hashes of large files without freezing UI."""

    progress = Signal(int, int, int)  # (bytes_read, total_bytes, percent)
    finished_success = Signal(str)    # hex digest
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        file_path: Path | str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.file_path = Path(file_path)
        self.chunk_size = chunk_size
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        try:
            if not self.file_path.is_file():
                self.failed.emit(f"File not found: {self.file_path}")
                return

            total_bytes = self.file_path.stat().st_size
            hasher = hashlib.sha256()
            bytes_read = 0

            with open(self.file_path, "rb") as f:
                while True:
                    if self._is_cancelled:
                        break

                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break

                    hasher.update(chunk)
                    bytes_read += len(chunk)

                    percent = int((bytes_read / total_bytes) * 100) if total_bytes > 0 else 0
                    self.progress.emit(bytes_read, total_bytes, percent)

            if self._is_cancelled:
                self.cancelled.emit()
            else:
                self.progress.emit(total_bytes, total_bytes, 100)
                self.finished_success.emit(hasher.hexdigest())

        except Exception as exc:
            logger.exception("Checksum computation error: %s", exc)
            self.failed.emit(f"Failed to calculate SHA-256 checksum: {exc}")


class SyncBootConfigWorker(QThread):
    """Asynchronous worker to synchronize GRUB configuration to boot partition."""

    finished_success = Signal(str)
    failed = Signal(str)

    def __init__(self, storage_manager: Any, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.storage_manager = storage_manager

    def run(self) -> None:
        try:
            if not self.storage_manager:
                self.failed.emit("Storage manager not initialized.")
                return
            result_path = self.storage_manager.sync_boot_menu()
            self.finished_success.emit(f"Boot menu synced to {result_path}")
        except Exception as exc:
            logger.exception("Boot config sync error: %s", exc)
            self.failed.emit(f"Failed to sync boot menu: {exc}")

