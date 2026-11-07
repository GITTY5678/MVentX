"""Linux Filesystem Utilities for MVentX.

Handles POSIX permissions, filesystem buffer syncing, and path resolution.
"""

from __future__ import annotations

import os
from pathlib import Path


class LinuxFilesystemUtils:
    """Linux-specific filesystem utilities."""

    @staticmethod
    def sync_buffers() -> None:
        """Call sync() system call."""
        if hasattr(os, "sync"):
            os.sync()

    @staticmethod
    def is_writable_mount(mount_point: Path | str) -> bool:
        """Check if mount point is writable."""
        p = Path(mount_point)
        return os.access(p, os.W_OK)

