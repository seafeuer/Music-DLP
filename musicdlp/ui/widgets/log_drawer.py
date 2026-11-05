"""Collapsible activity log drawer."""

from __future__ import annotations

import time

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
    QWidget,
)

LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}


class LogDrawer(QWidget):
    open_log_file = Signal()
    closed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lines: list[tuple[str, str, str]] = []
        self.max_lines = 3000

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        title = QLabel("Activity log")
        title.setStyleSheet("font-weight: 600;")
        header.addWidget(title)

        self.filter_combo = QComboBox()
        for label, value in [
            ("All", "DEBUG"),
            ("Info", "INFO"),
            ("Warnings", "WARNING"),
            ("Errors", "ERROR"),
        ]:
            self.filter_combo.addItem(label, value)
        self.filter_combo.setCurrentIndex(1)
        self.filter_combo.currentIndexChanged.connect(self._rerender)
        header.addWidget(self.filter_combo)

        header.addStretch(1)

        open_button = QPushButton("Open log file")
        open_button.clicked.connect(self.open_log_file)
        header.addWidget(open_button)

        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear)
        header.addWidget(clear_button)

        close_button = QPushButton("Hide")
        close_button.clicked.connect(self.closed)
        header.addWidget(close_button)

        layout.addLayout(header)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(self.max_lines)
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(9)
        self.text.setFont(font)
        layout.addWidget(self.text, 1)

    def append(self, level: str, message: str) -> None:
        level = (level or "INFO").upper()
        stamp = time.strftime("%H:%M:%S")
        self.lines.append((level, stamp, message))
        if len(self.lines) > self.max_lines:
            self.lines = self.lines[-self.max_lines:]
        if self._passes(level):
            was_at_bottom = self._at_bottom()
            self.text.appendPlainText(self._format(level, stamp, message))
            # Reading back through the log used to be impossible: every
            # new line dragged the view to the bottom.
            if was_at_bottom:
                self._scroll_to_bottom()

    def clear(self) -> None:
        self.lines.clear()
        self.text.clear()

    def _threshold(self) -> int:
        return LEVEL_ORDER.get(self.filter_combo.currentData(), 0)

    def _passes(self, level: str) -> bool:
        return LEVEL_ORDER.get(level, 1) >= self._threshold()

    def _at_bottom(self) -> bool:
        scroll = self.text.verticalScrollBar()
        return scroll.value() >= scroll.maximum() - 4

    def _scroll_to_bottom(self) -> None:
        scroll = self.text.verticalScrollBar()
        scroll.setValue(scroll.maximum())

    @staticmethod
    def _format(level: str, stamp: str, message: str) -> str:
        return f"{stamp}  {level:<7}  {message}"

    def _rerender(self) -> None:
        keep = self._at_bottom()
        self.text.clear()
        for level, stamp, message in self.lines:
            if self._passes(level):
                self.text.appendPlainText(self._format(level, stamp, message))
        if keep:
            self._scroll_to_bottom()
