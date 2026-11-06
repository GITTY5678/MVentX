"""Main Window for the MVentX PySide6 Management Interface.

Integrates dark graphite UI styling, clean sidebar navigation,
active device switching, and stacked application screens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from manager.services.device_service import ActiveTarget, DeviceService
from manager.services.iso_service import ISOService
from manager.ui.add_environment import AddEnvironmentScreen
from manager.ui.dashboard import DashboardScreen
from manager.ui.environment_library import EnvironmentLibraryScreen
from manager.ui.settings import SettingsScreen
from manager.ui.storage_manager import StorageManagerScreen
from manager.ui.usb_installer import USBInstallerScreen
from manager.ui.styles import (
    COLOR_ACCENT,
    COLOR_BORDER,
    COLOR_DANGER,
    COLOR_SUCCESS,
    DARK_GRAPHITE_THEME,
)


class MainWindow(QMainWindow):
    """Main window of the MVentX Manager application."""

    def __init__(
        self,
        device_service: DeviceService,
        iso_service: ISOService,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.device_service = device_service
        self.iso_service = iso_service

        self.setWindowTitle("MVentX Manager — Multi-Environment Boot Experience")
        self.resize(1080, 720)
        self.setMinimumSize(880, 580)
        self.setStyleSheet(DARK_GRAPHITE_THEME)

        self._setup_ui()
        self._wire_signals()

        # Initial device scan
        self.device_service.refresh_devices()

    def _setup_ui(self) -> None:
        root_widget = QWidget()
        self.setCentralWidget(root_widget)

        root_layout = QHBoxLayout(root_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ----------------------------------------------------------------------
        # 1. Left Sidebar
        # ----------------------------------------------------------------------
        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebar")
        sb_layout = QVBoxLayout(self.sidebar)
        sb_layout.setContentsMargins(0, 0, 0, 16)
        sb_layout.setSpacing(4)

        # Sidebar Header Branding
        sb_header = QWidget()
        sb_header.setObjectName("sidebarHeader")
        sbh_layout = QVBoxLayout(sb_header)
        sbh_layout.setContentsMargins(16, 18, 16, 18)
        sbh_layout.setSpacing(2)

        title_lbl = QLabel("MVentX")
        title_lbl.setObjectName("sidebarTitle")
        sub_lbl = QLabel("Boot Manager Suite")
        sub_lbl.setObjectName("sidebarSubtitle")
        sbh_layout.addWidget(title_lbl)
        sbh_layout.addWidget(sub_lbl)
        sb_layout.addWidget(sb_header)

        # Nav Buttons Group
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self.btn_nav_dashboard = self._create_nav_button("Dashboard", 0, checked=True)
        self.btn_nav_library = self._create_nav_button("Environment Library", 1)
        self.btn_nav_add = self._create_nav_button("Add Environment", 2)
        self.btn_nav_storage = self._create_nav_button("Storage Manager", 3)
        self.btn_nav_installer = self._create_nav_button("USB Installer", 4)
        self.btn_nav_settings = self._create_nav_button("Settings", 5)

        for btn in (
            self.btn_nav_dashboard,
            self.btn_nav_library,
            self.btn_nav_add,
            self.btn_nav_storage,
            self.btn_nav_installer,
            self.btn_nav_settings,
        ):
            sb_layout.addWidget(btn)

        sb_layout.addStretch()

        # Version tag in sidebar footer
        version_lbl = QLabel("v0.2.0 • Milestone 9")
        version_lbl.setStyleSheet("color: #4b5563; font-size: 11px; padding: 0 16px;")
        sb_layout.addWidget(version_lbl)

        root_layout.addWidget(self.sidebar)

        # ----------------------------------------------------------------------
        # 2. Right Main Container (Header + Content Area)
        # ----------------------------------------------------------------------
        main_container = QWidget()
        mc_layout = QVBoxLayout(main_container)
        mc_layout.setContentsMargins(0, 0, 0, 0)
        mc_layout.setSpacing(0)

        # Top Header Bar
        self.header_bar = QWidget()
        self.header_bar.setObjectName("headerBar")
        hb_layout = QHBoxLayout(self.header_bar)
        hb_layout.setContentsMargins(24, 12, 24, 12)
        hb_layout.setSpacing(14)

        target_lbl = QLabel("TARGET DRIVE:")
        target_lbl.setObjectName("deviceSelectorLabel")
        hb_layout.addWidget(target_lbl)

        self.target_combo = QComboBox()
        self.target_combo.setMinimumWidth(260)
        self.target_combo.currentIndexChanged.connect(self._on_target_combo_changed)
        hb_layout.addWidget(self.target_combo)

        self.btn_rescan = QPushButton("Scan USBs")
        self.btn_rescan.clicked.connect(self._on_rescan_clicked)
        hb_layout.addWidget(self.btn_rescan)

        self.btn_custom_target = QPushButton("Select Custom / Virtual Mount...")
        self.btn_custom_target.clicked.connect(self._on_select_custom_target)
        hb_layout.addWidget(self.btn_custom_target)

        hb_layout.addStretch()
        mc_layout.addWidget(self.header_bar)

        # Content Area Stacked Widget
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("contentArea")

        # Instantiate Screens
        self.dashboard_screen = DashboardScreen(self.iso_service)
        self.library_screen = EnvironmentLibraryScreen(self.iso_service)
        self.add_screen = AddEnvironmentScreen(self.iso_service)
        self.storage_screen = StorageManagerScreen(self.iso_service)
        self.installer_screen = USBInstallerScreen(self.device_service)
        self.settings_screen = SettingsScreen(self.iso_service)

        self.content_stack.addWidget(self.dashboard_screen)   # 0
        self.content_stack.addWidget(self.library_screen)     # 1
        self.content_stack.addWidget(self.add_screen)         # 2
        self.content_stack.addWidget(self.storage_screen)     # 3
        self.content_stack.addWidget(self.installer_screen)   # 4
        self.content_stack.addWidget(self.settings_screen)    # 5

        mc_layout.addWidget(self.content_stack, stretch=1)
        root_layout.addWidget(main_container, stretch=1)

        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _create_nav_button(self, text: str, index: int, checked: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("class", "navButton")
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.clicked.connect(lambda: self.switch_screen(index))
        self.nav_group.addButton(btn, index)
        return btn

    def _wire_signals(self) -> None:
        self.dashboard_screen.navigate_requested.connect(self.navigate_to_screen_name)
        self.library_screen.navigate_requested.connect(self.navigate_to_screen_name)
        self.add_screen.navigate_requested.connect(self.navigate_to_screen_name)

        self.device_service.devices_refreshed.connect(self._on_devices_refreshed)
        self.device_service.target_changed.connect(self._on_target_changed)

        self.iso_service.operation_succeeded.connect(self._on_operation_succeeded)
        self.iso_service.operation_failed.connect(self._on_operation_failed)

    def switch_screen(self, index: int) -> None:
        self.content_stack.setCurrentIndex(index)
        btn = self.nav_group.button(index)
        if btn and not btn.isChecked():
            btn.setChecked(True)

        # Screen-specific refresh triggers
        if index == 1:
            self.library_screen.load_data()
        elif index == 2:
            self.add_screen.refresh_unregistered_list()
        elif index == 3:
            self.storage_screen.refresh_storage_info()
        elif index == 5:
            self.settings_screen.load_settings()

    def navigate_to_screen_name(self, name: str) -> None:
        mapping = {
            "dashboard": 0,
            "library": 1,
            "add": 2,
            "storage": 3,
            "installer": 4,
            "settings": 5,
        }
        idx = mapping.get(name.lower(), 0)
        self.switch_screen(idx)

    # --------------------------------------------------------------------------
    # Device & Target Switching
    # --------------------------------------------------------------------------

    def _on_devices_refreshed(self, pairs: list) -> None:
        self.target_combo.blockSignals(True)
        self.target_combo.clear()

        # Virtual option (if active or if staged build/data exists)
        staged_data = Path(__file__).resolve().parent.parent.parent / "build" / "data"
        staged_esp = Path(__file__).resolve().parent.parent.parent / "build" / "esp"

        if staged_data.is_dir():
            self.target_combo.addItem("Virtual Disk (build/data)", ("virtual", str(staged_data), str(staged_esp)))

        for p in pairs:
            label = f"USB: {p.data_partition.label} ({p.data_partition.mount_point})"
            self.target_combo.addItem(label, ("physical", p))

        if self.target_combo.count() == 0:
            self.target_combo.addItem("No MVentX Devices Detected", None)

        self.target_combo.blockSignals(False)

        # If no target active, select first item
        if not self.device_service.has_active_target() and self.target_combo.count() > 0:
            self._on_target_combo_changed(0)

    def _on_target_combo_changed(self, index: int) -> None:
        data = self.target_combo.itemData(index)
        if not data:
            self.device_service.set_active_target(None)
            return

        target_type = data[0]
        if target_type == "virtual":
            _, data_mount, boot_mount = data
            self.device_service.set_custom_target(
                data_mount=data_mount,
                boot_mount=boot_mount,
                display_name="Virtual Disk Staging",
            )
        elif target_type == "physical":
            _, pair = data
            self.device_service.set_active_device(pair)

    def _on_rescan_clicked(self) -> None:
        found = self.device_service.refresh_devices()
        count = len(found)
        self.status_bar.showMessage(f"Device scan complete. Found {count} physical MVentX drive(s).", 4000)

    def _on_select_custom_target(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select MVX_DATA Directory",
            str(Path.cwd()),
        )
        if folder:
            data_p = Path(folder).resolve()
            # Check if boot mount exists nearby
            boot_candidate = data_p.parent / "esp"
            boot_p = boot_candidate if boot_candidate.is_dir() else None

            self.device_service.set_custom_target(
                data_mount=data_p,
                boot_mount=boot_p,
                display_name=f"Custom: {data_p.name}",
            )
            # Add to combo box
            self.target_combo.blockSignals(True)
            label = f"Custom Mount ({data_p.name})"
            self.target_combo.addItem(label, ("virtual", str(data_p), str(boot_p) if boot_p else None))
            self.target_combo.setCurrentIndex(self.target_combo.count() - 1)
            self.target_combo.blockSignals(False)

    def _on_target_changed(self, target: Optional[ActiveTarget]) -> None:
        if target:
            msg = f"Target active: {target.display_name} ({target.data_mount})"
            self.status_bar.showMessage(msg, 5000)
        else:
            self.status_bar.showMessage("No active MVentX device selected.", 5000)

    # --------------------------------------------------------------------------
    # Notification Handlers
    # --------------------------------------------------------------------------

    def _on_operation_succeeded(self, message: str) -> None:
        self.status_bar.showMessage(f"SUCCESS: {message}", 6000)

    def _on_operation_failed(self, title: str, detail: str) -> None:
        self.status_bar.showMessage(f"ERROR: {detail}", 6000)
        QMessageBox.critical(self, title, detail)

