"""Installation Recovery and Failure Cleanup Subsystem for MVentX."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
from typing import List, Optional

logger = logging.getLogger(__name__)


class InstallationRecoveryService:
    """Safely cleans up temporary staging artifacts when installation fails or is cancelled."""

    def __init__(self) -> None:
        self._tracked_temp_dirs: List[Path] = []
        self._tracked_temp_files: List[Path] = []

    def track_temp_dir(self, directory: Path | str) -> None:
        """Register a temporary directory for cleanup upon error."""
        p = Path(directory).resolve()
        if p not in self._tracked_temp_dirs:
            self._tracked_temp_dirs.append(p)

    def track_temp_file(self, file_path: Path | str) -> None:
        """Register a temporary file for deletion upon error."""
        p = Path(file_path).resolve()
        if p not in self._tracked_temp_files:
            self._tracked_temp_files.append(p)

    def cleanup_on_error(self, remove_target_file: Optional[Path] = None) -> None:
        """Clean up registered temporary paths and optionally the partially written target."""
        for temp_dir in self._tracked_temp_dirs:
            if temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except OSError as exc:
                    logger.warning("Failed to remove staging directory %s: %exc", temp_dir, exc)

        for temp_file in self._tracked_temp_files:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

        if remove_target_file and remove_target_file.exists():
            try:
                remove_target_file.unlink()
            except OSError:
                pass

        self._tracked_temp_dirs.clear()
        self._tracked_temp_files.clear()

    def clear(self) -> None:
        """Clear tracking when operation succeeds."""
        self._tracked_temp_dirs.clear()
        self._tracked_temp_files.clear()

