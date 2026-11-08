"""Environment Library Screen for MVentX Manager.

Provides comprehensive table view of registered boot environments with:
- Sorting by Name, Size, Order, Boot Method, Status
- Search and text filtering
- Batch operations (Enable Selected, Disable Selected, Remove Selected)
- Detailed Environment Inspector dialog with on-demand SHA-256 verification
- Drag-and-Drop ISO importing
- Granular per-item actions (Reorder, Rename, Toggle, Remove)
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.iso_manager import EnvironmentRecord, OverlayStatus
from manager.services.iso_service import ISOService
from manager.services.workers import ChecksumWorker, ISOCopyWorker
from manager.ui.progress_dialog import OperationProgressDialog
from manager.ui.styles import (
    COLOR_ACCENT,
    COLOR_DANGER,
    COLOR_SUCCESS,
    COLOR_TEXT_MUTED,
    COLOR_WARNING,
)


def _fmt_bytes(num_bytes: Optional[int]) -> str:
    if num_bytes is None or num_bytes == 0:
        return "N/A"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PiB"


class EnvironmentDetailDialog(QDialog):
    """Detailed environment inspector dialog showing boot recipe details, kernel parameters, and checksum."""

    def __init__(self, env: EnvironmentRecord, iso_service: ISOService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.env = env
        self.iso_service = iso_service
        self.setWindowTitle(f"Environment Inspector — {env.display_name}")
        self.setFixedSize(650, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        header = QLabel(f"<b>{env.display_name}</b>")
        header.setStyleSheet("font-size: 16px; color: #f3f4f6;")
        layout.addWidget(header)

        # Form fields
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignRight)

        self._add_row(form, "Environment ID:", env.id)
        self._add_row(form, "Boot Method:", env.boot_method)
        self._add_row(form, "Target / ISO Path:", env.iso_path or env.target_path or "N/A")
        self._add_row(form, "File Size:", _fmt_bytes(env.file_size_bytes))
        self._add_row(form, "Boot Order Position:", f"#{env.order}")
        self._add_row(form, "Status:", "ENABLED (Active in Boot Menu)" if env.enabled else "DISABLED")
        self._add_row(form, "Kernel Path:", getattr(env, "kernel_path", "") or "Auto-detected by recipe")
        self._add_row(form, "Initramfs Path:", getattr(env, "initrd_path", "") or "Auto-detected by recipe")
        self._add_row(form, "Custom Kernel Args:", env.custom_args or "Default distribution parameters")
        self._add_row(form, "Description:", env.description or "None")

        # Initramfs Overlay Status & Boot Support Repair
        ov_status = self.iso_service.get_overlay_status(env)
        self.ov_req_lbl = QLabel("Yes (Casper exFAT Boot Support)" if ov_status.requires_overlay else "No")
        self.ov_req_lbl.setStyleSheet("color: #e5e7eb; font-weight: 500;")
        form.addRow("Requires Initramfs Overlay:", self.ov_req_lbl)

        self.ov_exists_lbl = QLabel("Yes" if ov_status.overlay_exists else "No")
        self.ov_exists_lbl.setStyleSheet("color: #e5e7eb; font-weight: 500;")
        form.addRow("Overlay Exists on MVX_BOOT:", self.ov_exists_lbl)

        self.ov_regen_lbl = QLabel("Yes" if ov_status.needs_regeneration else "No")
        self.ov_regen_lbl.setStyleSheet(
            f"color: {COLOR_WARNING if ov_status.needs_regeneration else COLOR_SUCCESS}; font-weight: 600;"
        )
        form.addRow("Needs Regeneration:", self.ov_regen_lbl)

        self.ov_status_lbl = QLabel(f"{ov_status.status_label} — {ov_status.detail_message}")
        self.ov_status_lbl.setWordWrap(True)
        self.ov_status_lbl.setStyleSheet("color: #e5e7eb; font-weight: 500;")
        form.addRow("Overlay Status:", self.ov_status_lbl)

        self.ov_path_lbl = QLabel(env.overlay_path or "None")
        self.ov_path_lbl.setStyleSheet("font-family: monospace; font-size: 11px; color: #9ca3af;")
        form.addRow("Overlay Path:", self.ov_path_lbl)

        self.btn_repair_boot = QPushButton("Regenerate / Repair Boot Support")
        self.btn_repair_boot.setFixedWidth(250)
        self.btn_repair_boot.setToolTip(
            "Rebuild the initramfs overlay (if required) and synchronize the MVX_BOOT GRUB menu atomically."
        )
        self.btn_repair_boot.clicked.connect(self._on_repair_boot_support)
        form.addRow("Boot Support Action:", self.btn_repair_boot)

        # SHA-256 row with verification button
        hash_val = env.sha256 or "Not calculated"
        self.hash_lbl = QLabel(hash_val)
        self.hash_lbl.setWordWrap(True)
        self.hash_lbl.setStyleSheet("font-family: monospace; font-size: 11px;")

        hash_box = QVBoxLayout()
        hash_box.addWidget(self.hash_lbl)

        self.btn_compute_hash = QPushButton("Compute SHA-256 Checksum")
        self.btn_compute_hash.setFixedWidth(200)
        self.btn_compute_hash.clicked.connect(self._on_compute_hash)
        hash_box.addWidget(self.btn_compute_hash)

        form.addRow("SHA-256 Checksum:", hash_box)
        layout.addLayout(form)

        layout.addStretch()

        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _add_row(self, form: QFormLayout, label: str, value: str) -> None:
        lbl = QLabel(value)
        lbl.setStyleSheet("color: #e5e7eb; font-weight: 500;")
        lbl.setWordWrap(True)
        form.addRow(label, lbl)

    def _refresh_overlay_display(self) -> None:
        ov_status = self.iso_service.get_overlay_status(self.env)
        self.ov_req_lbl.setText("Yes (Casper exFAT Boot Support)" if ov_status.requires_overlay else "No")
        self.ov_exists_lbl.setText("Yes" if ov_status.overlay_exists else "No")
        self.ov_regen_lbl.setText("Yes" if ov_status.needs_regeneration else "No")
        self.ov_regen_lbl.setStyleSheet(
            f"color: {COLOR_WARNING if ov_status.needs_regeneration else COLOR_SUCCESS}; font-weight: 600;"
        )
        self.ov_status_lbl.setText(f"{ov_status.status_label} — {ov_status.detail_message}")
        self.ov_path_lbl.setText(self.env.overlay_path or "None")

    def _on_repair_boot_support(self) -> None:
        progress = QProgressDialog(
            f"Regenerating boot support for '{self.env.display_name}'...",
            None,
            0,
            100,
            self,
        )
        progress.setWindowTitle("Repair / Regenerate Boot Support")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(5)
        QApplication.processEvents()

        def _cb(pct: int, msg: str) -> None:
            progress.setValue(pct)
            progress.setLabelText(msg)
            QApplication.processEvents()

        updated = self.iso_service.regenerate_boot_support(self.env.id, progress_callback=_cb)
        progress.close()
        if updated:
            self.env = updated
            self._refresh_overlay_display()
            QMessageBox.information(
                self,
                "Boot Support Updated",
                f"Boot support and MVX_BOOT menu configuration for '{updated.display_name}' "
                "have been regenerated and synchronized.",
            )

    def _on_compute_hash(self) -> None:
        target = self.iso_service.device_service.active_target
        if not target or not self.env.iso_path:
            QMessageBox.warning(self, "Checksum Error", "Cannot locate ISO file for this environment.")
            return

        iso_file = target.data_mount / self.env.iso_path
        if not iso_file.is_file():
            QMessageBox.warning(self, "Checksum Error", f"ISO file not found at {iso_file}")
            return

        worker = ChecksumWorker(iso_file, parent=self)
        dlg = OperationProgressDialog(
            title="Calculating Checksum",
            operation_description=f"Calculating SHA-256 checksum for {iso_file.name}...",
            worker=worker,
            parent=self,
        )

        def _on_success(hex_digest: str) -> None:
            self.hash_lbl.setText(hex_digest)
            self.btn_compute_hash.setEnabled(False)

        worker.finished_success.connect(_on_success)
        worker.start()
        dlg.exec()


class RenameDialog(QDialog):
    """Dialog to safely update an environment's custom display name."""

    def __init__(self, current_name: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rename Environment")
        self.setFixedSize(420, 160)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        lbl = QLabel("Enter new custom display name for this boot environment:")
        lbl.setStyleSheet("font-weight: 500;")
        layout.addWidget(lbl)

        self.name_edit = QLineEdit(current_name)
        self.name_edit.selectAll()
        layout.addWidget(self.name_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_name(self) -> str:
        return self.name_edit.text().strip()


class RemoveDialog(QDialog):
    """Confirmation dialog for removing an environment registration."""

    def __init__(self, env: EnvironmentRecord, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Remove Boot Environment")
        self.setFixedSize(460, 200)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        title = QLabel(f"Remove registration for <b>{env.display_name}</b>?")
        title.setStyleSheet("font-size: 14px; color: #f3f4f6;")
        layout.addWidget(title)

        desc = QLabel(
            "Removing this registration will exclude it from the GRUB boot menu. "
            "By default, the underlying ISO file will remain untouched on the disk."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #9ca3af; font-size: 12px;")
        layout.addWidget(desc)

        self.delete_file_cb = QCheckBox("Also permanently delete the underlying ISO file from disk")
        self.delete_file_cb.setStyleSheet(f"color: {COLOR_DANGER}; font-weight: 600;")
        layout.addWidget(self.delete_file_cb)

        buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Yes).setText("Remove Registration")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def should_delete_file(self) -> bool:
        return self.delete_file_cb.isChecked()


class EnvironmentLibraryScreen(QWidget):
    """Library view displaying registered boot environments in table format with batch and sorting features."""

    navigate_requested = Signal(str)

    def __init__(self, iso_service: ISOService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.iso_service = iso_service
        self._cached_environments: List[EnvironmentRecord] = []

        self.setAcceptDrops(True)
        self._setup_ui()
        self.iso_service.environments_changed.connect(self.load_data)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # ----------------------------------------------------------------------
        # 1. Search & Filter Bar + Sorting Selector
        # ----------------------------------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter environments by name, target path, or method...")
        self.search_input.textChanged.connect(self._apply_filter_and_sort)
        toolbar.addWidget(self.search_input, stretch=1)

        sort_lbl = QLabel("Sort by:")
        sort_lbl.setStyleSheet("color: #9ca3af; font-size: 12px;")
        toolbar.addWidget(sort_lbl)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems([
            "Default (Boot Order)",
            "Name (A → Z)",
            "Name (Z → A)",
            "Size (Largest First)",
            "Size (Smallest First)",
            "Boot Method",
            "Enabled First",
        ])
        self.sort_combo.currentIndexChanged.connect(self._apply_filter_and_sort)
        toolbar.addWidget(self.sort_combo)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.load_data)
        toolbar.addWidget(self.btn_refresh)

        self.btn_add = QPushButton("+ Register New ISO")
        self.btn_add.setProperty("class", "primaryButton")
        self.btn_add.clicked.connect(lambda: self.navigate_requested.emit("add"))
        toolbar.addWidget(self.btn_add)

        layout.addLayout(toolbar)

        # ----------------------------------------------------------------------
        # 2. Batch Operations Toolbar
        # ----------------------------------------------------------------------
        batch_bar = QHBoxLayout()
        batch_bar.setSpacing(10)

        batch_lbl = QLabel("BATCH ACTIONS:")
        batch_lbl.setProperty("class", "metricLabel")
        batch_bar.addWidget(batch_lbl)

        self.btn_batch_enable = QPushButton("✓ Enable Selected")
        self.btn_batch_enable.clicked.connect(lambda: self._execute_batch_toggle(True))
        batch_bar.addWidget(self.btn_batch_enable)

        self.btn_batch_disable = QPushButton("✕ Disable Selected")
        self.btn_batch_disable.clicked.connect(lambda: self._execute_batch_toggle(False))
        batch_bar.addWidget(self.btn_batch_disable)

        self.btn_batch_repair = QPushButton("⟳ Repair / Regenerate Selected")
        self.btn_batch_repair.setToolTip(
            "Regenerate initramfs overlays (where required) and synchronize MVX_BOOT GRUB configuration for selected environments."
        )
        self.btn_batch_repair.clicked.connect(self._execute_batch_repair)
        batch_bar.addWidget(self.btn_batch_repair)

        self.btn_batch_remove = QPushButton("🗑 Remove Selected")
        self.btn_batch_remove.setProperty("class", "dangerButton")
        self.btn_batch_remove.clicked.connect(self._execute_batch_remove)
        batch_bar.addWidget(self.btn_batch_remove)

        batch_bar.addStretch()

        self.drop_hint_lbl = QLabel("Tip: You can drag & drop .iso files onto this window to import them")
        self.drop_hint_lbl.setStyleSheet("color: #6b7280; font-size: 11px; font-style: italic;")
        batch_bar.addWidget(self.drop_hint_lbl)

        layout.addLayout(batch_bar)

        # ----------------------------------------------------------------------
        # 3. Main Environments Table
        # ----------------------------------------------------------------------
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Order",
            "Environment Name",
            "ISO File / Target",
            "File Size",
            "Boot Method",
            "Status",
            "Overlay Status",
            "Actions",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)

        layout.addWidget(self.table)

    # --------------------------------------------------------------------------
    # Drag and Drop Support
    # --------------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(u.toLocalFile().lower().endswith((".iso", ".img")) for u in urls):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        target = self.iso_service.device_service.active_target
        if not target or not target.data_mount.is_dir():
            QMessageBox.warning(self, "No Device", "Please select an active MVentX storage target first.")
            return

        urls = event.mimeData().urls()
        iso_urls = [u for u in urls if u.toLocalFile().lower().endswith((".iso", ".img"))]

        if not iso_urls:
            return

        first_iso = Path(iso_urls[0].toLocalFile())
        dest_dir = target.data_mount / "ISO"

        reply = QMessageBox.question(
            self,
            "Import Dropped ISO",
            f"Would you like to copy '{first_iso.name}' into the MVentX ISO directory ({dest_dir})?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            worker = ISOCopyWorker(
                source_path=first_iso,
                destination_dir=dest_dir,
                dest_filename=first_iso.name,
                parent=self,
            )
            dlg = OperationProgressDialog(
                title="Importing Dropped ISO",
                operation_description=f"Copying {first_iso.name} to USB drive...",
                worker=worker,
                parent=self,
            )
            worker.start()
            if dlg.exec() == OperationProgressDialog.Accepted:
                QMessageBox.information(
                    self,
                    "Import Complete",
                    f"'{first_iso.name}' was imported successfully. Opening registration screen...",
                )
                self.navigate_requested.emit("add")

    # --------------------------------------------------------------------------
    # Data Loading, Filtering, and Sorting
    # --------------------------------------------------------------------------

    def load_data(self) -> None:
        """Fetch environment records from service and populate table."""
        self._cached_environments = self.iso_service.list_environments()
        self._apply_filter_and_sort()

    def _apply_filter_and_sort(self) -> None:
        """Filter and sort cached environments based on search query and sort dropdown."""
        query = self.search_input.text().strip().lower()
        items = list(self._cached_environments)

        if query:
            items = [
                e for e in items
                if query in e.display_name.lower()
                or (e.iso_path and query in e.iso_path.lower())
                or (e.boot_method and query in e.boot_method.lower())
            ]

        # Sorting logic
        sort_mode = self.sort_combo.currentIndex()
        if sort_mode == 1:    # Name A-Z
            items.sort(key=lambda e: e.display_name.lower())
        elif sort_mode == 2:  # Name Z-A
            items.sort(key=lambda e: e.display_name.lower(), reverse=True)
        elif sort_mode == 3:  # Size Largest First
            items.sort(key=lambda e: e.file_size_bytes or 0, reverse=True)
        elif sort_mode == 4:  # Size Smallest First
            items.sort(key=lambda e: e.file_size_bytes or 0)
        elif sort_mode == 5:  # Boot Method
            items.sort(key=lambda e: e.boot_method.lower())
        elif sort_mode == 6:  # Enabled First
            items.sort(key=lambda e: (not e.enabled, e.order))
        else:                 # Default (Order)
            items.sort(key=lambda e: e.order)

        self._render_table(items)

    def _render_table(self, envs: List[EnvironmentRecord]) -> None:
        self.table.setRowCount(len(envs))

        for row, env in enumerate(envs):
            # 1. Order
            order_item = QTableWidgetItem(f"#{env.order}")
            order_item.setTextAlignment(Qt.AlignCenter)
            order_item.setData(Qt.UserRole, env.id)  # Store env ID for selection
            self.table.setItem(row, 0, order_item)

            # 2. Display Name
            name_item = QTableWidgetItem(env.display_name)
            name_item.setToolTip(f"ID: {env.id}\n{env.description or 'No description'}\nDouble-click to inspect.")
            self.table.setItem(row, 1, name_item)

            # 3. Path / Target
            path_str = env.iso_path or env.target_path or "N/A"
            path_item = QTableWidgetItem(path_str)
            path_item.setForeground(Qt.gray)
            self.table.setItem(row, 2, path_item)

            # 4. File Size
            size_item = QTableWidgetItem(_fmt_bytes(env.file_size_bytes))
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 3, size_item)

            # 5. Boot Method
            method_item = QTableWidgetItem(env.boot_method)
            self.table.setItem(row, 4, method_item)

            # 6. Status Badge Pill
            status_widget = QWidget()
            s_layout = QHBoxLayout(status_widget)
            s_layout.setContentsMargins(4, 2, 4, 2)
            s_layout.setAlignment(Qt.AlignCenter)

            status_lbl = QLabel("ENABLED" if env.enabled else "DISABLED")
            if env.enabled:
                status_lbl.setProperty("class", "statusBadgeEnabled")
            else:
                status_lbl.setProperty("class", "statusBadgeDisabled")
            s_layout.addWidget(status_lbl)
            self.table.setCellWidget(row, 5, status_widget)

            # 7. Initramfs Overlay Status Badge Pill
            ov_status: OverlayStatus = self.iso_service.get_overlay_status(env)
            ov_tooltip = (
                f"Requires Initramfs Overlay: {'Yes' if ov_status.requires_overlay else 'No'}\n"
                f"Overlay Exists on MVX_BOOT: {'Yes' if ov_status.overlay_exists else 'No'}\n"
                f"Needs Regeneration: {'Yes' if ov_status.needs_regeneration else 'No'}\n"
                f"Details: {ov_status.detail_message}"
            )
            ov_item = QTableWidgetItem(ov_status.status_label)
            ov_item.setToolTip(ov_tooltip)
            self.table.setItem(row, 6, ov_item)

            ov_widget = QWidget()
            ov_layout = QHBoxLayout(ov_widget)
            ov_layout.setContentsMargins(4, 2, 4, 2)
            ov_layout.setAlignment(Qt.AlignCenter)

            ov_lbl = QLabel(ov_status.status_label)
            ov_lbl.setToolTip(ov_tooltip)
            if ov_status.state == "ready":
                ov_lbl.setProperty("class", "statusBadgeEnabled")
            elif ov_status.state == "missing":
                ov_lbl.setProperty("class", "statusBadgeCorrupted")
            elif ov_status.state == "needs_regeneration":
                ov_lbl.setProperty("class", "statusBadgeWarning")
            else:
                ov_lbl.setProperty("class", "statusBadgeDisabled")
            ov_layout.addWidget(ov_lbl)
            self.table.setCellWidget(row, 6, ov_widget)

            # 8. Action Buttons
            action_widget = QWidget()
            a_layout = QHBoxLayout(action_widget)
            a_layout.setContentsMargins(4, 2, 4, 2)
            a_layout.setSpacing(5)

            # Up / Down
            btn_up = QPushButton("▲")
            btn_up.setToolTip("Move entry up in boot order")
            btn_up.setFixedSize(26, 26)
            btn_up.setEnabled(row > 0 and self.sort_combo.currentIndex() == 0)
            btn_up.clicked.connect(lambda _, eid=env.id: self.iso_service.move_environment(eid, "up"))
            a_layout.addWidget(btn_up)

            btn_down = QPushButton("▼")
            btn_down.setToolTip("Move entry down in boot order")
            btn_down.setFixedSize(26, 26)
            btn_down.setEnabled(row < len(envs) - 1 and self.sort_combo.currentIndex() == 0)
            btn_down.clicked.connect(lambda _, eid=env.id: self.iso_service.move_environment(eid, "down"))
            a_layout.addWidget(btn_down)

            # Toggle Enable/Disable
            btn_toggle = QPushButton("Disable" if env.enabled else "Enable")
            btn_toggle.setFixedHeight(26)
            btn_toggle.clicked.connect(lambda _, eid=env.id, en=env.enabled: self.iso_service.toggle_environment_enabled(eid, not en))
            a_layout.addWidget(btn_toggle)

            # Repair / Regenerate Boot Support
            btn_repair = QPushButton("Repair Boot" if ov_status.needs_regeneration else "Regenerate")
            btn_repair.setFixedHeight(26)
            btn_repair.setToolTip(
                "Regenerate initramfs overlay (if required) and synchronize MVX_BOOT GRUB configuration atomically."
            )
            if ov_status.needs_regeneration:
                btn_repair.setProperty("class", "primaryButton")
            btn_repair.clicked.connect(lambda _, e=env: self._on_repair_clicked(e))
            a_layout.addWidget(btn_repair)

            # Rename
            btn_rename = QPushButton("Rename")
            btn_rename.setFixedHeight(26)
            btn_rename.clicked.connect(lambda _, e=env: self._on_rename_clicked(e))
            a_layout.addWidget(btn_rename)

            # Inspect
            btn_inspect = QPushButton("Inspect")
            btn_inspect.setFixedHeight(26)
            btn_inspect.clicked.connect(lambda _, e=env: self._on_inspect_clicked(e))
            a_layout.addWidget(btn_inspect)

            # Remove
            btn_remove = QPushButton("Remove")
            btn_remove.setProperty("class", "dangerButton")
            btn_remove.setFixedHeight(26)
            btn_remove.clicked.connect(lambda _, e=env: self._on_remove_clicked(e))
            a_layout.addWidget(btn_remove)

            self.table.setCellWidget(row, 7, action_widget)

        self.table.resizeRowsToContents()

    # --------------------------------------------------------------------------
    # Batch Operations
    # --------------------------------------------------------------------------

    def _get_selected_environment_ids(self) -> List[str]:
        selected_rows = set()
        for item in self.table.selectedItems():
            selected_rows.add(item.row())

        env_ids = []
        for r in sorted(selected_rows):
            order_item = self.table.item(r, 0)
            if order_item:
                eid = order_item.data(Qt.UserRole)
                if eid:
                    env_ids.append(eid)
        return env_ids

    def _execute_batch_toggle(self, enable: bool) -> None:
        ids = self._get_selected_environment_ids()
        if not ids:
            QMessageBox.information(self, "No Selection", "Please select one or more environments from the table first.")
            return

        for eid in ids:
            self.iso_service.toggle_environment_enabled(eid, enable)

    def _execute_batch_repair(self) -> None:
        ids = self._get_selected_environment_ids()
        if not ids:
            QMessageBox.information(
                self,
                "No Selection",
                "Please select one or more environments to repair or regenerate.",
            )
            return

        progress = QProgressDialog(
            "Regenerating boot support for selected environments...",
            None,
            0,
            100,
            self,
        )
        progress.setWindowTitle("Repair / Regenerate Boot Support")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        QApplication.processEvents()

        repaired_count = 0
        total = len(ids)
        for idx, eid in enumerate(ids):
            base_pct = int((idx / total) * 100)

            def _cb(pct: int, msg: str, b: int = base_pct, n: int = total) -> None:
                scaled = min(99, b + int(pct / n))
                progress.setValue(scaled)
                progress.setLabelText(msg)
                QApplication.processEvents()

            res = self.iso_service.regenerate_boot_support(eid, progress_callback=_cb)
            if res is not None:
                repaired_count += 1

        progress.setValue(100)
        progress.close()
        if repaired_count > 0:
            QMessageBox.information(
                self,
                "Boot Support Updated",
                f"Successfully repaired/regenerated boot support and synchronized MVX_BOOT for "
                f"{repaired_count} of {total} environment(s).",
            )

    def _execute_batch_remove(self) -> None:
        ids = self._get_selected_environment_ids()
        if not ids:
            QMessageBox.information(self, "No Selection", "Please select one or more environments to remove.")
            return

        reply = QMessageBox.question(
            self,
            "Confirm Batch Removal",
            f"Are you sure you want to remove {len(ids)} selected environment registrations from the boot menu?\n\n"
            "The underlying ISO files will remain preserved on the disk.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            for eid in ids:
                self.iso_service.remove_environment(eid, delete_file=False, confirm_deletion=False)

    # --------------------------------------------------------------------------
    # Individual Dialog Handlers
    # --------------------------------------------------------------------------

    def _on_inspect_clicked(self, env: EnvironmentRecord) -> None:
        dlg = EnvironmentDetailDialog(env, self.iso_service, self)
        dlg.exec()

    def _on_repair_clicked(self, env: EnvironmentRecord) -> None:
        progress = QProgressDialog(
            f"Regenerating boot support for '{env.display_name}'...",
            None,
            0,
            100,
            self,
        )
        progress.setWindowTitle("Repair / Regenerate Boot Support")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(5)
        QApplication.processEvents()

        def _cb(pct: int, msg: str) -> None:
            progress.setValue(pct)
            progress.setLabelText(msg)
            QApplication.processEvents()

        updated = self.iso_service.regenerate_boot_support(env.id, progress_callback=_cb)
        progress.close()
        if updated:
            ov_status = self.iso_service.get_overlay_status(updated)
            QMessageBox.information(
                self,
                "Boot Support Updated",
                f"Boot support for '{updated.display_name}' has been regenerated and synchronized on MVX_BOOT.\n\n"
                f"Overlay Status: {ov_status.status_label}\n"
                f"{ov_status.detail_message}",
            )

    def _on_rename_clicked(self, env: EnvironmentRecord) -> None:
        dlg = RenameDialog(env.display_name, self)
        if dlg.exec() == QDialog.Accepted:
            new_name = dlg.get_name()
            if new_name and new_name != env.display_name:
                self.iso_service.rename_environment(env.id, new_name)

    def _on_remove_clicked(self, env: EnvironmentRecord) -> None:
        dlg = RemoveDialog(env, self)
        if dlg.exec() == QDialog.Accepted:
            delete_file = dlg.should_delete_file()
            self.iso_service.remove_environment(
                env_id=env.id,
                delete_file=delete_file,
                confirm_deletion=delete_file,
            )
