"""The three-step pipeline strip: Download → Convert → Spotify."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

WAITING = "waiting"
ACTIVE = "active"
DONE = "done"
ERROR = "error"

CIRCLE_STYLE = {
    WAITING: "background: #e5e7eb; color: #4b5563;",
    ACTIVE: "background: #3b82f6; color: white;",
    DONE: "background: #22c55e; color: white;",
    ERROR: "background: #ef4444; color: white;",
}


class _Step(QFrame):
    clicked = Signal()

    def __init__(self, number: int, title: str, parent=None) -> None:
        super().__init__(parent)
        self.number = number
        self.state = WAITING
        self.clickable = False
        self.setObjectName("step")
        self.setCursor(Qt.ArrowCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        self.circle = QLabel(str(number))
        self.circle.setFixedSize(26, 26)
        self.circle.setAlignment(Qt.AlignCenter)
        self.circle.setStyleSheet(
            CIRCLE_STYLE[WAITING] + " border-radius: 13px; font-weight: 700;"
        )
        layout.addWidget(self.circle)

        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-weight: 600;")
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("mutedSmall")
        text_box.addWidget(self.title_label)
        text_box.addWidget(self.detail_label)
        layout.addLayout(text_box, 1)

    def set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        style = CIRCLE_STYLE.get(state, CIRCLE_STYLE[WAITING])
        mark = "✓" if state == DONE else ("!" if state == ERROR else str(self.number))
        self.circle.setText(mark)
        self.circle.setStyleSheet(
            style + " border-radius: 13px; font-weight: 700;"
        )
        self.detail_label.setText(detail)

    def set_clickable(self, clickable: bool) -> None:
        self.clickable = clickable
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        self.setStyleSheet(
            "#step { border-radius: 8px; }"
            "#step:hover { background: rgba(128,128,128,0.12); }"
            if clickable else ""
        )

    def mousePressEvent(self, event) -> None:
        if self.clickable:
            self.clicked.emit()
        super().mousePressEvent(event)


class StepIndicator(QWidget):
    """Shows where a batch is in the pipeline."""

    step_clicked = Signal(int)

    TITLES = ("Download", "Convert to MP3", "Send to Spotify")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.steps: list[_Step] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        for index, title in enumerate(self.TITLES):
            step = _Step(index + 1, title)
            step.clicked.connect(lambda i=index: self.step_clicked.emit(i))
            self.steps.append(step)
            layout.addWidget(step, 1)

    def set_clickable(self, clickable: bool) -> None:
        for step in self.steps:
            step.set_clickable(clickable)
    def reset(self, selection_detail: str = "", enabled: tuple[bool, bool, bool] = (True, True, True)) -> None:
        details = [
            selection_detail or "Select songs to start",
            "Runs after the download",
            "Copies songs into your Spotify folder",
        ]
        for step, detail, on in zip(self.steps, details, enabled):
            step.set_state(WAITING, detail)
            # Keep the text readable when a step is not yet available; only
            # the cursor and click behavior change.
            step.setEnabled(True)
            step.set_clickable(step.clickable and on)

    def set_step(self, index: int, state: str, detail: str = "") -> None:
        if 0 <= index < len(self.steps):
            self.steps[index].set_state(state, detail)

    def mark_done(self) -> None:
        for step in self.steps:
            step.set_state(DONE, "Done")

    def set_enabled_steps(self, enabled: tuple[bool, bool, bool]) -> None:
        for step, on in zip(self.steps, enabled):
            step.setEnabled(on)
