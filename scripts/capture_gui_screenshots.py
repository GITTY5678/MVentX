"""Automated Screenshot Capture Tool for MVentX Manager Desktop UI.

Generates high-resolution PNG screenshots for each screen:
- Dashboard
- Environment Library
- Add Environment
- Storage Manager
- Settings
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

# Ensure offscreen Qt rendering
os.environ["QT_QPA_PLATFORM"] = "offscreen"

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from manager.main import create_app
from tests.unit.test_iso_validation import make_mock_iso


def capture_screenshots(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Use staged build directories
    staged_data = REPO_ROOT / "build" / "data"
    staged_esp = REPO_ROOT / "build" / "esp"
    staged_iso_dir = staged_data / "ISO"
    staged_iso_dir.mkdir(parents=True, exist_ok=True)

    # Ensure at least two mock ISOs are present for demonstration
    iso1 = staged_iso_dir / "ubuntu-24.04-desktop-amd64.iso"
    if not iso1.exists():
        make_mock_iso(iso1, volume_id="UBUNTU_24_04_X86_64", system_id="LINUX", publisher_id="CANONICAL")

    iso2 = staged_iso_dir / "fedora-40-workstation-x86_64.iso"
    if not iso2.exists():
        make_mock_iso(iso2, volume_id="FEDORA_40_X86_64", system_id="LINUX", publisher_id="RED_HAT")

    iso3 = staged_iso_dir / "archlinux-2026.05.01-x86_64.iso"
    if not iso3.exists():
        make_mock_iso(iso3, volume_id="ARCH_202605", system_id="LINUX", publisher_id="ARCH_LINUX", format_type="hybrid")

    app, window = create_app(data_path=staged_data, boot_path=staged_esp)
    window.resize(1120, 750)
    window.show()

    # Register an ISO if none registered yet so screens look populated
    iso_svc = window.iso_service
    envs = iso_svc.list_environments()
    if not envs:
        iso_svc.register_iso(
            iso_file="ubuntu-24.04-desktop-amd64.iso",
            display_name="Ubuntu 24.04 LTS Desktop (Noble)",
            boot_method="linux_generic_loopback",
            description="Default Linux workstation environment",
            enabled=True,
            calculate_hash=True,
        )
        iso_svc.register_iso(
            iso_file="fedora-40-workstation-x86_64.iso",
            display_name="Fedora 40 Workstation",
            boot_method="linux_generic_loopback",
            description="Red Hat GNOME 46 Workstation",
            enabled=True,
        )

    screens = [
        ("dashboard.png", 0),
        ("environment_library.png", 1),
        ("add_environment.png", 2),
        ("storage_manager.png", 3),
        ("settings.png", 4),
    ]

    for filename, idx in screens:
        window.switch_screen(idx)
        app.processEvents()

        # Capture high-res pixmap
        pixmap = window.grab()
        target_path = output_dir / filename
        pixmap.save(str(target_path), "PNG")
        print(f"[+] Saved screenshot: {target_path} ({pixmap.width()}x{pixmap.height()})")

    window.close()


def main() -> int:
    out = REPO_ROOT / "docs" / "screenshots"
    print(f"[*] Capturing MVentX Manager UI screenshots to {out}...")
    capture_screenshots(out)
    print("[*] All screenshots captured successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

