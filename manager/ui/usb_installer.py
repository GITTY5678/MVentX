"""USB Installation and Virtual Disk Preparation Screen for MVentX Manager.

Provides a dedicated interface to:
1. Configure target storage (Virtual Disk Image or Physical USB Storage).
2. Preview GPT partition layout (MVX_BOOT FAT32 + MVX_DATA exFAT).
3. Enforce strict safety confirmation before destructive operations.
4. Execute installation asynchronously with real-time stage progress reporting.
5. Display structured verification results upon completion.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
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
    QVBoxLayout,
    QWidget,
)

from core.host.host_platform import HostPlatform
from core.storage.device_info import DeviceInfo, DeviceType
from core.storage.device_manager import DeviceManager, DeviceSafetyError
from installer.installation_recovery import (
    InstallationRecoveryService,
    USBInstallationState,
)
from installer.installation_service import InstallationResult, InstallationService
from installer.installation_verifier import InstallationVerificationReport
from installer.partition_planner import (
    DEFAULT_ESP_SIZE_MB,
    MIN_DISK_CAPACITY_MB,
    MIN_ESP_SIZE_MB,
    PartitionPlan,
    PartitionPlanner,
)
from installer.physical_device_workflow import (
    DestructiveOperationPlan,
    PhysicalDeviceWorkflow,
    PhysicalSafetyViolationError,
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

logger = logging.getLogger(__name__)


class PhysicalConfirmationDialog(QDialog):
    """Modal confirmation dialog enforcing two-phase explicit user confirmation."""

    def __init__(self, plan: DestructiveOperationPlan, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle("CRITICAL WARNING: Confirm Destructive USB Initialization")
        self.setModal(True)
        self.setMinimumWidth(560)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # Danger banner
        banner = QFrame()
        banner.setStyleSheet(
            "background-color: rgba(239, 68, 68, 0.15); "
            "border: 1px solid rgba(239, 68, 68, 0.6); "
            "border-radius: 6px; padding: 14px;"
        )
        b_layout = QVBoxLayout(banner)
        b_layout.setSpacing(6)
        b_title = QLabel("PERMANENT DATA DESTRUCTION WARNING")
        b_title.setStyleSheet("color: #f87171; font-weight: 800; font-size: 14px;")
        b_desc = QLabel(
            f"You are about to permanently erase and repartition physical storage device:\n"
            f"• Device Name: {self.plan.device_name} ({self.plan.device_id})\n"
            f"• Manufacturer/Model: {self.plan.vendor} {self.plan.model}\n"
            f"• Capacity: {self.plan.capacity_gb:.2f} GB ({self.plan.capacity_bytes:,} bytes)\n"
            f"• Hardware Serial: {self.plan.serial or 'N/A'}\n"
            f"• Device Fingerprint: {self.plan.fingerprint[:16]}...\n\n"
            "All existing files, partitions, and operating systems on this drive will be destroyed."
        )
        b_desc.setStyleSheet("color: #fca5a5; font-size: 12px; line-height: 1.4;")
        b_layout.addWidget(b_title)
        b_layout.addWidget(b_desc)
        layout.addWidget(banner)

        # Operations summary
        ops_title = QLabel("Planned Destructive Actions:")
        ops_title.setStyleSheet("font-weight: 700; color: #f3f4f6; font-size: 12px;")
        layout.addWidget(ops_title)

        ops_box = QFrame()
        ops_box.setStyleSheet("background-color: #111827; border: 1px solid #374151; border-radius: 4px; padding: 10px;")
        ops_layout = QVBoxLayout(ops_box)
        ops_layout.setSpacing(4)
        for act in self.plan.destructive_actions:
            lbl = QLabel(f"• {act}")
            lbl.setStyleSheet("color: #9ca3af; font-size: 11px;")
            ops_layout.addWidget(lbl)
        layout.addWidget(ops_box)

        # Typing confirmation
        prompt_lbl = QLabel(
            f"To confirm, type the device name or 'YES' exactly below:"
        )
        prompt_lbl.setStyleSheet("color: #f3f4f6; font-weight: 600; font-size: 12px;")
        layout.addWidget(prompt_lbl)

        self.input_confirm = QLineEdit()
        self.input_confirm.setPlaceholderText(f"Type '{self.plan.device_name}' or 'YES'")
        self.input_confirm.setStyleSheet(
            "background-color: #1f2937; color: #ffffff; border: 1px solid #4b5563; "
            "padding: 8px 12px; border-radius: 4px; font-size: 13px;"
        )
        self.input_confirm.textChanged.connect(self._on_input_changed)
        layout.addWidget(self.input_confirm)

        # Dialog Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_confirm = QPushButton("I Understand, Wipe & Prepare USB")
        self.btn_confirm.setStyleSheet(
            "background-color: #dc2626; color: #ffffff; font-weight: 700; "
            "padding: 8px 16px; border-radius: 4px;"
        )
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_confirm)
        layout.addLayout(btn_layout)

    def _on_input_changed(self, text: str) -> None:
        typed = text.strip().upper()
        expected = self.plan.device_name.strip().upper()
        is_match = (typed == "YES") or (typed == expected) or (len(typed) >= 3 and typed in expected)
        self.btn_confirm.setEnabled(is_match)


class InstallationWorker(QObject):
    """Asynchronous worker executing disk installation off the main GUI thread."""

    progress_updated = Signal(int, int, str)
    installation_finished = Signal(object)  # Emits InstallationResult

    def __init__(
        self,
        target_path: Optional[Path] = None,
        disk_size_mb: int = 256,
        esp_size_mb: int = DEFAULT_ESP_SIZE_MB,
        menu_title: str = "MVentX Multiboot System",
        menu_timeout: int = 10,
        mode: str = "virtual",
        device: Optional[DeviceInfo] = None,
        expected_fingerprint: str = "",
        allow_physical_formatting: bool = False,
    ) -> None:
        super().__init__()
        self.target_path = target_path
        self.disk_size_mb = disk_size_mb
        self.esp_size_mb = esp_size_mb
        self.menu_title = menu_title
        self.menu_timeout = menu_timeout
        self.mode = mode
        self.device = device
        self.expected_fingerprint = expected_fingerprint
        self.allow_physical_formatting = allow_physical_formatting
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        def on_progress(step: int, total: int, msg: str) -> None:
            self.progress_updated.emit(step, total, msg)

        if self.mode == "physical" and self.device:
            service = InstallationService(allow_physical_formatting=self.allow_physical_formatting)
            res = service.install_to_physical_device(
                device=self.device,
                expected_fingerprint=self.expected_fingerprint,
                esp_size_mb=self.esp_size_mb,
                menu_title=self.menu_title,
                menu_timeout=self.menu_timeout,
                progress_callback=on_progress,
                is_cancelled=lambda: self._cancelled,
            )
        else:
            service = InstallationService(allow_physical_formatting=False)
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
        allow_physical_formatting: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.device_service = device_service
        self.allow_physical_formatting = allow_physical_formatting
        self.device_manager = DeviceManager(allow_physical_formatting=allow_physical_formatting)
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[InstallationWorker] = None
        self._detected_devices: list[DeviceInfo] = []

        self._setup_ui()
        self.set_allow_physical_formatting(self.allow_physical_formatting)
        self._update_preview()

    def set_allow_physical_formatting(self, enabled: bool) -> None:
        """Dynamically unlock or lock physical storage device preparation."""
        self.allow_physical_formatting = enabled
        self.device_manager.allow_physical_formatting = enabled
        self.radio_physical.setEnabled(enabled)

        if enabled:
            self.radio_physical.setText("Physical Storage Media (Removable USB Drives) [Destructive Mode]")
            if not self.chk_unlock_physical.isChecked():
                self.chk_unlock_physical.setChecked(True)
            self._refresh_physical_devices()
        else:
            self.radio_physical.setText("Physical Storage Media (Protected / Disabled by Policy)")
            if self.chk_unlock_physical.isChecked():
                self.chk_unlock_physical.setChecked(False)
            if self.radio_physical.isChecked():
                self.radio_virtual.setChecked(True)

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
        h_title = QLabel("USB Installation & Storage Preparation")
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
        sb_layout.setSpacing(6)
        sb_head = QLabel("Safety Protection Policy Active (Milestone 13 Physical Validation Protocol)")
        sb_head.setStyleSheet("color: #60a5fa; font-weight: 700; font-size: 13px;")
        sb_text = QLabel(
            "Physical disk formatting is locked by default to protect host drives. "
            "System disks, boot disks, internal drives, and C:\\ are strictly rejected by safety filters."
        )
        sb_text.setStyleSheet("color: #cbd5e1; font-size: 12px;")
        sb_layout.addWidget(sb_head)
        sb_layout.addWidget(sb_text)

        # Elevation status indicator
        if HostPlatform.is_windows() and not HostPlatform.is_elevated():
            elev_warn = QLabel(
                "⚠️ Elevation Status: MVentX is running without Administrator privileges. "
                "Physical USB disk modification will be rejected by the Windows storage subsystem. "
                "Please run MVentX as Administrator."
            )
            elev_warn.setStyleSheet("color: #f59e0b; font-size: 11px; font-weight: 600;")
            sb_layout.addWidget(elev_warn)

        self.chk_unlock_physical = QCheckBox("Enable physical USB storage device preparation (Destructive Policy Unlock)")
        self.chk_unlock_physical.setStyleSheet("color: #93c5fd; font-size: 12px; font-weight: 600;")
        self.chk_unlock_physical.toggled.connect(self.set_allow_physical_formatting)
        sb_layout.addWidget(self.chk_unlock_physical)

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

        self.radio_virtual.toggled.connect(self._on_target_mode_changed)
        tc_layout.addWidget(self.radio_virtual)
        tc_layout.addWidget(self.radio_physical)

        # Container for Virtual Target Options
        self.virtual_config_widget = QWidget()
        vc_layout = QVBoxLayout(self.virtual_config_widget)
        vc_layout.setContentsMargins(0, 0, 0, 0)
        vc_layout.setSpacing(10)

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
        vc_layout.addLayout(vpath_layout)

        v_size_layout = QHBoxLayout()
        v_size_lbl = QLabel("Virtual Disk Capacity:")
        v_size_lbl.setMinimumWidth(130)
        self.disk_size_combo = QComboBox()
        self.disk_size_combo.addItem("256 MiB (Compact Virtual Test Disk)", 256)
        self.disk_size_combo.addItem("512 MiB (Standard Virtual Disk)", 512)
        self.disk_size_combo.addItem("1024 MiB (1 GiB Test Disk)", 1024)
        self.disk_size_combo.addItem("2048 MiB (2 GiB Virtual Disk)", 2048)
        self.disk_size_combo.currentIndexChanged.connect(self._update_preview)
        v_size_layout.addWidget(v_size_lbl)
        v_size_layout.addWidget(self.disk_size_combo, stretch=1)
        vc_layout.addLayout(v_size_layout)

        tc_layout.addWidget(self.virtual_config_widget)

        # Container for Physical Target Options
        self.physical_config_widget = QWidget()
        self.physical_config_widget.setVisible(False)
        pc_config_layout = QVBoxLayout(self.physical_config_widget)
        pc_config_layout.setContentsMargins(0, 0, 0, 0)
        pc_config_layout.setSpacing(10)

        # Device Selection Row
        dev_sel_layout = QHBoxLayout()
        dev_sel_lbl = QLabel("Target Physical USB:")
        dev_sel_lbl.setMinimumWidth(130)
        self.physical_device_combo = QComboBox()
        self.physical_device_combo.currentIndexChanged.connect(self._on_physical_device_selected)

        self.btn_refresh_devices = QPushButton("Refresh Devices")
        self.btn_refresh_devices.clicked.connect(self._refresh_physical_devices)

        dev_sel_layout.addWidget(dev_sel_lbl)
        dev_sel_layout.addWidget(self.physical_device_combo, stretch=1)
        dev_sel_layout.addWidget(self.btn_refresh_devices)
        pc_config_layout.addLayout(dev_sel_layout)

        # Physical Device Details Card
        self.physical_details_card = QFrame()
        self.physical_details_card.setStyleSheet(
            "background-color: #111827; border: 1px solid #374151; "
            "border-radius: 6px; padding: 10px;"
        )
        self.physical_details_card.setVisible(False)
        pdc_layout = QVBoxLayout(self.physical_details_card)
        pdc_layout.setSpacing(4)
        self.pdc_title = QLabel("Hardware Identity & Attributes")
        self.pdc_title.setStyleSheet("font-weight: 700; color: #60a5fa; font-size: 12px;")
        self.pdc_details = QLabel()
        self.pdc_details.setStyleSheet("color: #cbd5e1; font-size: 11px; line-height: 1.4;")
        pdc_layout.addWidget(self.pdc_title)
        pdc_layout.addWidget(self.pdc_details)
        pc_config_layout.addWidget(self.physical_details_card)

        # Destructive Plan Warning
        self.destructive_plan_card = QFrame()
        self.destructive_plan_card.setStyleSheet(
            "background-color: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.3); "
            "border-radius: 6px; padding: 10px;"
        )
        self.destructive_plan_card.setVisible(False)
        dpc_layout = QVBoxLayout(self.destructive_plan_card)
        dpc_layout.setSpacing(4)
        dpc_head = QLabel("Destructive Action Plan Preview")
        dpc_head.setStyleSheet("color: #f87171; font-weight: 700; font-size: 12px;")
        self.dpc_text = QLabel()
        self.dpc_text.setStyleSheet("color: #fca5a5; font-size: 11px;")
        dpc_layout.addWidget(dpc_head)
        dpc_layout.addWidget(self.dpc_text)
        pc_config_layout.addWidget(self.destructive_plan_card)

        tc_layout.addWidget(self.physical_config_widget)

        # Sizing Grid (ESP Partition Size - Shared between virtual and physical)
        esp_layout = QHBoxLayout()
        esp_lbl = QLabel("EFI Partition Size (MVX_BOOT):")
        esp_lbl.setMinimumWidth(130)
        self.esp_size_combo = QComboBox()
        self.esp_size_combo.addItem("256 MiB (Recommended Standard)", 256)
        self.esp_size_combo.addItem("64 MiB (Compact Virtual Disk)", 64)
        self.esp_size_combo.addItem("128 MiB (Extended Module Buffer)", 128)
        self.esp_size_combo.addItem("32 MiB (Minimum Practical)", 32)
        self.esp_size_combo.currentIndexChanged.connect(self._update_preview)
        esp_layout.addWidget(esp_lbl)
        esp_layout.addWidget(self.esp_size_combo, stretch=1)
        tc_layout.addLayout(esp_layout)

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
            "I understand that proceeding will initialize the partition table and format the target storage."
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

    def _on_target_mode_changed(self) -> None:
        is_virtual = self.radio_virtual.isChecked()
        self.virtual_config_widget.setVisible(is_virtual)
        self.physical_config_widget.setVisible(not is_virtual)

        if not is_virtual:
            self._refresh_physical_devices()

        self._update_preview()
        self._on_confirm_toggled()

    def _refresh_physical_devices(self) -> None:
        self.physical_device_combo.blockSignals(True)
        self.physical_device_combo.clear()
        self.physical_device_combo.addItem("-- Select target USB storage device --", None)

        try:
            devices = self.device_manager.list_devices()
            # Filter for removable USB devices that are not system disks
            self._detected_devices = [
                d for d in devices
                if not d.is_system_disk and (d.device_type == DeviceType.REMOVABLE_USB or d.is_removable)
            ]
            for dev in self._detected_devices:
                cap_gb = dev.size_bytes / (1024 ** 3)
                label = f"{dev.vendor} {dev.model} ({cap_gb:.1f} GB) - {dev.device_id}"
                self.physical_device_combo.addItem(label, dev)
        except Exception as exc:
            logger.warning("Error enumerating physical devices: %s", exc)

        self.physical_device_combo.setCurrentIndex(0)
        self.physical_device_combo.blockSignals(False)
        self._on_physical_device_selected(0)

    def _on_physical_device_selected(self, index: int) -> None:
        device: Optional[DeviceInfo] = self.physical_device_combo.currentData()
        if device is None:
            self.physical_details_card.setVisible(False)
            self.destructive_plan_card.setVisible(False)
            self._update_preview()
            self._on_confirm_toggled()
            return

        cap_gb = device.size_bytes / (1024 ** 3)
        part_desc = ", ".join(
            f"{p.name} ({p.filesystem}, {p.size_bytes // (1024**2)} MiB)"
            for p in device.partitions
        ) or "No active partitions"

        # Diagnose USB installation state
        part_dicts = [
            {
                "PartitionNumber": p.partition_number,
                "FileSystem": p.filesystem,
                "FileSystemLabel": p.label,
                "DriveLetter": (p.mount_point or "")[:1],
            }
            for p in device.partitions
        ]
        part_style = getattr(device, "partition_style", "GPT") or "GPT"
        diag = InstallationRecoveryService.diagnose_installation_state(
            partition_style=part_style,
            partitions=part_dicts,
        )

        details_txt = (
            f"• Manufacturer / Model:  {device.vendor or 'Generic'} {device.model}\n"
            f"• Hardware Serial:        {device.serial_number or 'N/A'}\n"
            f"• Windows Identifier:     {device.device_id}\n"
            f"• Physical Capacity:      {cap_gb:.2f} GB ({device.size_bytes:,} bytes)\n"
            f"• Bus Type:               {getattr(device, 'bus_type', 'USB')}\n"
            f"• Partition Style:        {part_style} ({len(device.partitions)} partitions)\n"
            f"• Installation State:     {diag.state_name}\n"
            f"• Existing Partitions:   {part_desc}\n"
            f"• Recovery Plan:          {diag.recommended_action}"
        )
        self.pdc_details.setText(details_txt)
        self.physical_details_card.setVisible(True)

        # Destructive actions summary
        esp_size_mb = self.esp_size_combo.currentData() or 256
        if diag.state == USBInstallationState.EMPTY_GPT:
            action_desc = (
                f"• STATE-AWARE RECOVERY: Target is already empty GPT (0 partitions).\n"
                f"• PRESERVE: Skipping redundant Clear-Disk and Initialize-Disk.\n"
                f"• CREATE: Partition 1 'MVX_BOOT' (FAT32, {esp_size_mb} MiB ESP).\n"
                f"• CREATE: Partition 2 'MVX_DATA' (exFAT, remaining capacity).\n"
                f"• DEPLOY: GRUB 2.12 x86_64-efi bootloader and initial MVentX configuration."
            )
        elif diag.state == USBInstallationState.GPT_UNFORMATTED:
            action_desc = (
                f"• STATE-AWARE RECOVERY: Both partitions already allocated.\n"
                f"• PRESERVE: Formatting existing partitions directly without re-partitioning.\n"
                f"• FORMAT: Partition 1 'MVX_BOOT' (FAT32) and Partition 2 'MVX_DATA' (exFAT).\n"
                f"• DEPLOY: GRUB 2.12 x86_64-efi bootloader and initial MVentX configuration."
            )
            self.btn_install.setText("Start Installation & Verification")
        elif diag.state == USBInstallationState.GPT_FORMATTED_NO_GRUB:
            action_desc = (
                f"• STATE-AWARE RESUME: Partitions already formatted (MVX_BOOT FAT32 + MVX_DATA exFAT).\n"
                f"• PRESERVE: Zero repartitioning or reformatting will be performed.\n"
                f"• DEPLOY: GRUB 2.12 x86_64-efi bootloader binaries, runtime modules, and fonts.\n"
                f"• INITIALIZE: Directory skeleton (ISO/, config/, metadata/) and initial mventx.json.\n"
                f"• VERIFY: Structural and filesystem integrity."
            )
            self.btn_install.setText("Resume Installation (Deploy Bootloader)")
        else:
            action_desc = (
                f"• WIPE: Erase existing partition table and records on {device.device_id}.\n"
                f"• INITIALIZE: Write new GPT partition table.\n"
                f"• CREATE: Partition 1 'MVX_BOOT' (FAT32, {esp_size_mb} MiB ESP).\n"
                f"• CREATE: Partition 2 'MVX_DATA' (exFAT, remaining capacity).\n"
                f"• DEPLOY: GRUB 2.12 x86_64-efi bootloader and initial MVentX configuration."
            )
            self.btn_install.setText("Start Installation & Verification")
        self.dpc_text.setText(action_desc)
        self.destructive_plan_card.setVisible(True)

        self._update_preview()
        self._on_confirm_toggled()

    def _update_preview(self) -> None:
        esp_size_mb = self.esp_size_combo.currentData() or 64

        if self.radio_virtual.isChecked():
            disk_size_mb = self.disk_size_combo.currentData() or 256
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
                    f"• Target Mode: Virtual Raw Disk Image\n"
                    f"• Partition 1 (MVX_BOOT): LBA {plan.esp_partition.start_lba:,} .. {plan.esp_partition.end_lba:,} "
                    f"({esp_size_mb} MiB, Type: EFI System Partition)\n"
                    f"• Partition 2 (MVX_DATA): LBA {plan.data_partition.start_lba:,} .. {plan.data_partition.end_lba:,} "
                    f"({data_mb:.1f} MiB, Type: Basic Data Partition)\n"
                    f"• Alignment: 1 MiB ({plan.first_usable_lba} sectors) | Total Sectors: {plan.total_sectors:,}"
                )
            except Exception as exc:
                self.preview_details.setText(f"Layout calculation error: {exc}")

        else:
            # Physical target mode
            device: Optional[DeviceInfo] = self.physical_device_combo.currentData()
            if device is None:
                self.bar_esp.setText(f"MVX_BOOT\nFAT32 ESP\n{esp_size_mb} MiB")
                self.bar_data.setText("MVX_DATA\nexFAT Data\nRemaining")
                self.preview_details.setText("Select a target physical USB storage device to preview partition layout.")
                return

            try:
                plan = PartitionPlanner.calculate_plan(
                    disk_size_bytes=device.size_bytes,
                    esp_size_mb=esp_size_mb,
                    sector_size=device.sector_size or 512,
                )
                data_mb = plan.data_partition.size_mb
                data_gb = data_mb / 1024.0

                self.bar_esp.setText(f"MVX_BOOT\nFAT32 ESP\n{esp_size_mb} MiB")
                self.bar_data.setText(f"MVX_DATA\nexFAT Data\n{data_gb:.1f} GB")

                self.layout_bar.setStretch(0, 1)
                self.layout_bar.setStretch(1, 9)

                self.preview_details.setText(
                    f"• Target Mode: Physical USB Device ({device.name})\n"
                    f"• Partition 1 (MVX_BOOT): LBA {plan.esp_partition.start_lba:,} .. {plan.esp_partition.end_lba:,} "
                    f"({esp_size_mb} MiB, FAT32 ESP)\n"
                    f"• Partition 2 (MVX_DATA): LBA {plan.data_partition.start_lba:,} .. {plan.data_partition.end_lba:,} "
                    f"({data_gb:.2f} GB / {data_mb:.1f} MiB, exFAT Basic Data)\n"
                    f"• Alignment: 1 MiB ({plan.first_usable_lba} sectors) | Total Sectors: {plan.total_sectors:,}"
                )
            except Exception as exc:
                self.preview_details.setText(f"Layout calculation error: {exc}")

    def _on_confirm_toggled(self) -> None:
        confirmed = self.chk_confirm.isChecked()
        if self.radio_virtual.isChecked():
            has_path = bool(self.vpath_edit.text().strip())
            self.btn_install.setEnabled(confirmed and has_path)
        else:
            has_device = self.physical_device_combo.currentData() is not None
            self.btn_install.setEnabled(confirmed and has_device and self.allow_physical_formatting)

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
        esp_mb = self.esp_size_combo.currentData() or 64

        if self.radio_virtual.isChecked():
            target_str = self.vpath_edit.text().strip()
            if not target_str:
                QMessageBox.critical(self, "Invalid Path", "Please specify a destination image file path.")
                return

            target_path = Path(target_str)
            disk_mb = self.disk_size_combo.currentData() or 256

            # Disable controls during run
            self._set_controls_enabled(False)
            self._reset_progress_report()

            self._worker_thread = QThread()
            self._worker = InstallationWorker(
                target_path=target_path,
                disk_size_mb=disk_mb,
                esp_size_mb=esp_mb,
                menu_title="MVentX Multiboot System",
                menu_timeout=10,
                mode="virtual",
            )
            self._worker.moveToThread(self._worker_thread)
            self._worker_thread.started.connect(self._worker.run)
            self._worker.progress_updated.connect(self._on_worker_progress)
            self._worker.installation_finished.connect(self._on_worker_finished)
            self._worker_thread.start()

        else:
            # Physical Device Mode
            device: Optional[DeviceInfo] = self.physical_device_combo.currentData()
            if device is None:
                QMessageBox.critical(self, "No Device Selected", "Please select a target physical USB storage device.")
                return

            # Check administrative elevation on Windows
            if HostPlatform.is_windows() and not HostPlatform.is_elevated():
                QMessageBox.critical(
                    self,
                    "Administrator Elevation Required",
                    "Modifying physical storage devices requires Administrator privileges.\n"
                    "Please restart MVentX by right-clicking and selecting 'Run as administrator'.",
                )
                return

            # Generate destructive operation plan
            workflow = PhysicalDeviceWorkflow(
                device_manager=self.device_manager,
                allow_physical_formatting=self.allow_physical_formatting,
            )
            try:
                plan = workflow.generate_plan(device, esp_size_mb=esp_mb)
            except Exception as exc:
                QMessageBox.critical(self, "Plan Generation Failed", f"Cannot prepare device plan:\n{exc}")
                return

            # Launch Phase 2 confirmation modal dialog
            dialog = PhysicalConfirmationDialog(plan, parent=self)
            if dialog.exec() != QDialog.Accepted:
                logger.info("Physical drive preparation cancelled by user in Phase 2 modal confirmation.")
                return

            # Phase 2 accepted - proceed with asynchronous installation
            self._set_controls_enabled(False)
            self._reset_progress_report()

            self._worker_thread = QThread()
            self._worker = InstallationWorker(
                device=device,
                expected_fingerprint=plan.fingerprint,
                esp_size_mb=esp_mb,
                menu_title="MVentX Multiboot System",
                menu_timeout=10,
                mode="physical",
                allow_physical_formatting=True,
            )
            self._worker.moveToThread(self._worker_thread)
            self._worker_thread.started.connect(self._worker.run)
            self._worker.progress_updated.connect(self._on_worker_progress)
            self._worker.installation_finished.connect(self._on_worker_finished)
            self._worker_thread.start()

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.btn_install.setEnabled(enabled and self.chk_confirm.isChecked())
        self.chk_confirm.setEnabled(enabled)
        self.radio_virtual.setEnabled(enabled)
        self.radio_physical.setEnabled(enabled and self.allow_physical_formatting)
        self.chk_unlock_physical.setEnabled(enabled)
        self.vpath_edit.setEnabled(enabled)
        self.btn_browse.setEnabled(enabled)
        self.disk_size_combo.setEnabled(enabled)
        self.physical_device_combo.setEnabled(enabled)
        self.btn_refresh_devices.setEnabled(enabled)
        self.esp_size_combo.setEnabled(enabled)

    def _reset_progress_report(self) -> None:
        while self.rb_layout.count() > 0:
            item = self.rb_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.report_box.setVisible(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_lbl.setVisible(True)
        self.status_lbl.setText("Starting installation pipeline...")

    def _on_worker_progress(self, step: int, total: int, message: str) -> None:
        self.progress_bar.setValue(step)
        self.status_lbl.setText(f"Step {step}/{total}: {message}")

    def _on_worker_finished(self, result: InstallationResult) -> None:
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
            self._worker = None

        self._set_controls_enabled(True)
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

            if self.radio_physical.isChecked():
                post_hint = QLabel(
                    "\nNext Steps:\n"
                    "1. Safely eject the USB drive before disconnecting.\n"
                    "2. Re-insert to verify MVentX recognizes the device as VALID_MVENTX.\n"
                    "3. Reboot computer and enter UEFI Firmware Settings to test boot."
                )
                post_hint.setStyleSheet("color: #93c5fd; font-size: 12px; font-weight: 600;")
                self.rb_layout.addWidget(post_hint)

            QMessageBox.information(
                self,
                "Installation Successful",
                f"MVentX storage target prepared successfully:\n{result.target_path}\n\n"
                f"All {rep.passed_count} structural and filesystem verification checks passed.",
            )
        else:
            err = result.error_message or "Unknown verification error"
            self.status_lbl.setText("✗ Installation Failed")
            self.status_lbl.setStyleSheet("color: #ef4444; font-weight: 700; font-size: 13px;")

            head = QLabel("Installation Errors & Diagnostics:")
            head.setStyleSheet("color: #ef4444; font-weight: 700; font-size: 13px;")
            self.rb_layout.addWidget(head)

            detail = QLabel(err)
            detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
            detail.setStyleSheet(
                "color: #fca5a5; font-size: 12px; background-color: rgba(239, 68, 68, 0.1); "
                "border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 4px; padding: 10px;"
            )
            detail.setWordWrap(True)
            self.rb_layout.addWidget(detail)

            rec = QLabel(
                "Troubleshooting Steps:\n"
                "• Verify the USB flash drive is firmly connected.\n"
                "• Ensure no other applications or File Explorer windows are accessing the drive.\n"
                "• Re-launch MVentX with Administrator privileges if storage access was denied."
            )
            rec.setStyleSheet("color: #94a3b8; font-size: 11px; padding: 6px;")
            rec.setWordWrap(True)
            self.rb_layout.addWidget(rec)

            btn_copy_diag = QPushButton("Copy Diagnostic Report to Clipboard")
            btn_copy_diag.setStyleSheet(
                "background-color: #374151; color: #f3f4f6; font-size: 11px; padding: 6px 12px; border-radius: 4px;"
            )
            def copy_err() -> None:
                clipboard = QApplication.clipboard()
                if clipboard:
                    clipboard.setText(err)
                    btn_copy_diag.setText("✓ Diagnostic Report Copied!")
            btn_copy_diag.clicked.connect(copy_err)
            self.rb_layout.addWidget(btn_copy_diag)

            QMessageBox.critical(self, "Installation Failed", f"Physical USB installation failed:\n\n{err}")
