"""Bulk link entry dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
)

from musicdlp.services.urls import split_links


class BulkAddDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add multiple links")
        self.resize(620, 360)
        self.links: list[str] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Paste one link per line:"))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            "https://www.youtube.com/playlist?list=...\n"
            "https://youtu.be/...\n"
            "https://x.com/user/status/..."
        )
        layout.addWidget(self.text, 1)

        self.error = QLabel("")
        self.error.setStyleSheet("color: #dc2626;")
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        links = split_links(self.text.toPlainText())
        if not links:
            self.error.setText("No usable links found.")
            return
        self.links = links
        self.accept()
