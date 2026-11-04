"""Bulk link entry dialog."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QVBoxLayout

from musicdlp.ui.dialogs.footer import DialogFooter
from musicdlp.ui.dialogs.sizing import fit_dialog

from musicdlp.services.urls import split_links
from musicdlp.ui.theme import set_tone


class _LinkBox(QPlainTextEdit):
    """A multi-line box where Enter submits and Shift+Enter starts a line.

    The footer makes "Add links" the default button, but a text box eats
    Return, so Enter did nothing and the shortcut list was wrong.
    """

    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not (event.modifiers() & Qt.ShiftModifier)
        ):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class BulkAddDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add multiple links")
        fit_dialog(self, 620, 360)
        self.links: list[str] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Paste one link per line:"))
        self.text = _LinkBox()
        self.text.submitted.connect(self._accept)
        self.text.setPlaceholderText(
            "https://www.youtube.com/playlist?list=...\n"
            "https://youtu.be/...\n"
            "https://x.com/user/status/..."
        )
        layout.addWidget(self.text, 1)

        self.error = QLabel("")
        set_tone(self.error, "danger")
        layout.addWidget(self.error)

        self.footer = DialogFooter(self)
        self.footer.set_primary("Add links", self._accept)
        layout.addWidget(self.footer)

    def _accept(self) -> None:
        links = split_links(self.text.toPlainText())
        if not links:
            self.error.setText("No usable links found.")
            return
        self.links = links
        self.accept()
