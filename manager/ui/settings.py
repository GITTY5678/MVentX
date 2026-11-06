"""Settings Screen for MVentX Manager.

Provides controls for global GRUB boot menu title and timeout settings,
inspection and restoration of timestamped configuration backups, and application info.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from manager.services.iso_service import ISOService
from manager.ui.styles import COLOR_ACCENT, COLOR_SUCCESS


class SettingsScreen(QWidget):
    """Settings screen."""

    def __init__(self, iso_service: ISOService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.iso_service = iso_service

        self._setup_ui()
        self.iso_service.environments_changed.connect(self.load_settings)
        self.iso_service.device_service.target_changed.connect(self.load_settings)

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(16)

        # Header Title
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        h_title = QLabel("System & Boot Settings")
        h_title.setStyleSheet("font-size: 18px; font-weight: 700; color: #f3f4f6;")
        h_sub = QLabel("Configure global bootloader preferences and manage configuration backups.")
        h_sub.setStyleSheet("color: #9ca3af; font-size: 12px;")
        title_box.addWidget(h_title)
        title_box.addWidget(h_sub)
        main_layout.addLayout(title_box)

        # 1. Boot Configuration Card
        self.boot_card = QFrame()
        self.boot_card.setProperty("class", "card")
        b_layout = QVBoxLayout(self.boot_card)
        b_layout.setSpacing(12)

        b_hdr = QLabel("GLOBAL GRUB BOOT MENU PREFERENCES")
        b_hdr.setProperty("class", "metricLabel")
        b_layout.addWidget(b_hdr)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("MVentX Multiboot System")
        form.addRow("Menu Title*:", self.title_edit)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(0, 300)
        self.timeout_spin.setSuffix(" seconds")
        self.timeout_spin.setValue(10)
        form.addRow("Timeout:", self.timeout_spin)

        self.default_env_combo = QComboBox()
        form.addRow("Default Entry:", self.default_env_combo)

        b_layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_save_boot = QPushButton("Save Boot Settings")
        self.btn_save_boot.setProperty("class", "primaryButton")
        self.btn_save_boot.clicked.connect(self._on_save_boot_settings)
        btn_row.addWidget(self.btn_save_boot)
        b_layout.addLayout(btn_row)

        main_layout.addWidget(self.boot_card)

        # 2. Backups Manager Card
        self.backup_card = QFrame()
        self.backup_card.setProperty("class", "card")
        bk_layout = QVBoxLayout(self.backup_card)
        bk_layout.setSpacing(10)

        bk_hdr = QLabel("CONFIGURATION BACKUPS (MVX_DATA/backups/)")
        bk_hdr.setProperty("class", "metricLabel")
        bk_layout.addWidget(bk_hdr)

        self.backup_table = QTableWidget()
        self.backup_table.setColumnCount(3)
        self.backup_table.setHorizontalHeaderLabels(["Backup Filename", "Created Date / Time", "File Size"])
        self.backup_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.backup_table.setSelectionMode(QTableWidget.SingleSelection)
        self.backup_table.verticalHeader().setVisible(False)
        self.backup_table.horizontalHeader().setStretchLastSection(True)
        self.backup_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.backup_table.setFixedHeight(140)
        bk_layout.addWidget(self.backup_table)

        bk_btns = QHBoxLayout()
        bk_btns.addStretch()
        self.btn_restore = QPushButton("Restore Selected Backup")
        self.btn_restore.clicked.connect(self._on_restore_clicked)
        bk_btns.addWidget(self.btn_restore)
        bk_layout.addLayout(bk_btns)

        main_layout.addWidget(self.backup_card)

        # 3. About Card
        self.about_card = QFrame()
        self.about_card.setProperty("class", "card")
        ab_layout = QVBoxLayout(self.about_card)
        ab_layout.setSpacing(6)

        ab_title = QLabel("ABOUT MVENTX")
        ab_title.setProperty("class", "metricLabel")
        ab_desc = QLabel("MVentX (Multi-Environment Boot Experience) v0.1.0\n"
                         "Decoupled UEFI + GRUB 2 boot engine and PySide6 host management suite.\n"
                         "Licensed under GNU General Public License v3.0 (GPLv3).")
        ab_desc.setStyleSheet("color: #9ca3af; font-size: 12px;")
        ab_layout.addWidget(ab_title)
        ab_layout.addWidget(ab_desc)
        main_layout.addWidget(self.about_card)

        main_layout.addStretch()

    def load_settings(self) -> None:
        """Load settings from active mventx.json configuration."""
        meta = self.iso_service.get_config_metadata()
        self.title_edit.setText(meta.get("title", "MVentX Multiboot System"))
        self.timeout_spin.setValue(meta.get("timeout", 10))

        # Populate default entry combo
        self.default_env_combo.clear()
        self.default_env_combo.addItem("None (First entry)", "")

        envs = self.iso_service.list_environments()
        default_id = meta.get("default_entry_id")
        selected_idx = 0

        for idx, env in enumerate(envs, start=1):
            self.default_env_combo.addItem(f"{env.display_name} ({env.id})", env.id)
            if default_id and env.id == default_id:
                selected_idx = idx

        self.default_env_combo.setCurrentIndex(selected_idx)

        # Populate backups table
        self._load_backups()

    def _load_backups(self) -> None:
        backups = self.iso_service.list_backups()
        self.backup_table.setRowCount(len(backups))

        for row, b_path in enumerate(backups):
            name_item = QTableWidgetItem(b_path.name)
            name_item.setData(Qt.UserRole, str(b_path))
            self.backup_table.setItem(row, 0, name_item)

            stat = b_path.stat()
            dt = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            time_item = QTableWidgetItem(dt)
            self.backup_table.setItem(row, 1, time_item)

            size_item = QTableWidgetItem(f"{stat.st_size} bytes")
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.backup_table.setItem(row, 2, size_item)

    def _on_save_boot_settings(self) -> None:
        title = self.title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "Invalid Title", "Menu title cannot be empty.")
            return

        timeout = self.timeout_spin.value()
        default_id = self.default_env_combo.currentData() or None

        success = self.iso_service.update_config_metadata(
            title=title,
            timeout=timeout,
            default_id=default_id,
        )
        if success:
            QMessageBox.information(
                self,
                "Settings Saved",
                "Boot settings updated and synchronized with GRUB menu configuration.",
            )
            self._load_backups()

    def _on_restore_clicked(self) -> None:
        selected_row = self.backup_table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "No Selection", "Please select a backup from the table to restore.")
            return

        item = self.backup_table.item(selected_row, 0)
        if not item:
            return

        backup_path = Path(item.data(Qt.UserRole))

        confirm = QMessageBox.question(
            self,
            "Confirm Restore",
            f"Restore configuration from backup '{backup_path.name}'?\n\n"
            "This will overwrite the active configuration and regenerate the GRUB boot menu.",
            QMessageBox.Yes | QMessageBox.No,
        )

        if confirm == QMessageBox.Yes:
            if self.iso_service.restore_backup(backup_path):
                QMessageBox.information(self, "Restored", f"Successfully restored {backup_path.name}.")
                self.load_settings()

