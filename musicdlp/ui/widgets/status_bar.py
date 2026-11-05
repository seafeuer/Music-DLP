"""Bottom status bar: what is happening now, and where things live.

The bar answers "is it working?" before anything else. While idle it shows
one line of plain text and nothing else moves; while a job runs it shows a
progress bar, the current song with its own percentage, the overall
percentage, speed and time left, and a Cancel button right beside them.
"""

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
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(160, 8)
        self.progress.hide()
        layout.addWidget(self.progress, 0, Qt.AlignVCenter)

        self.status_label = QLabel("Ready")
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # Enough room for "Download: <song> — 42%" before the ETA gives up
        # its space. The ETA used to keep its full width and squeeze this
        # line to "Download…".
        self.status_label.setMinimumWidth(220)
        layout.addWidget(self.status_label, 1)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("muted")
        self.detail_label.setMaximumWidth(320)
        self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.detail_label)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("danger")
        self.cancel_button.setToolTip("Stop the job after the current file")
        self.cancel_button.clicked.connect(self.cancel_clicked)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)

        self.output_button = QPushButton("Output folder")
        self.output_button.setObjectName("quiet")
        self.output_button.setToolTip("Open the folder your songs are saved in")
        self.output_button.clicked.connect(self.open_output)
        layout.addWidget(self.output_button)

        self.spotify_button = QPushButton("Spotify")
        self.spotify_button.setObjectName("quiet")
        self.spotify_button.clicked.connect(self.spotify_clicked)
        layout.addWidget(self.spotify_button)

        self.log_button = QPushButton("Logs")
        self.log_button.setObjectName("quiet")
        self.log_button.setToolTip("Show the activity log (Ctrl+L)")
        self.log_button.clicked.connect(self.log_clicked)
        layout.addWidget(self.log_button)

    # ------------------------------------------------------------- progress

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # At a narrow window the location shortcuts leave the status line
        # no room. Settings and the menu still open both.
        narrow = self.width() < 900
        self.output_button.setVisible(not narrow)
        self.spotify_button.setVisible(not narrow)

    def set_progress(self, data: dict) -> None:
        percent = float(data.get("percent") or 0)
        self.progress.show()
        self.progress.setValue(int(max(0, min(100, percent))))

        stage = (data.get("stage") or "").title()
        title = data.get("title") or ""
        item = float(data.get("item_percent") or 0)
        narrow = self.width() < 900
        if title and stage and not narrow:
            self.status_label.setText(f"{stage}: {title} — {item:.0f}%")
        elif title:
            self.status_label.setText(f"{title} — {item:.0f}%")
        elif stage:
            self.status_label.setText(stage)
        else:
            self.status_label.setText(title)

        eta = _format_eta(data.get("eta"))
        speed = _format_speed(data.get("speed"))
        if narrow:
            # Keep the two numbers that answer "how long?" and drop the
            # throughput, which needs the width this line does not have.
            details = [f"{percent:.0f}%"]
            if eta:
                details.append(f"{eta} left")
        else:
            details = [f"{percent:.0f}% overall"]
            if speed:
                details.insert(0, speed)
            if eta:
                details.append(f"{eta} left")
        self.detail_label.setText(" · ".join(details))
        self.cancel_button.show()

    def set_add_progress(self, current: int, total: int, url: str = "") -> None:
        self.cancel_button.show()
        self.progress.show()
        self.progress.setValue(int(current / total * 100) if total else 0)
        label = f"Reading link {current}/{total}" if total else "Reading link"
        if url:
            label += f" · {_short_url(url)}"
        self.status_label.setText(label)
        self.detail_label.setText("")

    # ---------------------------------------------------------------- states

    def set_busy(self, message: str) -> None:
        self.cancel_button.show()
        self.progress.show()
        self.status_label.setText(message)
        self.detail_label.setText("")

    def set_idle(self, message: str = "Ready") -> None:
        self.cancel_button.hide()
        self.progress.hide()
        self.status_label.setText(message)
        self.detail_label.setText("")

    def set_message(self, message: str) -> None:
        """Say something without disturbing a running job's progress."""
        self.status_label.setText(message)

    # ------------------------------------------------------------- locations

    def set_output_path(self, path: Path) -> None:
        self.output_path = Path(path)
        self.output_button.setToolTip(f"Open {path}")

    def set_spotify_state(self, text: str, tooltip: str = "") -> None:
        # A missing Spotify used to look identical to a working one until
        # the user hovered. The button stays short, but it now says the
        # part that changes between the two states.
        self.spotify_button.setToolTip(tooltip or text)
        missing = "not found" in (text or "").lower()
        # The button opens the folder dialog, so it says folder, not
        # "Spotify" (which reads like "launch Spotify").
        self.spotify_button.setText(
            "Spotify not found" if missing else "Spotify folder"
        )


def _short_url(url: str) -> str:
    """A link without its scheme, folded to a readable width."""
    text = (url or "").strip()
    for prefix in ("https://", "http://", "www."):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text if len(text) <= 48 else text[:45] + "…"
