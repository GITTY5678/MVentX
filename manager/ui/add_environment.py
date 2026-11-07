"""Add Environment Screen for MVentX Manager.

Guided workflow for manually selecting, validating, configuring,
and registering an ISO image into the authoritative environment store.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.iso_manager import ISOValidationResult, ISOValidator
from core.iso_manager.windows_iso_inspector import WindowsISOInspector, WindowsInspectionResult
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


class AddEnvironmentScreen(QWidget):
    """Screen guiding manual ISO image registration."""

    navigate_requested = Signal(str)

    def __init__(self, iso_service: ISOService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.iso_service = iso_service
        self._selected_iso_path: Optional[Path] = None
        self._validation_result: Optional[ISOValidationResult] = None
        self._windows_inspection: Optional[WindowsInspectionResult] = None

        self._setup_ui()
        self.iso_service.device_service.target_changed.connect(self.refresh_unregistered_list)

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(16)

        # Header Title
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        h_title = QLabel("Register New Boot Environment")
        h_title.setStyleSheet("font-size: 18px; font-weight: 700; color: #f3f4f6;")
        h_sub = QLabel(
            "Select an ISO image located on the data partition to validate its filesystem "
            "and configure its boot menu entry. No files are auto-discovered."
        )
        h_sub.setStyleSheet("color: #9ca3af; font-size: 12px;")
        title_box.addWidget(h_title)
        title_box.addWidget(h_sub)
        main_layout.addLayout(title_box)

        # 1. Step 1: ISO File Selection
        self.step1_card = QFrame()
        self.step1_card.setProperty("class", "card")
        s1_layout = QVBoxLayout(self.step1_card)
        s1_layout.setSpacing(10)

        s1_title = QLabel("STEP 1: SELECT ISO IMAGE FILE")
        s1_title.setProperty("class", "metricLabel")
        s1_layout.addWidget(s1_title)

        pick_row = QHBoxLayout()
        pick_row.setSpacing(10)

        self.iso_combo = QComboBox()
        self.iso_combo.setPlaceholderText("Select an unregistered ISO from MVX_DATA/ISO/...")
        self.iso_combo.currentIndexChanged.connect(self._on_combo_selected)
        pick_row.addWidget(self.iso_combo, stretch=1)

        self.btn_browse = QPushButton("Browse Files...")
        self.btn_browse.clicked.connect(self._on_browse_clicked)
        pick_row.addWidget(self.btn_browse)

        s1_layout.addLayout(pick_row)
        main_layout.addWidget(self.step1_card)

        # 2. Step 2: Validation Status Card
        self.val_card = QFrame()
        self.val_card.setProperty("class", "card")
        val_layout = QVBoxLayout(self.val_card)
        val_layout.setSpacing(8)

        v_title = QLabel("STEP 2: FILESYSTEM & STRUCTURE VALIDATION")
        v_title.setProperty("class", "metricLabel")
        val_layout.addWidget(v_title)

        self.val_status_lbl = QLabel("No ISO file selected.")
        self.val_status_lbl.setStyleSheet("color: #9ca3af; font-weight: 500;")
        val_layout.addWidget(self.val_status_lbl)

        self.val_details_lbl = QLabel("")
        self.val_details_lbl.setStyleSheet("color: #6b7280; font-size: 11px;")
        self.val_details_lbl.setWordWrap(True)
        val_layout.addWidget(self.val_details_lbl)

        main_layout.addWidget(self.val_card)

        # 3. Step 3: Environment Configuration Form
        self.cfg_card = QFrame()
        self.cfg_card.setProperty("class", "card")
        cfg_layout = QVBoxLayout(self.cfg_card)
        cfg_layout.setSpacing(12)

        c_title = QLabel("STEP 3: BOOT MENU CONFIGURATION")
        c_title.setProperty("class", "metricLabel")
        cfg_layout.addWidget(c_title)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Ubuntu 24.04 LTS (Noble Numbat)")
        form.addRow("Display Name*:", self.name_edit)

        self.method_combo = QComboBox()
        self.method_combo.addItem("linux_generic_loopback", "linux_generic_loopback")
        self.method_combo.addItem("arch_loopback", "arch_loopback")
        self.method_combo.addItem("debian_live_loopback", "debian_live_loopback")
        self.method_combo.addItem("windows_extracted_media", "windows_extracted_media")
        form.addRow("Boot Method:", self.method_combo)

        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("Optional description or release notes")
        form.addRow("Description:", self.desc_edit)

        self.args_edit = QLineEdit()
        self.args_edit.setPlaceholderText("Optional kernel command-line arguments (e.g. quiet splash)")
        form.addRow("Custom Args:", self.args_edit)

        self.enabled_cb = QCheckBox("Enable this environment in the active boot menu")
        self.enabled_cb.setChecked(True)
        form.addRow("", self.enabled_cb)

        self.hash_cb = QCheckBox("Compute and record SHA-256 cryptographic hash")
        self.hash_cb.setChecked(False)
        form.addRow("", self.hash_cb)

        cfg_layout.addLayout(form)
        main_layout.addWidget(self.cfg_card)

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(lambda: self.navigate_requested.emit("library"))
        btn_row.addWidget(self.btn_cancel)

        self.btn_register = QPushButton("Register Environment")
        self.btn_register.setProperty("class", "primaryButton")
        self.btn_register.setEnabled(False)
        self.btn_register.clicked.connect(self._on_register_clicked)
        btn_row.addWidget(self.btn_register)

        main_layout.addLayout(btn_row)
        main_layout.addStretch()

    def refresh_unregistered_list(self) -> None:
        """Scan ISO/ directory for candidate files."""
        self.iso_combo.blockSignals(True)
        self.iso_combo.clear()

        unregistered = self.iso_service.list_unregistered_isos()
        if unregistered:
            for p in unregistered:
                self.iso_combo.addItem(f"{p.name} ({_fmt_bytes(p.stat().st_size)})", str(p))
            self.iso_combo.setCurrentIndex(0)
            self._selected_iso_path = unregistered[0]
        else:
            self.iso_combo.addItem("No unregistered ISO files found in MVX_DATA/ISO/", None)
            self._selected_iso_path = None

        self.iso_combo.blockSignals(False)
        self._validate_current_selection()

    def _on_combo_selected(self, index: int) -> None:
        data = self.iso_combo.currentData()
        if data:
            self._selected_iso_path = Path(data)
        else:
            self._selected_iso_path = None
        self._validate_current_selection()

    def _on_browse_clicked(self) -> None:
        mgr = self.iso_service.get_registration_manager()
        start_dir = str(mgr.iso_directory) if mgr else ""

        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Select Bootable ISO Image",
            start_dir,
            "ISO Disc Images (*.iso *.ISO)",
        )
        if path_str:
            p = Path(path_str).resolve()
            # Check if inside ISO directory
            if mgr:
                try:
                    p.relative_to(mgr.iso_directory.resolve())
                except ValueError:
                    QMessageBox.warning(
                        self,
                        "External ISO File",
                        f"The selected ISO file '{p.name}' is outside the MVX_DATA/ISO/ directory.\n\n"
                        "Please copy it into the ISO folder on your MVentX drive before registering.",
                    )
                    return

            self._selected_iso_path = p
            # Add to combo if not present
            self.iso_combo.blockSignals(True)
            self.iso_combo.addItem(f"{p.name} ({_fmt_bytes(p.stat().st_size)})", str(p))
            self.iso_combo.setCurrentIndex(self.iso_combo.count() - 1)
            self.iso_combo.blockSignals(False)
            self._validate_current_selection()

    def _validate_current_selection(self) -> None:
        if not self._selected_iso_path or not self._selected_iso_path.is_file():
            self.val_status_lbl.setText("No ISO file selected.")
            self.val_status_lbl.setStyleSheet("color: #9ca3af; font-weight: 500;")
            self.val_details_lbl.setText("")
            self.btn_register.setEnabled(False)
            self._windows_inspection = None
            return

        res = self.iso_service.validate_iso(self._selected_iso_path)
        self._validation_result = res

        if res.is_valid:
            # Check for Windows Installation Media
            win_inspector = WindowsISOInspector(self._selected_iso_path)
            win_res = win_inspector.inspect()
            self._windows_inspection = win_res if win_res.is_windows_media else None

            if win_res.is_windows_media:
                status_text = (
                    f"WINDOWS MEDIA DETECTED: {win_res.architecture.value.upper()} "
                    f"({_fmt_bytes(res.file_size_bytes)})"
                )
                self.val_status_lbl.setText(status_text)
                self.val_status_lbl.setStyleSheet(f"color: {COLOR_ACCENT}; font-weight: 600;")

                details = (
                    f"Bootloader: {win_res.primary_bootloader} | BCD Store: {win_res.bcd_path or 'Not found'}\n"
                    f"Payload: {win_res.payload_path} ({win_res.payload_type.value})\n"
                    f"Strategy: Staged Media on MVX_DATA with direct UEFI chainloading."
                )
                self.val_details_lbl.setText(details)

                # Automatically switch boot method to windows_extracted_media
                idx = self.method_combo.findText("windows_extracted_media")
                if idx >= 0:
                    self.method_combo.setCurrentIndex(idx)

                # Pre-fill display name if empty
                if not self.name_edit.text().strip():
                    self.name_edit.setText(f"Windows Setup ({win_res.architecture.value.upper()})")
            else:
                status_text = f"VALID: {res.filesystem_format} ({_fmt_bytes(res.file_size_bytes)})"
                if res.has_el_torito_boot:
                    status_text += " | El Torito Boot Record Detected"
                self.val_status_lbl.setText(status_text)
                self.val_status_lbl.setStyleSheet(f"color: {COLOR_SUCCESS}; font-weight: 600;")

                details = f"Volume ID: {res.volume_identifier or 'None'} | System ID: {res.system_identifier or 'None'}\n"
                details += f"Notice: {res.disclaimer}"
                self.val_details_lbl.setText(details)

                # Pre-fill display name if empty
                if not self.name_edit.text().strip():
                    clean_name = self._selected_iso_path.stem.replace("-", " ").replace("_", " ").title()
                    if res.volume_identifier:
                        clean_name = res.volume_identifier.replace("_", " ").title()
                    self.name_edit.setText(clean_name)

            self.btn_register.setEnabled(True)
        else:
            self.val_status_lbl.setText("INVALID ISO FILE")
            self.val_status_lbl.setStyleSheet(f"color: {COLOR_DANGER}; font-weight: 600;")
            self.val_details_lbl.setText("; ".join(res.errors))
            self.btn_register.setEnabled(False)
            self._windows_inspection = None

    def _on_register_clicked(self) -> None:
        if not self._selected_iso_path:
            return

        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Please provide a custom display name.")
            return

        boot_method = self.method_combo.currentText()
        if boot_method == "windows_extracted_media":
            bootloader = (
                self._windows_inspection.primary_bootloader
                if self._windows_inspection and self._windows_inspection.primary_bootloader
                else "/efi/boot/bootx64.efi"
            )
            rec = self.iso_service.register_windows_media(
                display_name=name,
                bootloader_path=bootloader,
                iso_file=self._selected_iso_path,
                description=self.desc_edit.text().strip() or None,
                enabled=self.enabled_cb.isChecked(),
            )
        else:
            rec = self.iso_service.register_iso(
                iso_file=self._selected_iso_path.name,
                display_name=name,
                boot_method=boot_method,
                description=self.desc_edit.text().strip() or None,
                custom_args=self.args_edit.text().strip() or None,
                enabled=self.enabled_cb.isChecked(),
                calculate_hash=self.hash_cb.isChecked(),
            )

        if rec:
            QMessageBox.information(
                self,
                "Registration Successful",
                f"Successfully registered '{rec.display_name}'.\n\n"
                f"Boot menu configuration has been regenerated and synchronized on MVX_BOOT.",
            )
            # Reset fields and navigate to library
            self.name_edit.clear()
            self.desc_edit.clear()
            self.args_edit.clear()
            self.navigate_requested.emit("library")

