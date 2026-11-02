"""A label that shortens its text instead of spilling into its neighbours.

A long link title used to run straight over the details panel heading at
125% Windows scaling, because a plain QLabel draws its full text whatever
width it is given. This one elides with "…" and keeps the whole text in
its tooltip.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(60)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._apply()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        metrics = QFontMetrics(self.font())
        width = max(20, self.width() - 2)
        elided = metrics.elidedText(
            self._full_text, Qt.ElideRight, width
        )
        if super().text() != elided:
            super().setText(elided)
