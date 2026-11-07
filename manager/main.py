"""MVentX Desktop Manager Application Entry Point.

Launches the PySide6 desktop management interface for MVentX USB drives.
Provides UI for inspecting storage, manually registering ISO environments,
reordering boot targets, and compiling updated GRUB boot menus.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from manager.services.device_service import DeviceService
from manager.services.iso_service import ISOService
from manager.ui.main_window import MainWindow


def create_app(data_path: Optional[Path] = None, boot_path: Optional[Path] = None) -> tuple[QApplication, MainWindow]:
    """Factory creating and wiring the QApplication and MainWindow instances."""
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    app.setApplicationName("MVentX Manager")
    app.setApplicationDisplayName("MVentX Manager")
    app.setOrganizationName("MVentX")

    device_service = DeviceService()
    iso_service = ISOService(device_service)

    # If explicit paths provided via CLI, configure them as active target
    if data_path:
        device_service.set_custom_target(
            data_mount=data_path,
            boot_mount=boot_path,
            display_name=f"Custom: {data_path.name}",
        )
    else:
        # Default fallback to build/data if exists and no physical drive connected
        staged_data = REPO_ROOT / "build" / "data"
        staged_esp = REPO_ROOT / "build" / "esp"
        if staged_data.is_dir() and not device_service.has_active_target():
            device_service.set_custom_target(
                data_mount=staged_data,
                boot_mount=staged_esp if staged_esp.is_dir() else None,
                display_name="Virtual Disk Staging",
            )

    window = MainWindow(device_service=device_service, iso_service=iso_service)
    return app, window


def main() -> int:
    """Launch the MVentX Manager desktop application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="MVentX Desktop Boot Management Interface")
    parser.add_argument(
        "--data",
        type=Path,
        default=None,
        help="Path to MVX_DATA partition or staging folder",
    )
    parser.add_argument(
        "--boot",
        type=Path,
        default=None,
        help="Path to MVX_BOOT partition or staging folder",
    )
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="Run in offscreen headless mode (for testing)",
    )

    args = parser.parse_args()

    if args.offscreen or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    app, window = create_app(data_path=args.data, boot_path=args.boot)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
