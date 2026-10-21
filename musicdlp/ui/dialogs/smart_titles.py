"""Review smart title suggestions that were not applied automatically."""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from musicdlp.core.models import TrackRow


class SmartTitlesDialog(QDialog):
    """Lists suggested names next to the current ones."""

    def __init__(self, rows: Iterable[TrackRow], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Suggested titles")
        self.resize(920, 560)
        self.rows = [
            row for row in rows
            if row.track.smart_title and row.track.has_suggestion
        ]
        self.accepted_ids: list[int] = []

        layout = QVBoxLayout(self)

        intro = QLabel(
            "These names were worked out from the upload titles. The app was "
            "not sure enough to use them by itself. Tick the ones that look "
            "right, then press Apply. Nothing is renamed until you do."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        self.table = QTableWidget(len(self.rows), 5)
        self.table.setHorizontalHeaderLabels(
            ["Use", "Current title", "Current artist", "Suggested title",
             "Suggested artist"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 48)

        for index, row in enumerate(self.rows):
            track = row.track
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            confidence = int(round((track.smart_confidence or 0) * 100))
            use.setCheckState(
                Qt.Checked if confidence >= 60 else Qt.Unchecked
            )
            use.setToolTip(f"{confidence}% sure — {track.smart_reason or ''}")
            self.table.setItem(index, 0, use)
            self.table.setItem(index, 1, QTableWidgetItem(track.display_title))
            self.table.setItem(index, 2, QTableWidgetItem(track.display_artist))
            self.table.setItem(
                index, 3, QTableWidgetItem(track.smart_title or "")
            )
            self.table.setItem(
                index, 4, QTableWidgetItem(track.smart_artist or "")
            )

        layout.addWidget(self.table, 1)

        if not self.rows:
            intro.setText(
                "There are no suggestions left to review for this link."
            )

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        apply_button = buttons.addButton(
            "Apply selected", QDialogButtonBox.AcceptRole
        )
        apply_button.setObjectName("primary")
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply(self) -> None:
        self.accepted_ids = [
            row.track.id
            for index, row in enumerate(self.rows)
            if self.table.item(index, 0).checkState() == Qt.Checked
        ]
        self.accept()
