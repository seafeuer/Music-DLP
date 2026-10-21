"""Review, edit, and AI-fill suggested song titles."""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QHeaderView,
    QLabel, QMessageBox, QProgressDialog, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from musicdlp.core.models import TrackRow
from musicdlp.services.ai import AIError, AISettings, suggest_names

COL_USE = 0
COL_CURRENT_TITLE = 1
COL_CURRENT_ARTIST = 2
COL_TITLE = 3
COL_ARTIST = 4
COL_NOTE = 5


class SmartTitlesDialog(QDialog):
    """Lists suggested names next to the current ones.

    The suggestion columns can be edited before applying, so a near-miss
    only needs a small correction rather than a full retype.
    """

    def __init__(
        self,
        rows: Iterable[TrackRow],
        parent=None,
        *,
        ai_settings: Optional[AISettings] = None,
        on_ai_result: Optional[Callable[[int, str, str, float, str], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Suggested titles")
        self.resize(1000, 600)
        self.rows = list(rows)
        self.ai_settings = ai_settings
        self.on_ai_result = on_ai_result
        self.accepted_values: list[tuple[int, str, str]] = []

        layout = QVBoxLayout(self)

        self.intro = QLabel(
            "These names were worked out from the upload titles. The app was "
            "not sure enough to use them by itself. Edit any name, tick the "
            "ones you want, then press Apply."
        )
        self.intro.setWordWrap(True)
        self.intro.setObjectName("muted")
        layout.addWidget(self.intro)

        self.table = QTableWidget(len(self.rows), 6)
        self.table.setHorizontalHeaderLabels(
            ["Use", "Current title", "Current artist", "New title",
             "New artist", "Why"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_USE, QHeaderView.Fixed)
        for column in (COL_CURRENT_TITLE, COL_CURRENT_ARTIST,
                       COL_TITLE, COL_ARTIST):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_NOTE, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(COL_USE, 48)

        for index, row in enumerate(self.rows):
            track = row.track
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            confidence = int(round((track.smart_confidence or 0) * 100))
            use.setCheckState(Qt.Checked if confidence >= 60 else Qt.Unchecked)
            self.table.setItem(index, COL_USE, use)

            current_title = QTableWidgetItem(track.display_title)
            current_title.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(index, COL_CURRENT_TITLE, current_title)

            current_artist = QTableWidgetItem(track.display_artist)
            current_artist.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(index, COL_CURRENT_ARTIST, current_artist)

            self.table.setItem(
                index, COL_TITLE, QTableWidgetItem(track.smart_title or "")
            )
            self.table.setItem(
                index, COL_ARTIST, QTableWidgetItem(track.smart_artist or "")
            )

            note = QTableWidgetItem(track.smart_reason or "")
            note.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(index, COL_NOTE, note)

        layout.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)

        if not self.rows:
            self.intro.setText("There are no suggestions to review for this link.")

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.apply_button = buttons.addButton(
            "Apply selected", QDialogButtonBox.AcceptRole
        )
        self.apply_button.setObjectName("primary")
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)

        self.ai_button = QPushButton("Ask AI")
        self.ai_button.setToolTip(
            "Ask the AI model to name the songs the app could not work out"
        )
        self.ai_button.clicked.connect(self._ask_ai)
        if ai_settings is None or not ai_settings.enabled:
            self.ai_button.setEnabled(False)
            self.ai_button.setToolTip(
                "Set up a model in Settings, AI, to use this"
            )
        buttons.addButton(self.ai_button, QDialogButtonBox.ActionRole)

        layout.addWidget(buttons)

    # ------------------------------------------------------------------ ai

    def _ask_ai(self) -> None:
        if self.ai_settings is None:
            return
        targets = [
            row for row in self.rows
            if row.track.smart_confidence is None
            or (row.track.smart_confidence or 0) < 0.8
        ]
        if not targets:
            self.status.setText(
                "Every song here already has a confident suggestion."
            )
            return

        progress = QProgressDialog(
            "Asking the AI…", "Stop", 0, len(targets), self
        )
        progress.setWindowTitle("Ask AI")
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModal)

        done = 0
        errors = 0
        for row in targets:
            if progress.wasCanceled():
                break
            progress.setValue(done)
            progress.setLabelText(
                f"Naming “{row.track.display_title[:50]}”…"
            )
            QApplication.processEvents()

            try:
                result = suggest_names(
                    self.ai_settings,
                    title=row.track.title,
                    channel=row.track.uploader or "",
                    description=row.track.description or "",
                )
            except AIError as exc:
                errors += 1
                self.status.setText(str(exc.args[0]) if exc.args else str(exc))
                break
            except Exception as exc:
                errors += 1
                self.status.setText(f"The AI failed: {exc}")
                break

            done += 1
            if not result.usable:
                continue

            index = self.rows.index(row)
            self.table.item(index, COL_TITLE).setText(result.title)
            if result.artist:
                self.table.item(index, COL_ARTIST).setText(result.artist)
            self.table.item(index, COL_USE).setCheckState(Qt.Checked)
            self.table.item(index, COL_NOTE).setText("AI suggestion")

            if self.on_ai_result is not None:
                self.on_ai_result(
                    row.track.id,
                    result.title,
                    result.artist,
                    result.confidence,
                    "AI suggestion",
                )

        progress.setValue(len(targets))
        if errors:
            self.status.setText(
                "Some songs could not be named. See the message above."
            )
        elif done:
            self.status.setText(
                f"AI named {done} song{'' if done == 1 else 's'}. "
                "Check them, then press Apply."
            )

    # --------------------------------------------------------------- apply

    def _apply(self) -> None:
        values: list[tuple[int, str, str]] = []
        for index, row in enumerate(self.rows):
            if self.table.item(index, COL_USE).checkState() != Qt.Checked:
                continue
            title = self.table.item(index, COL_TITLE).text().strip()
            artist = self.table.item(index, COL_ARTIST).text().strip()
            if not title:
                continue
            values.append((row.track.id, title, artist))

        if not values:
            QMessageBox.information(
                self,
                "Nothing selected",
                "Tick at least one song, and make sure it has a title.",
            )
            return

        self.accepted_values = values
        self.accept()
