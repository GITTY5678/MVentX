"""Storage Manager Screen for MVentX Manager.

Provides detailed inspection of the dual-partition architecture:
- Partition 1 (MVX_BOOT FAT32 ESP)
- Partition 2 (MVX_DATA exFAT Basic Data)
Clearly distinguishes registered ISO sizes from total filesystem storage usage.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from manager.services.device_service import ActiveTarget
from manager.services.iso_service import ISOService
from manager.ui.styles import (
    COLOR_ACCENT,
    COLOR_DANGER,
    COLOR_SUCCESS,
    COLOR_WARNING,
)


def _fmt_bytes(num_bytes: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PiB"


class StorageManagerScreen(QWidget):
    """Storage architecture and capacity inspection screen."""

    def __init__(self, iso_service: ISOService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.iso_service = iso_service

        self._setup_ui()
        self.iso_service.environments_changed.connect(self.refresh_storage_info)
        self.iso_service.device_service.target_changed.connect(self.refresh_storage_info)

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(16)

        # Header Title
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        h_title = QLabel("Storage Subsystem & Partitions")
        h_title.setStyleSheet("font-size: 18px; font-weight: 700; color: #f3f4f6;")
        h_sub = QLabel(
            "Detailed breakdown of the dual-partition GPT USB architecture and capacity allocation."
        )
        h_sub.setStyleSheet("color: #9ca3af; font-size: 12px;")
        title_box.addWidget(h_title)
        title_box.addWidget(h_sub)
        main_layout.addLayout(title_box)

        # 1. Partition Cards Grid (Partition 1 vs Partition 2)
        part_grid = QGridLayout()
        part_grid.setSpacing(16)

        # Partition 1: MVX_BOOT
        self.p1_card = QFrame()
        self.p1_card.setProperty("class", "card")
        p1_layout = QVBoxLayout(self.p1_card)
        p1_layout.setSpacing(8)

        p1_hdr = QLabel("PARTITION 1: MVX_BOOT")
        p1_hdr.setProperty("class", "metricLabel")
        self.p1_type = QLabel("EFI System Partition (ESP) | FAT32")
        self.p1_type.setStyleSheet("font-size: 15px; font-weight: 700; color: #f3f4f6;")
        self.p1_guid = QLabel("GUID: C12A7328-F81F-11D2-BA4B-00A0C93EC93B")
        self.p1_guid.setStyleSheet("color: #9ca3af; font-family: monospace; font-size: 11px;")
        self.p1_mount = QLabel("Mount: Unknown")
        self.p1_mount.setStyleSheet("color: #9ca3af; font-size: 12px;")
        self.p1_files = QLabel("Contents: BOOTX64.EFI, grub.cfg, mventx_menu.cfg")
        self.p1_files.setStyleSheet("color: #6b7280; font-size: 11px;")

        p1_layout.addWidget(p1_hdr)
        p1_layout.addWidget(self.p1_type)
        p1_layout.addWidget(self.p1_guid)
        p1_layout.addWidget(self.p1_mount)
        p1_layout.addWidget(self.p1_files)
        part_grid.addWidget(self.p1_card, 0, 0)

        # Partition 2: MVX_DATA
        self.p2_card = QFrame()
        self.p2_card.setProperty("class", "card")
        p2_layout = QVBoxLayout(self.p2_card)
        p2_layout.setSpacing(8)

        p2_hdr = QLabel("PARTITION 2: MVX_DATA")
        p2_hdr.setProperty("class", "metricLabel")
        self.p2_type = QLabel("Microsoft Basic Data | exFAT")
        self.p2_type.setStyleSheet("font-size: 15px; font-weight: 700; color: #f3f4f6;")
        self.p2_guid = QLabel("GUID: EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")
        self.p2_guid.setStyleSheet("color: #9ca3af; font-family: monospace; font-size: 11px;")
        self.p2_mount = QLabel("Mount: Unknown")
        self.p2_mount.setStyleSheet("color: #9ca3af; font-size: 12px;")
        self.p2_dirs = QLabel("Folders: ISO/, config/, metadata/, backups/")
        self.p2_dirs.setStyleSheet("color: #6b7280; font-size: 11px;")

        p2_layout.addWidget(p2_hdr)
        p2_layout.addWidget(self.p2_type)
        p2_layout.addWidget(self.p2_guid)
        p2_layout.addWidget(self.p2_mount)
        p2_layout.addWidget(self.p2_dirs)
        part_grid.addWidget(self.p2_card, 0, 1)

        main_layout.addLayout(part_grid)

        # 2. Capacity Breakdown Card
        self.cap_card = QFrame()
        self.cap_card.setProperty("class", "card")
        cap_layout = QVBoxLayout(self.cap_card)
        cap_layout.setSpacing(12)

        c_title = QLabel("CAPACITY & SPACE ALLOCATION")
        c_title.setProperty("class", "metricLabel")
        cap_layout.addWidget(c_title)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(24)

        self.lbl_total = QLabel("Total: 0.00 B")
        self.lbl_used = QLabel("Used: 0.00 B")
        self.lbl_free = QLabel("Free: 0.00 B")
        self.lbl_isos = QLabel("Registered ISOs: 0.00 B")
        self.lbl_overhead = QLabel("Filesystem / Overhead: 0.00 B")

        for lbl in (self.lbl_total, self.lbl_used, self.lbl_free, self.lbl_isos, self.lbl_overhead):
            lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #f3f4f6;")
            stats_row.addWidget(lbl)
        stats_row.addStretch()
        cap_layout.addLayout(stats_row)

        self.cap_progress = QProgressBar()
        self.cap_progress.setRange(0, 100)
        self.cap_progress.setValue(0)
        self.cap_progress.setTextVisible(True)
        cap_layout.addWidget(self.cap_progress)

        note = QLabel(
            "Note: 'Registered ISOs' accounts strictly for verified, registered ISO images. "
            "Total disk usage includes unallocated overhead, metadata caches, backups, and filesystem structures."
        )
        note.setStyleSheet("color: #9ca3af; font-size: 11px;")
        cap_layout.addWidget(note)

        main_layout.addWidget(self.cap_card)

        # 3. Directory Health Validation Card
        self.health_card = QFrame()
        self.health_card.setProperty("class", "card")
        h_layout = QVBoxLayout(self.health_card)
        h_layout.setSpacing(8)

        dh_title = QLabel("DATA PARTITION DIRECTORY INTEGRITY")
        dh_title.setProperty("class", "metricLabel")
        h_layout.addWidget(dh_title)

        self.dir_status_lbl = QLabel("Checking directory structure...")
        self.dir_status_lbl.setStyleSheet("color: #9ca3af; font-size: 12px;")
        h_layout.addWidget(self.dir_status_lbl)

        main_layout.addWidget(self.health_card)
        main_layout.addStretch()

    def refresh_storage_info(self) -> None:
        target: Optional[ActiveTarget] = self.iso_service.device_service.active_target
        if not target:
            self.p1_mount.setText("Mount: Not connected")
            self.p2_mount.setText("Mount: Not connected")
            self.lbl_total.setText("Total: 0.00 B")
            self.lbl_used.setText("Used: 0.00 B")
            self.lbl_free.setText("Free: 0.00 B")
            self.lbl_isos.setText("Registered ISOs: 0.00 B")
            self.lbl_overhead.setText("Filesystem / Overhead: 0.00 B")
            self.cap_progress.setValue(0)
            self.dir_status_lbl.setText("No active target selected.")
            return

        self.p1_mount.setText(f"Mount Point: {target.boot_mount or 'Not mounted'}")
        self.p2_mount.setText(f"Mount Point: {target.data_mount}")

        metrics = self.iso_service.get_storage_metrics()
        if metrics and metrics.total_bytes > 0:
            self.lbl_total.setText(f"Total: {_fmt_bytes(metrics.total_bytes)}")
            self.lbl_used.setText(f"Used: {_fmt_bytes(metrics.used_bytes)}")
            self.lbl_free.setText(f"Free: {_fmt_bytes(metrics.available_bytes)}")
            self.lbl_isos.setText(f"Registered ISOs: {_fmt_bytes(metrics.total_registered_iso_bytes)}")

            overhead = max(0, metrics.used_bytes - metrics.total_registered_iso_bytes)
            self.lbl_overhead.setText(f"Overhead: {_fmt_bytes(overhead)}")

            pct = int((metrics.used_bytes / metrics.total_bytes) * 100)
            self.cap_progress.setValue(pct)
            self.cap_progress.setFormat(f"%p% Utilized ({_fmt_bytes(metrics.used_bytes)} / {_fmt_bytes(metrics.total_bytes)})")

        mgr = self.iso_service.get_registration_manager()
        if mgr:
            valid, missing = mgr.storage_manager.validate_data_partition_structure()
            if valid:
                self.dir_status_lbl.setText("PASS: All required directories (ISO/, config/, metadata/, backups/) and config/mventx.json are intact.")
                self.dir_status_lbl.setStyleSheet(f"color: {COLOR_SUCCESS}; font-weight: 500;")
            else:
                self.dir_status_lbl.setText(f"WARNING: Structure issues detected: {', '.join(missing)}")
                self.dir_status_lbl.setStyleSheet(f"color: {COLOR_WARNING}; font-weight: 500;")

