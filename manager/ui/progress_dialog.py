"""Reusable Asynchronous Progress Dialog for MVentX Manager.

Provides a responsive modal dialog with progress bar, throughput metrics,
and cancellation support for long-running disk operations.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from manager.services.workers import BootSupportRepairWorker, ChecksumWorker, ISOCopyWorker


def _fmt_bytes(num_bytes: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PiB"


class OperationProgressDialog(QDialog):
    """Progress dialog for background workers with cancellation support."""

    def __init__(
        self,
        title: str,
        operation_description: str,
        worker: ISOCopyWorker | ChecksumWorker | BootSupportRepairWorker,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.worker = worker
        self.setWindowTitle(title)
        self.setFixedSize(480, 180)
        self.setWindowModality(Qt.WindowModal)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self.title_lbl = QLabel(f"<b>{operation_description}</b>")
        self.title_lbl.setStyleSheet("font-size: 14px; color: #f3f4f6;")
        layout.addWidget(self.title_lbl)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_lbl = QLabel("Starting operation...")
        self.status_lbl.setStyleSheet("color: #9ca3af; font-size: 12px;")
        layout.addWidget(self.status_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        self._wire_worker()

    def _wire_worker(self) -> None:
        if isinstance(self.worker, ISOCopyWorker):
            self.worker.progress.connect(self._on_copy_progress)
        elif isinstance(self.worker, ChecksumWorker):
            self.worker.progress.connect(self._on_checksum_progress)
        elif isinstance(self.worker, BootSupportRepairWorker):
            self.worker.progress.connect(self._on_step_progress)

        self.worker.finished_success.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.cancelled.connect(self._on_cancelled)

    def _on_copy_progress(self, copied: int, total: int, percent: int, speed: float) -> None:
        self.progress_bar.setValue(percent)
        self.status_lbl.setText(
            f"{_fmt_bytes(copied)} of {_fmt_bytes(total)} ({percent}%) • {speed:.1f} MiB/s"
        )

    def _on_checksum_progress(self, read_bytes: int, total: int, percent: int) -> None:
        self.progress_bar.setValue(percent)
        self.status_lbl.setText(f"Processed {_fmt_bytes(read_bytes)} of {_fmt_bytes(total)} ({percent}%)")

    def _on_step_progress(self, percent: int, message: str) -> None:
        self.progress_bar.setValue(percent)
        self.status_lbl.setText(message)

    def _on_cancel_clicked(self) -> None:
        self.btn_cancel.setEnabled(False)
        self.status_lbl.setText("Cancelling and cleaning up partial files...")
        self.worker.cancel()

    def _on_success(self, _result: object) -> None:
        self.accept()

    def _on_failure(self, _err: str) -> None:
        self.reject()

    def _on_cancelled(self) -> None:
        self.reject()

