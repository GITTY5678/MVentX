"""USB Installation and Virtual Disk Preparation Screen for MVentX Manager.

Provides a dedicated interface to:
1. Configure target storage (Virtual Disk Image or Physical Device).
2. Preview GPT partition layout (MVX_BOOT FAT32 + MVX_DATA exFAT).
3. Enforce strict safety confirmation before destructive operations.
4. Execute installation asynchronously with real-time stage progress reporting.
5. Display structured verification results upon completion.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.storage.device_info import DeviceInfo, DeviceType
from core.storage.device_manager import DeviceManager
from installer.installation_service import InstallationResult, InstallationService
from installer.installation_verifier import InstallationVerificationReport
from installer.partition_planner import (
    DEFAULT_ESP_SIZE_MB,
    MIN_DISK_CAPACITY_MB,
    MIN_ESP_SIZE_MB,
    PartitionPlan,
    PartitionPlanner,
)
from manager.services.device_service import DeviceService
from manager.ui.styles import (
    COLOR_ACCENT,
    COLOR_BORDER,
    COLOR_DANGER,
    COLOR_SUCCESS,
    COLOR_SURFACE,
    COLOR_WARNING,
)


class InstallationWorker(QObject):
    """Asynchronous worker executing disk installation off the main GUI thread."""

    progress_updated = Signal(int, int, str)
    installation_finished = Signal(object)  # Emits InstallationResult

    def __init__(
        self,
        target_path: Path,
        disk_size_mb: int,
        esp_size_mb: int,
        menu_title: str,
        menu_timeout: int,
    ) -> None:
        super().__init__()
        self.target_path = target_path
        self.disk_size_mb = disk_size_mb
        self.esp_size_mb = esp_size_mb
        self.menu_title = menu_title
        self.menu_timeout = menu_timeout
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        service = InstallationService(allow_physical_formatting=False)

        def on_progress(step: int, total: int, msg: str) -> None:
            self.progress_updated.emit(step, total, msg)

        res = service.install_to_virtual_disk(
            target_image_path=self.target_path,
            disk_size_mb=self.disk_size_mb,
            esp_size_mb=self.esp_size_mb,
            menu_title=self.menu_title,
            menu_timeout=self.menu_timeout,
            progress_callback=on_progress,
            is_cancelled=lambda: self._cancelled,
        )
        self.installation_finished.emit(res)


class USBInstallerScreen(QWidget):
    """USB Provisioning & Virtual Disk Preparation Screen."""

    navigate_requested = Signal(str)

    def __init__(
        self,
        device_service: DeviceService,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.device_service = device_service
        self.device_manager = DeviceManager(allow_physical_formatting=False)
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[InstallationWorker] = None

        self._setup_ui()
        self._update_preview()

    def _setup_ui(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(0, 0, 0, 24)
        main_layout.setSpacing(18)

        # 1. Header
        header_box = QVBoxLayout()
        header_box.setSpacing(4)
        h_title = QLabel("USB Installation & Virtual Disk Preparation")
        h_title.setStyleSheet("font-size: 20px; font-weight: 700; color: #f3f4f6;")
        h_desc = QLabel(
            "Prepare a storage target with the MVentX dual-partition GPT architecture, "
            "GRUB 2.12 EFI bootloader, and default configuration."
        )
        h_desc.setStyleSheet("color: #9ca3af; font-size: 13px;")
        header_box.addWidget(h_title)
        header_box.addWidget(h_desc)
        main_layout.addLayout(header_box)

        # 2. Safety Shield Banner
        safety_banner = QFrame()
        safety_banner.setStyleSheet(
            "background-color: rgba(59, 130, 246, 0.12); "
            "border: 1px solid rgba(59, 130, 246, 0.4); "
            "border-radius: 6px; padding: 12px;"
        )
        sb_layout = QVBoxLayout(safety_banner)
        sb_layout.setSpacing(4)
        sb_head = QLabel("Safety Protection Policy Active (Milestone 9 Virtual-First Validation)")
        sb_head.setStyleSheet("color: #60a5fa; font-weight: 700; font-size: 13px;")
        sb_text = QLabel(
            "Physical disk formatting remains locked by default to protect host drives. "
            "Virtual raw disk images (.img / .vhd) are supported with complete UEFI + QEMU verification."
        )
        sb_text.setStyleSheet("color: #cbd5e1; font-size: 12px;")
        sb_layout.addWidget(sb_head)
        sb_layout.addWidget(sb_text)
        main_layout.addWidget(safety_banner)

        # 3. Target Configuration Card
        target_card = QFrame()
        target_card.setProperty("class", "card")
        tc_layout = QVBoxLayout(target_card)
        tc_layout.setSpacing(14)

        tc_title = QLabel("1. Target Storage Selection")
        tc_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #f3f4f6;")
        tc_layout.addWidget(tc_title)

        # Target Type Radio Buttons
        self.radio_group = QButtonGroup(self)
        self.radio_virtual = QRadioButton("Virtual Raw Disk Image (.img / .vhd) [Recommended for Testing]")
        self.radio_virtual.setChecked(True)
        self.radio_physical = QRadioButton("Physical Storage Media (Protected / Disabled by Policy)")
        self.radio_physical.setEnabled(False)
        self.radio_group.addButton(self.radio_virtual, 0)
        self.radio_group.addButton(self.radio_physical, 1)

        tc_layout.addWidget(self.radio_virtual)
        tc_layout.addWidget(self.radio_physical)

        # Virtual Target Path Row
        vpath_layout = QHBoxLayout()
        vpath_layout.setSpacing(10)
        vpath_lbl = QLabel("Output Image Path:")
        vpath_lbl.setMinimumWidth(130)
        self.vpath_edit = QLineEdit()
        default_img = Path("build") / "mventx_usb_test.img"
        self.vpath_edit.setText(str(default_img.resolve()))
        self.vpath_edit.textChanged.connect(self._update_preview)

        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._on_browse_target)

        vpath_layout.addWidget(vpath_lbl)
        vpath_layout.addWidget(self.vpath_edit, stretch=1)
        vpath_layout.addWidget(self.btn_browse)
        tc_layout.addLayout(vpath_layout)

        # Sizing Grid
        size_grid = QGridLayout()
        size_grid.setSpacing(12)

        size_lbl = QLabel("Total Disk Capacity:")
        size_lbl.setMinimumWidth(130)
        self.disk_size_combo = QComboBox()
        self.disk_size_combo.addItem("256 MiB (Compact Virtual Test Disk)", 256)
        self.disk_size_combo.addItem("512 MiB (Standard Virtual Disk)", 512)
        self.disk_size_combo.addItem("1024 MiB (1 GiB Test Disk)", 1024)
        self.disk_size_combo.addItem("2048 MiB (2 GiB Virtual Disk)", 2048)
        self.disk_size_combo.currentIndexChanged.connect(self._update_preview)

        esp_lbl = QLabel("EFI Partition Size (MVX_BOOT):")
        self.esp_size_combo = QComboBox()
        self.esp_size_combo.addItem("64 MiB (Recommended Standard)", 64)
        self.esp_size_combo.addItem("32 MiB (Minimum Practical)", 32)
        self.esp_size_combo.addItem("128 MiB (Extended Module Buffer)", 128)
        self.esp_size_combo.addItem("256 MiB (Large Diagnostics Buffer)", 256)
        self.esp_size_combo.currentIndexChanged.connect(self._update_preview)

        size_grid.addWidget(size_lbl, 0, 0)
        size_grid.addWidget(self.disk_size_combo, 0, 1)
        size_grid.addWidget(esp_lbl, 1, 0)
        size_grid.addWidget(self.esp_size_combo, 1, 1)
        tc_layout.addLayout(size_grid)

        main_layout.addWidget(target_card)

        # 4. Partition Layout Preview Card
        preview_card = QFrame()
        preview_card.setProperty("class", "card")
        pc_layout = QVBoxLayout(preview_card)
        pc_layout.setSpacing(12)

        pc_title = QLabel("2. Partition Layout Preview")
        pc_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #f3f4f6;")
        pc_layout.addWidget(pc_title)

        # Visual layout bar
        self.layout_bar = QHBoxLayout()
        self.layout_bar.setSpacing(4)

        self.bar_esp = QLabel("MVX_BOOT\nFAT32\n64 MiB")
        self.bar_esp.setAlignment(Qt.AlignCenter)
        self.bar_esp.setStyleSheet(
            "background-color: #0284c7; color: #ffffff; font-weight: 700; "
            "border-radius: 4px; padding: 14px 4px; font-size: 11px;"
        )

        self.bar_data = QLabel("MVX_DATA\nexFAT\n192 MiB")
        self.bar_data.setAlignment(Qt.AlignCenter)
        self.bar_data.setStyleSheet(
            "background-color: #059669; color: #ffffff; font-weight: 700; "
            "border-radius: 4px; padding: 14px 4px; font-size: 11px;"
        )

        self.layout_bar.addWidget(self.bar_esp, stretch=1)
        self.layout_bar.addWidget(self.bar_data, stretch=3)
        pc_layout.addLayout(self.layout_bar)

        self.preview_details = QLabel()
        self.preview_details.setStyleSheet("color: #9ca3af; font-size: 12px; line-height: 1.4;")
        pc_layout.addWidget(self.preview_details)

        main_layout.addWidget(preview_card)

        # 5. Confirmation & Progress Card
        action_card = QFrame()
        action_card.setProperty("class", "card")
        ac_layout = QVBoxLayout(action_card)
        ac_layout.setSpacing(14)

        ac_title = QLabel("3. Execution & Verification")
        ac_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #f3f4f6;")
        ac_layout.addWidget(ac_title)

        self.chk_confirm = QCheckBox(
            "I understand that proceeding will initialize the partition table and format the target image."
        )
        self.chk_confirm.setStyleSheet("color: #f3f4f6; font-weight: 600;")
        self.chk_confirm.stateChanged.connect(self._on_confirm_toggled)
        ac_layout.addWidget(self.chk_confirm)

        self.btn_install = QPushButton("Start Installation & Verification")
        self.btn_install.setStyleSheet(
            "background-color: #3b82f6; color: #ffffff; font-weight: 700; "
            "font-size: 14px; padding: 10px 20px; border-radius: 6px;"
        )
        self.btn_install.setEnabled(False)
        self.btn_install.clicked.connect(self._on_start_installation)
        ac_layout.addWidget(self.btn_install)

        # Progress elements (hidden until start)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 12)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        ac_layout.addWidget(self.progress_bar)

        self.status_lbl = QLabel()
        self.status_lbl.setStyleSheet("color: #60a5fa; font-weight: 600; font-size: 12px;")
        self.status_lbl.setVisible(False)
        ac_layout.addWidget(self.status_lbl)

        # Verification Report Section
        self.report_box = QFrame()
        self.report_box.setStyleSheet(
            "background-color: #1a1c22; border: 1px solid #2e323b; "
            "border-radius: 6px; padding: 12px;"
        )
        self.report_box.setVisible(False)
        self.rb_layout = QVBoxLayout(self.report_box)
        self.rb_layout.setSpacing(6)
        ac_layout.addWidget(self.report_box)

        main_layout.addWidget(action_card)
        scroll.setWidget(container)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(scroll)

    def _update_preview(self) -> None:
        disk_size_mb = self.disk_size_combo.currentData() or 256
        esp_size_mb = self.esp_size_combo.currentData() or 64

        try:
            plan = PartitionPlanner.calculate_plan(
                disk_size_bytes=disk_size_mb * 1024 * 1024,
                esp_size_mb=esp_size_mb,
            )
            data_mb = plan.data_partition.size_mb
            self.bar_esp.setText(f"MVX_BOOT\nFAT32 ESP\n{esp_size_mb} MiB")
            self.bar_data.setText(f"MVX_DATA\nexFAT Data\n{data_mb:.1f} MiB")

            self.layout_bar.setStretch(0, int(esp_size_mb))
            self.layout_bar.setStretch(1, int(data_mb))

            self.preview_details.setText(
                f"• Partition 1 (MVX_BOOT): LBA {plan.esp_partition.start_lba:,} .. {plan.esp_partition.end_lba:,} "
                f"({esp_size_mb} MiB, Type: EFI System Partition)\n"
                f"• Partition 2 (MVX_DATA): LBA {plan.data_partition.start_lba:,} .. {plan.data_partition.end_lba:,} "
                f"({data_mb:.1f} MiB, Type: Basic Data Partition)\n"
                f"• Alignment: 1 MiB ({plan.first_usable_lba} sectors) | Total Sectors: {plan.total_sectors:,}"
            )
        except Exception as exc:
            self.preview_details.setText(f"Layout calculation error: {exc}")

    def _on_confirm_toggled(self) -> None:
        self.btn_install.setEnabled(self.chk_confirm.isChecked())

    def _on_browse_target(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output Disk Image Destination",
            self.vpath_edit.text() or "mventx_disk.img",
            "Raw Disk Images (*.img *.vhd);;All Files (*.*)",
        )
        if filename:
            self.vpath_edit.setText(filename)

    def _on_start_installation(self) -> None:
        target_str = self.vpath_edit.text().strip()
        if not target_str:
            QMessageBox.critical(self, "Invalid Path", "Please specify a destination image file path.")
            return

        target_path = Path(target_str)
        disk_mb = self.disk_size_combo.currentData() or 256
        esp_mb = self.esp_size_combo.currentData() or 64

        # Disable controls during run
        self.btn_install.setEnabled(False)
        self.chk_confirm.setEnabled(False)
        self.vpath_edit.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.disk_size_combo.setEnabled(False)
        self.esp_size_combo.setEnabled(False)

        # Clear previous report
        while self.rb_layout.count() > 0:
            item = self.rb_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.report_box.setVisible(False)

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_lbl.setVisible(True)
        self.status_lbl.setText("Starting installation pipeline...")

        # Spawn background QThread worker
        self._worker_thread = QThread()
        self._worker = InstallationWorker(
            target_path=target_path,
            disk_size_mb=disk_mb,
            esp_size_mb=esp_mb,
            menu_title="MVentX Multiboot System",
            menu_timeout=10,
        )
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.progress_updated.connect(self._on_worker_progress)
        self._worker.installation_finished.connect(self._on_worker_finished)
        self._worker_thread.start()

    def _on_worker_progress(self, step: int, total: int, message: str) -> None:
        self.progress_bar.setValue(step)
        self.status_lbl.setText(f"Step {step}/{total}: {message}")

    def _on_worker_finished(self, result: InstallationResult) -> None:
        # Stop worker thread
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
            self._worker = None

        # Re-enable inputs
        self.btn_install.setEnabled(True)
        self.chk_confirm.setEnabled(True)
        self.vpath_edit.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.disk_size_combo.setEnabled(True)
        self.esp_size_combo.setEnabled(True)

        self.report_box.setVisible(True)

        if result.success and result.verification_report and result.verification_report.is_valid:
            self.status_lbl.setText("✓ Installation and Verification Completed Successfully!")
            self.status_lbl.setStyleSheet("color: #10b981; font-weight: 700; font-size: 13px;")

            rep = result.verification_report
            head = QLabel(f"Verification Report: {rep.passed_count}/{rep.total_count} Checks Passed")
            head.setStyleSheet("color: #10b981; font-weight: 700; font-size: 14px;")
            self.rb_layout.addWidget(head)

            for c in rep.checks:
                row = QLabel(f"✓ {c.name}: {c.message}")
                row.setStyleSheet("color: #cbd5e1; font-size: 12px;")
                self.rb_layout.addWidget(row)

            QMessageBox.information(
                self,
                "Installation Successful",
                f"MVentX disk image prepared successfully at:\n{result.target_path}\n\n"
                f"All {rep.passed_count} structural and filesystem verification checks passed.",
            )
        else:
            err = result.error_message or "Unknown verification error"
            self.status_lbl.setText("✗ Installation Failed")
            self.status_lbl.setStyleSheet("color: #ef4444; font-weight: 700; font-size: 13px;")

            head = QLabel("Installation Errors:")
            head.setStyleSheet("color: #ef4444; font-weight: 700;")
            self.rb_layout.addWidget(head)

            detail = QLabel(err)
            detail.setStyleSheet("color: #fca5a5; font-size: 12px;")
            self.rb_layout.addWidget(detail)

            QMessageBox.critical(self, "Installation Failed", f"Disk preparation failed:\n{err}")

