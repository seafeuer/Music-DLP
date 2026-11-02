"""A search box that behaves the way people expect.

The clear button is there, but Escape is the key people press. Qt leaves
Escape to the dialog, so a search box that does not handle it leaves the
filter stuck with no obvious way back.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QLineEdit


class SearchBox(QLineEdit):
    def __init__(self, placeholder: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setClearButtonEnabled(True)
        self.setPlaceholderText(placeholder)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape and self.text():
            self.clear()
            event.accept()
            return
        super().keyPressEvent(event)
