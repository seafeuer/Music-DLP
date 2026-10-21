"""Review, edit, and AI-fill suggested song titles.

Nothing is written to the library until Apply is pressed. AI results live
only in this dialog, so Cancel really does cancel.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QMessageBox, QProgressDialog, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from musicdlp.core.models import TrackRow
from musicdlp.services.ai import AIError, AISettings, friendly_error, suggest_names

COL_USE = 0
COL_CURRENT_TITLE = 1
COL_CURRENT_ARTIST = 2
COL_TITLE = 3
COL_ARTIST = 4
COL_NOTE = 5

AUTO_TICK = 0.6
CONFIDENT = 0.8


@dataclass
class Suggestion:
    """One row's editable suggestion, held in memory until Apply."""

    track_id: int
    title: str
    artist: str
    source: str          # "rules", "AI" or "chatbot"
    reason: str


class SmartTitlesDialog(QDialog):
    """Lists suggested names next to the current ones."""

    def __init__(
        self,
        rows: Iterable[TrackRow],
        parent=None,
        *,
        ai_settings: Optional[AISettings] = None,
        on_setup_ai: Optional[Callable[[], Optional[AISettings]]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Suggested titles")
        self.resize(1040, 620)
        self.rows = list(rows)
        self.ai_settings = ai_settings
        self.on_setup_ai = on_setup_ai
        self.accepted_values: list[tuple[int, str, str]] = []

        self._results: queue.Queue = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pending: list[TrackRow] = []

        layout = QVBoxLayout(self)

        self.intro = QLabel(
            "These names were worked out from the upload titles. The app was "
            "not sure enough to use them by itself. Edit any name, tick the "
            "ones you want, then press Apply. Nothing is saved until you do."
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
        header.setSectionResizeMode(COL_NOTE, QHeaderView.Stretch)
        self.table.setColumnWidth(COL_USE, 44)

        self._row_by_id: dict[int, int] = {}
        for index, row in enumerate(self.rows):
            self._row_by_id[row.track.id] = index
            self._build_row(index, row)

        layout.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)

        # Kept separate so a later summary cannot hide the actual error.
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #f87171;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        if not self.rows:
            self.intro.setText("There are no suggestions to review for this link.")

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.apply_button = buttons.addButton(
            "Apply selected", QDialogButtonBox.AcceptRole
        )
        self.apply_button.setObjectName("primary")
        self.apply_button.setEnabled(bool(self.rows))
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)

        self.ai_button = QPushButton()
        self.ai_button.clicked.connect(self._on_ai_clicked)
        buttons.addButton(self.ai_button, QDialogButtonBox.ActionRole)
        self._update_ai_button()

        layout.addWidget(buttons)

    # ------------------------------------------------------------------ rows

    def _build_row(self, index: int, row: TrackRow) -> None:
        track = row.track
        use = QTableWidgetItem()
        use.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        confidence = track.smart_confidence or 0.0
        use.setCheckState(
            Qt.Checked if confidence >= AUTO_TICK else Qt.Unchecked
        )
        self.table.setItem(index, COL_USE, use)

        current_title = QTableWidgetItem(track.display_title)
        current_title.setFlags(Qt.ItemIsEnabled)
        self.table.setItem(index, COL_CURRENT_TITLE, current_title)

        current_artist = QTableWidgetItem(
            track.meta_artist or f"{track.uploader or 'Unknown'} (channel)"
        )
        current_artist.setFlags(Qt.ItemIsEnabled)
        current_artist.setToolTip(
            "What would be written now. “(channel)” means the channel name "
            "is used because no artist has been set."
        )
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

    def _update_ai_button(self) -> None:
        configured = self.ai_settings is not None and self.ai_settings.enabled
        if configured:
            self.ai_button.setText("Ask AI")
            self.ai_button.setEnabled(bool(self.rows))
            self.ai_button.setToolTip(
                "Ask the AI model to name the songs the app could not work out"
            )
        else:
            self.ai_button.setText("Set up AI…")
            self.ai_button.setEnabled(self.on_setup_ai is not None)
            self.ai_button.setToolTip(
                "Choose a model in Settings, AI, to have songs named for you"
            )

    # ------------------------------------------------------------------ ai

    def _on_ai_clicked(self) -> None:
        configured = self.ai_settings is not None and self.ai_settings.enabled
        if not configured:
            if self.on_setup_ai is None:
                return
            # Settings was opened from here; carry on with whatever was
            # saved, since the user already asked for AI naming.
            updated = self.on_setup_ai()
            if updated is not None:
                self.ai_settings = updated
            self._update_ai_button()
            if self.ai_settings is None or not self.ai_settings.enabled:
                return
        self._ask_ai()

    def _ai_targets(self) -> list[TrackRow]:
        """Songs the rules could not name confidently."""
        return [
            row for row in self.rows
            if (row.track.smart_confidence or 0) < CONFIDENT
        ]

    def _ask_ai(self) -> None:
        if self.ai_settings is None:
            return
        targets = self._ai_targets()
        if not targets:
            self.status.setText(
                "Every song here already has a confident name."
            )
            return

        self._pending = targets
        self._stop.clear()
        self.error_label.hide()
        self.ai_button.setEnabled(False)
        self.status.setText(f"Asking the AI about {len(targets)} song(s)…")

        settings = self.ai_settings
        results = self._results
        stop = self._stop

        def work() -> None:
            for row in targets:
                if stop.is_set():
                    break
                try:
                    result = suggest_names(
                        settings,
                        title=row.track.title,
                        channel=row.track.uploader or "",
                        description=row.track.description or "",
                    )
                except AIError as exc:
                    results.put(("error", friendly_error(exc)))
                    break
                except Exception as exc:  # noqa: BLE001 - surfaced to the user
                    results.put(("error", ("The AI failed.", str(exc))))
                    break
                results.put(("result", (row.track.id, result)))
            results.put(("done", None))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

        self._poll = QTimer(self)
        self._poll.setInterval(150)
        self._poll.timeout.connect(self._drain)
        self._poll.start()

    def _drain(self) -> None:
        finished = False
        named = 0
        while True:
            try:
                kind, payload = self._results.get_nowait()
            except queue.Empty:
                break

            if kind == "done":
                finished = True
                continue
            if kind == "error":
                message, hint = payload
                self.error_label.setText(
                    f"{message}\n{hint}" if hint else message
                )
                self.error_label.show()
                finished = True
                continue
            if kind == "result":
                track_id, result = payload
                if self._apply_ai_result(track_id, result):
                    named += 1

        if not finished:
            return

        if getattr(self, "_poll", None) is not None:
            self._poll.stop()
        self.ai_button.setEnabled(True)

        if self.error_label.isVisible():
            self.status.setText(
                "The AI stopped early. The names it did return are below."
                if named else "The AI could not name these songs."
            )
        elif named:
            self.status.setText(
                f"AI named {named} song{'' if named == 1 else 's'}. "
                "Check them, then press Apply."
            )
        else:
            self.status.setText(
                "The AI did not offer a name for these songs. "
                "Try a different model, or name them by hand."
            )

    def _apply_ai_result(self, track_id: int, result) -> bool:
        index = self._row_by_id.get(track_id)
        if index is None:
            return False
        row = self.rows[index]

        # Never replace a name the rules were already confident about.
        if (row.track.smart_confidence or 0) >= CONFIDENT:
            return False
        if not result.usable:
            return False

        self.table.item(index, COL_TITLE).setText(result.title)
        if result.artist:
            self.table.item(index, COL_ARTIST).setText(result.artist)

        confidence = int(round((result.confidence or 0) * 100))
        self.table.item(index, COL_USE).setCheckState(
            Qt.Checked if (result.confidence or 0) >= AUTO_TICK
            else Qt.Unchecked
        )
        self.table.item(index, COL_NOTE).setText(
            f"AI suggestion ({confidence}% sure)"
        )
        return True

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

    # --------------------------------------------------------------- close

    def reject(self) -> None:
        self._stop.set()
        super().reject()

    def closeEvent(self, event) -> None:
        self._stop.set()
        super().closeEvent(event)
