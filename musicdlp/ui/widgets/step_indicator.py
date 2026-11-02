"""The three-step pipeline strip: Download → Convert → Spotify.

The strip answers one question at a glance — how far a batch has got, and
what the next action is — so it stays compact, uses the theme's colours by
role (a theme switch restyles it), and never offers a second primary action
next to the real one in the toolbar.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

WAITING = "waiting"
ACTIVE = "active"
DONE = "done"
ERROR = "error"


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
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(8)

        self.circle = QLabel(str(number))
        self.circle.setObjectName("stepCircle")
        self.circle.setFixedSize(24, 24)
        self.circle.setAlignment(Qt.AlignCenter)
        self.circle.setProperty("state", WAITING)
        layout.addWidget(self.circle)

        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-weight: 600;")
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("mutedSmall")
        text_box.addWidget(self.title_label)
        text_box.addWidget(self.detail_label)
        layout.addLayout(text_box, 1)

    def set_title(self, title: str) -> None:
        if self.title_label.text() != title:
            self.title_label.setText(title)

    def set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        mark = "✓" if state == DONE else ("!" if state == ERROR else str(self.number))
        if self.circle.text() != mark:
            self.circle.setText(mark)
        if self.circle.property("state") != state:
            self.circle.setProperty("state", state)
            style = self.circle.style()
            style.unpolish(self.circle)
            style.polish(self.circle)
            self.circle.update()
        self.detail_label.setText(detail)

    def set_clickable(self, clickable: bool) -> None:
        self.clickable = clickable
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:
        if self.clickable:
            self.clicked.emit()
        super().mousePressEvent(event)


class StepIndicator(QWidget):
    """Shows where a batch is in the pipeline."""

    step_clicked = Signal(int)

    TITLES = ("Download", "Convert", "Send to Spotify")
    TOOLTIPS = (
        "Reads the audio from each link you added",
        "Turns the downloaded audio into tagged files",
        "Copies the finished files into your Spotify folder",
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.steps: list[_Step] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        for index, title in enumerate(self.TITLES):
            step = _Step(index + 1, title)
            step.setToolTip(self.TOOLTIPS[index])
            step.clicked.connect(lambda i=index: self.step_clicked.emit(i))
            self.steps.append(step)
            layout.addWidget(step, 1)

    def set_titles(self, convert: str) -> None:
        """Name the middle step after the format actually being written."""
        self.steps[1].set_title(convert)

    def set_clickable(self, clickable: bool) -> None:
        for step in self.steps:
            step.set_clickable(clickable)

    def reset(
        self,
        selection_detail: str = "",
        enabled: tuple[bool, bool, bool] = (True, True, True),
    ) -> None:
        details = [
            selection_detail or "Tick the songs you want",
            "",
            "",
        ]
        for step, detail in zip(self.steps, details):
            step.set_state(WAITING, detail)

    def set_step(self, index: int, state: str, detail: str = "") -> None:
        if 0 <= index < len(self.steps):
            self.steps[index].set_state(state, detail)

    def set_tooltip(self, index: int, text: str) -> None:
        if 0 <= index < len(self.steps):
            self.steps[index].setToolTip(text)

    def mark_done(self) -> None:
        for step in self.steps:
            step.set_state(DONE, "Done")

    def set_enabled_steps(self, enabled: tuple[bool, bool, bool]) -> None:
        for step, on in zip(self.steps, enabled):
            step.setEnabled(on)
