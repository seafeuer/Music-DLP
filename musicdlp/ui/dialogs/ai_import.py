"""Paste or open a chatbot's reply and import the song names."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QLabel, QPlainTextEdit, QVBoxLayout,
)

from musicdlp.ui.dialogs.footer import DialogFooter
from musicdlp.ui.dialogs.sizing import fit_dialog

from musicdlp.services.ai_export import ImportedName, parse_reply
from musicdlp.ui.theme import set_tone


class AIImportDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import AI reply")
        fit_dialog(self, 680, 480)
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
        set_tone(self.error, "danger")
        layout.addWidget(self.error)

        self.footer = DialogFooter(self)
        self.footer.add_action("Open file…", self._open_file)
        self.footer.set_primary("Import", self._accept)
        layout.addWidget(self.footer)

        clipboard = QApplication.clipboard().text()
        if clipboard.strip().startswith(("[", "{")):
            self.text.setPlainText(clipboard)

        # Once the text changes, an old complaint no longer describes it.
        self.text.textChanged.connect(self._clear_error)

    def _clear_error(self) -> None:
        if self.error.text():
            self.error.clear()

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
