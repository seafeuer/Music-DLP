"""Bottom status bar: progress, current item, cancel, output, Spotify, logs."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget,
)


def _format_speed(value) -> str:
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB/s"
    if value >= 1024:
        return f"{value / 1024:.0f} KB/s"
    return f"{value:.0f} B/s"


def _format_eta(value) -> str:
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    minutes, secs = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}:{secs:02d}"


class StatusBarWidget(QWidget):
    cancel_clicked = Signal()
    open_output = Signal()
    spotify_clicked = Signal()
    log_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.output_path: Optional[Path] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(180, 10)
        layout.addWidget(self.progress)

        self.status_label = QLabel("Ready")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status_label, 1)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("muted")
        layout.addWidget(self.detail_label)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.clicked.connect(self.cancel_clicked)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)

        self.output_button = QPushButton("Output folder")
        self.output_button.clicked.connect(self.open_output)
        layout.addWidget(self.output_button)

        self.spotify_button = QPushButton("Spotify")
        self.spotify_button.clicked.connect(self.spotify_clicked)
        layout.addWidget(self.spotify_button)

        self.log_button = QPushButton("Logs")
        self.log_button.clicked.connect(self.log_clicked)
        layout.addWidget(self.log_button)

    def set_progress(self, data: dict) -> None:
        percent = float(data.get("percent") or 0)
        self.progress.setValue(int(max(0, min(100, percent))))
        stage = (data.get("stage") or "").title()
        title = data.get("title") or ""
        if stage and title:
            self.status_label.setText(f"{stage}: {title}")
        elif stage:
            self.status_label.setText(stage)
        else:
            self.status_label.setText(title)

        details = []
        speed = _format_speed(data.get("speed"))
        if speed:
            details.append(speed)
        eta = _format_eta(data.get("eta"))
        if eta:
            details.append(f"ETA {eta}")
        self.detail_label.setText(" · ".join(details))
        self.cancel_button.show()

    def set_add_progress(self, current: int, total: int, url: str = "") -> None:
        self.cancel_button.show()
        self.progress.setValue(int(current / total * 100) if total else 0)
        label = f"Reading link {current}/{total}" if total else "Reading link"
        if url:
            label += f": {url}"
        self.status_label.setText(label)
        self.detail_label.setText("")

    def set_busy(self, message: str) -> None:
        self.cancel_button.show()
        self.progress.setValue(0)
        self.status_label.setText(message)
        self.detail_label.setText("")

    def set_idle(self, message: str = "Ready") -> None:
        self.cancel_button.hide()
        self.progress.setValue(0)
        self.status_label.setText(message)
        self.detail_label.setText("")

    def set_output_path(self, path: Path) -> None:
        self.output_path = Path(path)
        self.output_button.setToolTip(str(path))

    def set_spotify_state(self, text: str, tooltip: str = "") -> None:
        self.spotify_button.setToolTip(tooltip)
        self.spotify_button.setText(text)
