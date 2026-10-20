"""A single, prominent prompt for whatever setup is still missing."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


class SetupBanner(QFrame):
    """Shows one actionable setup prompt at a time."""

    action_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("setupBanner")
        self._kind: Optional[str] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        self.icon = QLabel("!")
        self.icon.setFixedWidth(18)
        self.icon.setStyleSheet("font-weight: 700; font-size: 15px;")
        layout.addWidget(self.icon)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message, 1)

        self.button = QPushButton("Fix")
        self.button.clicked.connect(self.action_clicked)
        layout.addWidget(self.button)

        self.hide()

    def show_prompt(self, kind: str, message: str, button_text: str) -> None:
        if self._kind == kind and self.isVisible():
            self.message.setText(message)
            self.button.setText(button_text)
            return
        self._kind = kind
        self.message.setText(message)
        self.button.setText(button_text)
        self.setStyleSheet(
            "#setupBanner {"
            " background: rgba(245, 158, 11, 0.14);"
            " border: 1px solid rgba(245, 158, 11, 0.55);"
            " border-radius: 8px;"
            "}"
        )
        self.show()

    def clear_prompt(self) -> None:
        self._kind = None
        self.hide()

    @property
    def kind(self) -> Optional[str]:
        return self._kind
