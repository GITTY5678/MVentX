"""Host Platform Abstraction and Capability Detection for MVentX.

Identifies host operating system type, runtime capabilities, notification backends,
and privilege constraints without tying high-level services to a specific OS.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class PlatformType(str, Enum):
    """Host operating system family."""
    WINDOWS = "windows"
    LINUX = "linux"
    MACOS = "macos"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PlatformCapabilities:
    """Supported host features and subsystem integrations."""
    platform_type: PlatformType
    has_native_notifications: bool      # WM_DEVICECHANGE (Windows) or udev/pyudev (Linux)
    can_enumerate_disks: bool           # PowerShell Get-Disk or lsblk available
    can_mount_unmounted: bool           # Mount manager can mount partitions
    can_safe_eject: bool                # Platform eject API available
    is_administrator: bool              # Running with administrative/root elevation
    os_release: str                     # e.g. "10.0.22631", "6.8.0-45-generic"
    python_version: str                 # e.g. "3.14.0"


class HostPlatform:
    """Probes host operating system characteristics and capabilities."""

    @classmethod
    def get_platform_type(cls) -> PlatformType:
        """Determine current operating system family."""
        system = platform.system().lower()
        if system == "windows":
            return PlatformType.WINDOWS
        elif system == "linux":
            return PlatformType.LINUX
        elif system == "darwin":
            return PlatformType.MACOS
        return PlatformType.UNKNOWN

    @classmethod
    def is_windows(cls) -> bool:
        return cls.get_platform_type() == PlatformType.WINDOWS

    @classmethod
    def is_linux(cls) -> bool:
        return cls.get_platform_type() == PlatformType.LINUX

    @classmethod
    def is_elevated(cls) -> bool:
        """Check if current process has root or Administrator rights."""
        if cls.is_windows():
            try:
                import ctypes
                return bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception:
                return False
        else:
            try:
                return os.geteuid() == 0  # type: ignore[attr-defined]
            except Exception:
                return False

    @classmethod
    def get_capabilities(cls) -> PlatformCapabilities:
        """Query platform support matrix."""
        p_type = cls.get_platform_type()
        is_admin = cls.is_elevated()

        has_native_notif = False
        can_enum = False
        can_mount = False
        can_eject = False

        if p_type == PlatformType.WINDOWS:
            has_native_notif = True     # WM_DEVICECHANGE via Win32 message loop
            can_enum = shutil.which("powershell") is not None
            can_mount = True
            can_eject = True
        elif p_type == PlatformType.LINUX:
            has_native_notif = shutil.which("udevadm") is not None
            can_enum = shutil.which("lsblk") is not None
            can_mount = shutil.which("udisksctl") is not None or shutil.which("mount") is not None
            can_eject = shutil.which("udisksctl") is not None or shutil.which("eject") is not None

        return PlatformCapabilities(
            platform_type=p_type,
            has_native_notifications=has_native_notif,
            can_enumerate_disks=can_enum,
            can_mount_unmounted=can_mount,
            can_safe_eject=can_eject,
            is_administrator=is_admin,
            os_release=platform.release(),
            python_version=platform.python_version(),
        )

