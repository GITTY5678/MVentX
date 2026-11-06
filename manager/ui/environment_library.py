"""Environment Library Screen for MVentX Manager.

Provides table view of registered boot environments with actions to rename,
enable/disable, reorder, and safely remove registrations.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.iso_manager import EnvironmentRecord
from manager.services.iso_service import ISOService
from manager.ui.styles import (
    COLOR_DANGER,
    COLOR_SUCCESS,
    COLOR_TEXT_MUTED,
)


def _fmt_bytes(num_bytes: Optional[int]) -> str:
    if num_bytes is None or num_bytes == 0:
        return "N/A"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PiB"


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
    """Library view displaying registered boot environments in table format."""

    navigate_requested = Signal(str)

    def __init__(self, iso_service: ISOService, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.iso_service = iso_service
        self._cached_environments: List[EnvironmentRecord] = []

        self._setup_ui()
        self.iso_service.environments_changed.connect(self.load_data)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        # Toolbar row
        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter environments by name or ISO path...")
        self.search_input.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.search_input, stretch=1)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.load_data)
        toolbar.addWidget(self.btn_refresh)

        self.btn_add = QPushButton("+ Register New ISO")
        self.btn_add.setProperty("class", "primaryButton")
        self.btn_add.clicked.connect(lambda: self.navigate_requested.emit("add"))
        toolbar.addWidget(self.btn_add)

        layout.addLayout(toolbar)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Order",
            "Environment Name",
            "ISO File / Target",
            "File Size",
            "Boot Method",
            "Status",
            "Actions",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)

        layout.addWidget(self.table)

    def load_data(self) -> None:
        """Fetch environment records from service and populate table."""
        self._cached_environments = self.iso_service.list_environments()
        self._render_table(self._cached_environments)

    def _apply_filter(self, text: str) -> None:
        query = text.strip().lower()
        if not query:
            self._render_table(self._cached_environments)
            return

        filtered = [
            e for e in self._cached_environments
            if query in e.display_name.lower() or (e.iso_path and query in e.iso_path.lower())
        ]
        self._render_table(filtered)

    def _render_table(self, envs: List[EnvironmentRecord]) -> None:
        self.table.setRowCount(len(envs))

        for row, env in enumerate(envs):
            # 1. Order
            order_item = QTableWidgetItem(f"#{env.order}")
            order_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, order_item)

            # 2. Display Name
            name_item = QTableWidgetItem(env.display_name)
            name_item.setToolTip(f"ID: {env.id}\n{env.description or 'No description'}")
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

            # 7. Action Buttons
            action_widget = QWidget()
            a_layout = QHBoxLayout(action_widget)
            a_layout.setContentsMargins(4, 2, 4, 2)
            a_layout.setSpacing(6)

            # Up / Down
            btn_up = QPushButton("▲")
            btn_up.setToolTip("Move entry up in boot order")
            btn_up.setFixedSize(26, 26)
            btn_up.setEnabled(row > 0)
            btn_up.clicked.connect(lambda _, eid=env.id: self.iso_service.move_environment(eid, "up"))
            a_layout.addWidget(btn_up)

            btn_down = QPushButton("▼")
            btn_down.setToolTip("Move entry down in boot order")
            btn_down.setFixedSize(26, 26)
            btn_down.setEnabled(row < len(envs) - 1)
            btn_down.clicked.connect(lambda _, eid=env.id: self.iso_service.move_environment(eid, "down"))
            a_layout.addWidget(btn_down)

            # Toggle Enable/Disable
            btn_toggle = QPushButton("Disable" if env.enabled else "Enable")
            btn_toggle.setFixedHeight(26)
            btn_toggle.clicked.connect(lambda _, eid=env.id, en=env.enabled: self.iso_service.toggle_environment_enabled(eid, not en))
            a_layout.addWidget(btn_toggle)

            # Rename
            btn_rename = QPushButton("Rename")
            btn_rename.setFixedHeight(26)
            btn_rename.clicked.connect(lambda _, e=env: self._on_rename_clicked(e))
            a_layout.addWidget(btn_rename)

            # Remove
            btn_remove = QPushButton("Remove")
            btn_remove.setProperty("class", "dangerButton")
            btn_remove.setFixedHeight(26)
            btn_remove.clicked.connect(lambda _, e=env: self._on_remove_clicked(e))
            a_layout.addWidget(btn_remove)

            self.table.setCellWidget(row, 6, action_widget)

        self.table.resizeRowsToContents()

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

