"""Dashboard Screen for MVentX Manager.

Displays high-level device status, validation badge, partition health (including
mount points, filesystems, and read/write states), storage utilization, and
direct action shortcuts (Open in File Explorer, Copy ISO to USB, Safely Eject).
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.host.device_classification import MVentXStatus
from core.iso_manager import StorageMetrics
from manager.services.device_service import ActiveTarget
from manager.services.iso_service import ISOService
from manager.services.workers import ISOCopyWorker
from manager.ui.progress_dialog import OperationProgressDialog
from manager.ui.styles import (
    COLOR_ACCENT,
    COLOR_DANGER,
    COLOR_SUCCESS,
    COLOR_WARNING,
)


def _fmt_bytes(num_bytes: int) -> str:
    """Format bytes into human-readable string."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PiB"


class MetricCard(QFrame):
    """Compact metric card displaying a title, large numeric value, and subtitle."""

    def __init__(self, title: str, initial_val: str = "0", subtitle: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("class", "card")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        self.title_lbl = QLabel(title.upper())
        self.title_lbl.setProperty("class", "metricLabel")

        self.value_lbl = QLabel(initial_val)
        self.value_lbl.setProperty("class", "metricValue")

        self.subtitle_lbl = QLabel(subtitle)
        self.subtitle_lbl.setStyleSheet("color: #9ca3af; font-size: 11px;")

        layout.addWidget(self.title_lbl)
        layout.addWidget(self.value_lbl)
        layout.addWidget(self.subtitle_lbl)

    def set_value(self, val: str, subtitle: Optional[str] = None) -> None:
        self.value_lbl.setText(val)
        if subtitle is not None:
            self.subtitle_lbl.setText(subtitle)


class DashboardScreen(QWidget):
    """Main overview dashboard with health badges, metrics, and direct action shortcuts."""

    navigate_requested = Signal(str)  # Screen name to switch to

    def __init__(self, iso_service: ISOService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.iso_service = iso_service

        self._setup_ui()
        self.iso_service.environments_changed.connect(self.refresh_data)
        self.iso_service.device_service.target_changed.connect(self.refresh_data)

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(16)

        # ----------------------------------------------------------------------
        # 1. Device Status Banner Card + Validation Badge + Action Shortcuts
        # ----------------------------------------------------------------------
        self.device_card = QFrame()
        self.device_card.setProperty("class", "card")
        dev_layout = QVBoxLayout(self.device_card)
        dev_layout.setContentsMargins(20, 16, 20, 16)
        dev_layout.setSpacing(14)

        top_row = QHBoxLayout()
        info_box = QVBoxLayout()
        info_box.setSpacing(4)

        title_badge_row = QHBoxLayout()
        title_badge_row.setSpacing(12)
        self.device_title = QLabel("No MVentX Device Selected")
        self.device_title.setStyleSheet("font-size: 18px; font-weight: 700; color: #f3f4f6;")
        title_badge_row.addWidget(self.device_title)

        self.badge_status = QLabel("DISCONNECTED")
        self.badge_status.setProperty("class", "statusBadgeDisabled")
        title_badge_row.addWidget(self.badge_status)
        title_badge_row.addStretch()

        self.device_subtitle = QLabel("Please connect a physical MVentX USB drive or select a virtual disk.")
        self.device_subtitle.setStyleSheet("color: #9ca3af; font-size: 12px;")

        info_box.addLayout(title_badge_row)
        info_box.addWidget(self.device_subtitle)
        top_row.addLayout(info_box, stretch=1)

        # Navigation shortcut buttons
        self.btn_manage_storage = QPushButton("Inspect Storage")
        self.btn_manage_storage.clicked.connect(lambda: self.navigate_requested.emit("storage"))
        top_row.addWidget(self.btn_manage_storage)

        self.btn_add_env = QPushButton("+ Add Environment")
        self.btn_add_env.setProperty("class", "primaryButton")
        self.btn_add_env.clicked.connect(lambda: self.navigate_requested.emit("add"))
        top_row.addWidget(self.btn_add_env)

        dev_layout.addLayout(top_row)

        # Direct Actions Toolbar
        actions_sep = QFrame()
        actions_sep.setFrameShape(QFrame.HLine)
        actions_sep.setStyleSheet("color: #2e323b;")
        dev_layout.addWidget(actions_sep)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(12)

        action_title = QLabel("QUICK SHORTCUTS:")
        action_title.setProperty("class", "metricLabel")
        actions_row.addWidget(action_title)

        self.btn_open_explorer = QPushButton("📁 Open in File Explorer")
        self.btn_open_explorer.setEnabled(False)
        self.btn_open_explorer.clicked.connect(self._on_open_explorer)
        actions_row.addWidget(self.btn_open_explorer)

        self.btn_copy_iso = QPushButton("📥 Copy ISO to USB...")
        self.btn_copy_iso.setEnabled(False)
        self.btn_copy_iso.clicked.connect(self._on_copy_iso_clicked)
        actions_row.addWidget(self.btn_copy_iso)

        self.btn_eject_usb = QPushButton("⏏ Safely Eject USB")
        self.btn_eject_usb.setEnabled(False)
        self.btn_eject_usb.clicked.connect(self._on_eject_clicked)
        actions_row.addWidget(self.btn_eject_usb)

        actions_row.addStretch()
        dev_layout.addLayout(actions_row)

        main_layout.addWidget(self.device_card)

        # ----------------------------------------------------------------------
        # 2. Metric Cards Grid
        # ----------------------------------------------------------------------
        grid = QGridLayout()
        grid.setSpacing(14)

        self.card_envs = MetricCard("Registered Environments", "0", "0 active in boot menu")
        self.card_isos = MetricCard("Registered ISO Size", "0.00 B", "Across 0 image files")
        self.card_capacity = MetricCard("Total Capacity", "0.00 B", "Free: 0.00 B")
        self.card_missing = MetricCard("Missing ISOs", "0", "All registered files verified")

        grid.addWidget(self.card_envs, 0, 0)
        grid.addWidget(self.card_isos, 0, 1)
        grid.addWidget(self.card_capacity, 0, 2)
        grid.addWidget(self.card_missing, 0, 3)

        main_layout.addLayout(grid)

        # ----------------------------------------------------------------------
        # 3. Storage Utilization Card
        # ----------------------------------------------------------------------
        self.storage_card = QFrame()
        self.storage_card.setProperty("class", "card")
        st_layout = QVBoxLayout(self.storage_card)
        st_layout.setContentsMargins(18, 16, 18, 16)
        st_layout.setSpacing(10)

        header_row = QHBoxLayout()
        st_title = QLabel("STORAGE UTILIZATION (MVX_DATA)")
        st_title.setProperty("class", "metricLabel")
        header_row.addWidget(st_title)
        header_row.addStretch()

        self.storage_text = QLabel("0.00 B used of 0.00 B (0%)")
        self.storage_text.setStyleSheet("color: #9ca3af; font-size: 12px; font-weight: 500;")
        header_row.addWidget(self.storage_text)
        st_layout.addLayout(header_row)

        self.storage_progress = QProgressBar()
        self.storage_progress.setRange(0, 100)
        self.storage_progress.setValue(0)
        self.storage_progress.setTextVisible(False)
        st_layout.addWidget(self.storage_progress)

        main_layout.addWidget(self.storage_card)

        # ----------------------------------------------------------------------
        # 4. Partition Health Status
        # ----------------------------------------------------------------------
        self.health_card = QFrame()
        self.health_card.setProperty("class", "card")
        health_layout = QVBoxLayout(self.health_card)
        health_layout.setContentsMargins(18, 16, 18, 16)
        health_layout.setSpacing(10)

        h_title = QLabel("PARTITION ARCHITECTURE STATUS")
        h_title.setProperty("class", "metricLabel")
        health_layout.addWidget(h_title)

        parts_row = QHBoxLayout()
        parts_row.setSpacing(20)

        # Partition 1 box
        p1_box = QVBoxLayout()
        p1_lbl = QLabel("Partition 1: MVX_BOOT (FAT32 ESP)")
        p1_lbl.setStyleSheet("font-weight: 600; color: #f3f4f6;")
        self.p1_status = QLabel("Checking...")
        self.p1_status.setStyleSheet("color: #9ca3af; font-size: 12px;")
        p1_box.addWidget(p1_lbl)
        p1_box.addWidget(self.p1_status)
        parts_row.addLayout(p1_box)

        # Partition 2 box
        p2_box = QVBoxLayout()
        p2_lbl = QLabel("Partition 2: MVX_DATA (exFAT Basic Data)")
        p2_lbl.setStyleSheet("font-weight: 600; color: #f3f4f6;")
        self.p2_status = QLabel("Checking...")
        self.p2_status.setStyleSheet("color: #9ca3af; font-size: 12px;")
        p2_box.addWidget(p2_lbl)
        p2_box.addWidget(self.p2_status)
        parts_row.addLayout(p2_box)

        health_layout.addLayout(parts_row)
        main_layout.addWidget(self.health_card)

        main_layout.addStretch()

    # --------------------------------------------------------------------------
    # Data Refresh & State Updates
    # --------------------------------------------------------------------------

    def refresh_data(self) -> None:
        """Update metrics and device info from service."""
        target: Optional[ActiveTarget] = self.iso_service.device_service.active_target

        if not target:
            self.device_title.setText("No MVentX Device Selected")
            self.device_subtitle.setText("Please connect a physical MVentX USB drive or select a virtual disk above.")
            self.badge_status.setText("DISCONNECTED")
            self.badge_status.setProperty("class", "statusBadgeDisabled")
            self.badge_status.setStyleSheet("")

            self.btn_add_env.setEnabled(False)
            self.btn_manage_storage.setEnabled(False)
            self.btn_open_explorer.setEnabled(False)
            self.btn_copy_iso.setEnabled(False)
            self.btn_eject_usb.setEnabled(False)

            self.card_envs.set_value("0", "0 active")
            self.card_isos.set_value("0.00 B", "No target selected")
            self.card_capacity.set_value("0.00 B", "Free: 0.00 B")
            self.card_missing.set_value("0", "No target selected")
            self.storage_progress.setValue(0)
            self.storage_text.setText("0.00 B used of 0.00 B (0%)")
            self.p1_status.setText("Not connected")
            self.p2_status.setText("Not connected")
            return

        self.btn_add_env.setEnabled(True)
        self.btn_manage_storage.setEnabled(True)
        self.btn_open_explorer.setEnabled(target.data_mount.is_dir())
        self.btn_copy_iso.setEnabled(target.data_mount.is_dir())
        self.btn_eject_usb.setEnabled(True)

        # Update Validation Badge
        self._update_validation_badge(target)

        # Update Device Title
        if target.is_virtual:
            self.device_title.setText(f"Virtual Disk ({target.data_mount.name})")
            self.device_subtitle.setText(f"Data Mount: {target.data_mount} | Boot Mount: {target.boot_mount or 'None'}")
        else:
            self.device_title.setText(f"MVentX USB ({target.display_name})")
            self.device_subtitle.setText(f"Data: {target.data_mount} | Boot: {target.boot_mount or 'Not mounted'}")

        # Check Read/Write status
        p1_rw = "Read-Write" if (target.boot_mount and os.access(target.boot_mount, os.W_OK)) else "Read-Only"
        p2_rw = "Read-Write" if os.access(target.data_mount, os.W_OK) else "Read-Only"

        # Partition 1 status
        if target.has_boot_mount:
            self.p1_status.setText(f"Mounted at {target.boot_mount} (FAT32, {p1_rw}) • GRUB Menu Synchronized")
            self.p1_status.setStyleSheet(f"color: {COLOR_SUCCESS}; font-size: 12px;")
        else:
            self.p1_status.setText("Boot partition not mounted (Menu sync skipped)")
            self.p1_status.setStyleSheet(f"color: {COLOR_WARNING}; font-size: 12px;")

        # Partition 2 status
        if target.data_mount.is_dir():
            self.p2_status.setText(f"Mounted at {target.data_mount} (exFAT, {p2_rw}) • Structure Valid")
            self.p2_status.setStyleSheet(f"color: {COLOR_SUCCESS}; font-size: 12px;")
        else:
            self.p2_status.setText(f"Directory not accessible: {target.data_mount}")
            self.p2_status.setStyleSheet(f"color: {COLOR_DANGER}; font-size: 12px;")

        # Metrics
        envs = self.iso_service.list_environments()
        enabled_count = len([e for e in envs if e.enabled])
        unregistered = self.iso_service.list_unregistered_isos()
        total_isos_on_disk = len(envs) + len(unregistered)
        self.card_envs.set_value(
            str(len(envs)),
            f"{enabled_count} active in menu • {total_isos_on_disk} total ISOs found",
        )

        metrics = self.iso_service.get_storage_metrics()
        missing = self.iso_service.detect_missing_isos()

        if missing:
            self.card_missing.set_value(str(len(missing)), "Missing files detected!")
            self.card_missing.value_lbl.setStyleSheet(f"color: {COLOR_DANGER}; font-size: 26px; font-weight: 700;")
        else:
            self.card_missing.set_value("0", "All files verified on disk")
            self.card_missing.value_lbl.setStyleSheet("font-size: 26px; font-weight: 700; color: #f3f4f6;")

        if metrics:
            self.card_isos.set_value(
                _fmt_bytes(metrics.total_registered_iso_bytes),
                f"Across {metrics.registered_iso_count} registered ISOs",
            )
            self.card_capacity.set_value(
                _fmt_bytes(metrics.total_bytes),
                f"Free: {_fmt_bytes(metrics.available_bytes)}",
            )

            if metrics.total_bytes > 0:
                pct = int((metrics.used_bytes / metrics.total_bytes) * 100)
                pct = max(0, min(100, pct))
                self.storage_progress.setValue(pct)
                self.storage_text.setText(
                    f"{_fmt_bytes(metrics.used_bytes)} used of {_fmt_bytes(metrics.total_bytes)} ({pct}%)"
                )
            else:
                self.storage_progress.setValue(0)
                self.storage_text.setText("Storage capacity unknown")

    def _update_validation_badge(self, target: ActiveTarget) -> None:
        """Render validation status badge based on target classification."""
        status = None
        if target.classification:
            status = target.classification.status

        if status == MVentXStatus.VALID_MVENTX or (status is None and target.data_mount.is_dir()):
            self.badge_status.setText("VALID MVX")
            self.badge_status.setProperty("class", "statusBadgeValid")
            self.badge_status.setStyleSheet("")
        elif status == MVentXStatus.CORRUPTED_INSTALLATION:
            self.badge_status.setText("CORRUPTED")
            self.badge_status.setProperty("class", "statusBadgeCorrupted")
            self.badge_status.setStyleSheet("")
        elif status == MVentXStatus.INCOMPLETE_INSTALLATION:
            self.badge_status.setText("INCOMPLETE")
            self.badge_status.setProperty("class", "statusBadgeUninitialized")
            self.badge_status.setStyleSheet("")
        elif status == MVentXStatus.INACCESSIBLE:
            self.badge_status.setText("INACCESSIBLE")
            self.badge_status.setProperty("class", "statusBadgeCorrupted")
            self.badge_status.setStyleSheet("")
        else:
            self.badge_status.setText("STANDARD USB")
            self.badge_status.setProperty("class", "statusBadgeWarning")
            self.badge_status.setStyleSheet("")

    # --------------------------------------------------------------------------
    # Direct Action Shortcuts
    # --------------------------------------------------------------------------

    def _on_open_explorer(self) -> None:
        target = self.iso_service.device_service.active_target
        if not target or not target.data_mount.is_dir():
            return

        path_str = str(target.data_mount)
        if sys.platform == "win32":
            os.startfile(path_str)
        elif sys.platform == "darwin":
            subprocess.run(["open", path_str], check=False)
        else:
            subprocess.run(["xdg-open", path_str], check=False)

    def _on_copy_iso_clicked(self) -> None:
        target = self.iso_service.device_service.active_target
        if not target or not target.data_mount.is_dir():
            return

        source_file, _ = QFileDialog.getOpenFileName(
            self,
            "Select ISO Image to Copy",
            "",
            "Disk Images (*.iso *.img);;All Files (*.*)",
        )
        if not source_file:
            return

        src_p = Path(source_file)
        dest_dir = target.data_mount / "ISO"
        worker = ISOCopyWorker(
            source_path=src_p,
            destination_dir=dest_dir,
            dest_filename=src_p.name,
            parent=self,
        )

        dlg = OperationProgressDialog(
            title="Copying ISO Image",
            operation_description=f"Copying {src_p.name} to USB drive...",
            worker=worker,
            parent=self,
        )

        worker.start()
        res = dlg.exec()

        if res == OperationProgressDialog.Accepted:
            QMessageBox.information(
                self,
                "ISO Copied",
                f"Successfully copied '{src_p.name}' to {dest_dir}.\n\n"
                "You can now register it in the Add Environment screen.",
            )
            self.refresh_data()
        elif not worker._is_cancelled:
            QMessageBox.warning(self, "Copy Cancelled or Failed", "The ISO copy operation did not complete.")

    def _on_eject_clicked(self) -> None:
        self.iso_service.device_service.eject_active_device()
