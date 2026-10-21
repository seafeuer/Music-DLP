"""Paste or open a chatbot's reply and import the song names."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QLabel,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from musicdlp.services.ai_export import ImportedName, parse_reply


class AIImportDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import AI reply")
        self.resize(680, 480)
        self.results: list[ImportedName] = []

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Paste the chatbot's reply below, or open the file it saved. "
            "The names are added to Suggested titles, where you can review "
            "them before applying."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(
            '[{"id": "12", "title": "At The Gates", '
            '"artist": "Drake, Lil Uzi Vert", "confidence": 0.9}]'
        )
        layout.addWidget(self.text, 1)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #dc2626;")
        layout.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        import_button = buttons.addButton("Import", QDialogButtonBox.AcceptRole)
        import_button.setObjectName("primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        open_button = QPushButton("Open file…")
        open_button.clicked.connect(self._open_file)
        buttons.addButton(open_button, QDialogButtonBox.ActionRole)
        layout.addWidget(buttons)

        clipboard = QApplication.clipboard().text()
        if clipboard.strip().startswith(("[", "{")):
            self.text.setPlainText(clipboard)

    def _open_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open AI reply", "", "Text files (*.txt *.json);;All files (*)"
        )
        if not filename:
            return
        try:
            from pathlib import Path

            self.text.setPlainText(
                Path(filename).read_text(encoding="utf-8", errors="replace")
            )
        except OSError as exc:
            self.error.setText(f"Could not read that file: {exc}")

    def _accept(self) -> None:
        try:
            self.results = parse_reply(self.text.toPlainText())
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()
