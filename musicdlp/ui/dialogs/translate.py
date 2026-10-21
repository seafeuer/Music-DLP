"""Review and apply translations of song titles and artist names.

Like the suggested-titles dialog, nothing is written until Apply. The model
runs in a background thread, and each result lands in the table as it
arrives, so a slow or failing model never freezes the window.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from musicdlp.core.models import TrackRow
from musicdlp.services.ai import AIError, AISettings, friendly_error
from musicdlp.services.translate import (
    LANGUAGES, STYLE_BOTH, STYLE_LABELS, STYLE_TRANSLATED,
    STYLE_TRANSLITERATED, Translation, combine, translate_many,
)

COL_USE = 0
COL_CURRENT_TITLE = 1
COL_CURRENT_ARTIST = 2
COL_TITLE = 3
COL_ARTIST = 4
COL_NOTE = 5

CONFIDENT = 0.6


class TranslationDialog(QDialog):
    """Translate one or many songs into a chosen language."""

    def __init__(
        self,
        rows: Iterable[TrackRow],
        parent=None,
        *,
        ai_settings: Optional[AISettings] = None,
        language: str = "Japanese",
        style: str = STYLE_BOTH,
        translate_artist: bool = True,
        on_setup_ai: Optional[Callable[[], Optional[AISettings]]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Translate titles")
        self.resize(1080, 620)

        self.rows = list(rows)
        self.ai_settings = ai_settings
        self.on_setup_ai = on_setup_ai
        self.accepted_values: list[tuple[int, str, str]] = []

        self._translations: dict[int, Translation] = {}
        self._rendered: dict[int, tuple[str, str]] = {}
        # Counted across polls: results land a few at a time, so a per-poll
        # counter would report only the last handful.
        self._translated_count = 0
        self._results: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._poll: Optional[QTimer] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "Translate song titles and artist names into another language. "
            "This is separate from renaming: it takes the name each song "
            "already has and gives you the same song in Japanese, Hebrew, or "
            "another language. Nothing is saved until you press Apply."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        layout.addWidget(self._build_options())

        self.table = QTableWidget(len(self.rows), 6)
        self.table.setHorizontalHeaderLabels(
            ["Use", "Current title", "Current artist", "New title",
             "New artist", "Note"]
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
        header.setMinimumSectionSize(60)
        self.table.setColumnWidth(COL_USE, 44)
        # Ticking a row by hand must enable Apply as well.
        self.table.itemChanged.connect(lambda _item: self._update_apply_state())

        self._row_by_id: dict[int, int] = {}
        for index, row in enumerate(self.rows):
            self._row_by_id[row.track.id] = index
            self._build_row(index, row)

        layout.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        layout.addWidget(self.status)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #f87171;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        if not self.rows:
            intro.setText("There are no songs to translate here.")

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.apply_button = buttons.addButton(
            "Apply selected", QDialogButtonBox.AcceptRole
        )
        self.apply_button.setObjectName("primary")
        self.apply_button.setEnabled(False)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)

        self.go_button = QPushButton("Translate")
        self.go_button.setDefault(True)
        self.go_button.clicked.connect(self._on_translate_clicked)
        buttons.addButton(self.go_button, QDialogButtonBox.ActionRole)

        layout.addWidget(buttons)

        self._update_go_button()
        self._update_apply_state()
        self.set_options(language, style, translate_artist)

    # --------------------------------------------------------------- options

    def _build_options(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        row.addWidget(QLabel("Translate into"))
        self.language_combo = QComboBox()
        self.language_combo.setEditable(True)
        self.language_combo.addItems(list(LANGUAGES))
        self.language_combo.setCurrentText("Japanese")
        self.language_combo.setMinimumWidth(170)
        self.language_combo.setToolTip(
            "Pick a language, or type any language name"
        )
        row.addWidget(self.language_combo)

        row.addWidget(QLabel("Written as"))
        self.style_combo = QComboBox()
        for value in (STYLE_BOTH, STYLE_TRANSLATED, STYLE_TRANSLITERATED):
            self.style_combo.addItem(STYLE_LABELS[value], value)
        self.style_combo.setToolTip(
            "Japanese, Hebrew, and similar languages have two useful forms: "
            "the native script (夜に駆ける), the same sounds in Latin letters "
            "(Yoru ni Kakeru), or both together."
        )
        row.addWidget(self.style_combo, 1)

        self.artist_check = QCheckBox("Translate artist names too")
        self.artist_check.setChecked(True)
        self.artist_check.setToolTip(
            "Leave this off to keep artist names exactly as they are"
        )
        row.addWidget(self.artist_check)

        self.style_combo.currentIndexChanged.connect(self._restyle_rows)
        self.artist_check.toggled.connect(self._restyle_rows)
        return box

    def set_options(self, language: str, style: str, translate_artist: bool) -> None:
        """Apply remembered choices without emitting a translation."""
        self.language_combo.setCurrentText(language or "Japanese")
        index = self.style_combo.findData(style)
        if index >= 0:
            self.style_combo.setCurrentIndex(index)
        self.artist_check.setChecked(bool(translate_artist))

    def _style(self) -> str:
        return self.style_combo.currentData() or STYLE_BOTH

    def _language(self) -> str:
        return (self.language_combo.currentText() or "").strip() or "Japanese"

    # ------------------------------------------------------------------ rows

    def _build_row(self, index: int, row: TrackRow) -> None:
        track = row.track

        use = QTableWidgetItem()
        use.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        use.setCheckState(Qt.Unchecked)
        self.table.setItem(index, COL_USE, use)

        current_title = QTableWidgetItem(track.display_title)
        current_title.setFlags(Qt.ItemIsEnabled)
        self.table.setItem(index, COL_CURRENT_TITLE, current_title)

        current_artist = QTableWidgetItem(track.display_artist)
        current_artist.setFlags(Qt.ItemIsEnabled)
        self.table.setItem(index, COL_CURRENT_ARTIST, current_artist)

        self.table.setItem(index, COL_TITLE, QTableWidgetItem(""))
        self.table.setItem(index, COL_ARTIST, QTableWidgetItem(""))

        note = QTableWidgetItem("Not yet")
        note.setFlags(Qt.ItemIsEnabled)
        self.table.setItem(index, COL_NOTE, note)

    def _restyle_rows(self, *_args) -> None:
        """Re-render rows whose text the user has not edited by hand."""
        style = self._style()
        for track_id, translation in self._translations.items():
            index = self._row_by_id.get(track_id)
            if index is None:
                continue
            rendered = self._rendered.get(track_id)
            if rendered is None:
                continue
            title_item = self.table.item(index, COL_TITLE)
            artist_item = self.table.item(index, COL_ARTIST)
            if (
                title_item.text() != rendered[0]
                or artist_item.text() != rendered[1]
            ):
                continue  # edited by hand; leave it alone
            self._fill_row(index, translation, style)

    def _fill_row(self, index: int, translation: Translation, style: str) -> None:
        title = combine(translation.title, translation.title_latin, style)
        artist = ""
        if self.artist_check.isChecked():
            artist = combine(
                translation.artist, translation.artist_latin, style
            )
        self.table.item(index, COL_TITLE).setText(title)
        self.table.item(index, COL_ARTIST).setText(artist)
        self._rendered[self.rows[index].track.id] = (title, artist)

    # ------------------------------------------------------------- translate

    def _update_go_button(self) -> None:
        configured = self.ai_settings is not None and self.ai_settings.enabled
        if configured:
            self.go_button.setText("Translate")
            self.go_button.setEnabled(bool(self.rows))
            self.go_button.setToolTip(
                "Ask the AI model to translate the songs listed here"
            )
        else:
            self.go_button.setText("Set up AI…")
            self.go_button.setEnabled(self.on_setup_ai is not None)
            self.go_button.setToolTip(
                "Choose a model in Settings, AI, before translating"
            )

    def _on_translate_clicked(self) -> None:
        configured = self.ai_settings is not None and self.ai_settings.enabled
        if not configured:
            if self.on_setup_ai is None:
                return
            # Settings was opened from here; pick up whatever was saved and
            # carry on, since the user already asked for a translation.
            updated = self.on_setup_ai()
            if updated is not None:
                self.ai_settings = updated
            self._update_go_button()
            configured = (
                self.ai_settings is not None and self.ai_settings.enabled
            )
            if not configured:
                return
        self._start()

    def _start(self) -> None:
        if self.ai_settings is None:
            return
        items = [
            (row.track.id, row.track.display_title, row.track.display_artist)
            for row in self.rows
        ]
        if not items:
            return

        self._stop.clear()
        self._translated_count = 0
        self.error_label.hide()
        self.go_button.setEnabled(False)
        self.status.setText(f"Translating {len(items)} song(s)…")

        settings = self.ai_settings
        language = self._language()
        translate_artist = self.artist_check.isChecked()
        results = self._results
        stop = self._stop

        def work() -> None:
            def on_result(track_id: int, translation: Translation) -> None:
                results.put(("result", (track_id, translation)))

            try:
                _done, error = translate_many(
                    settings,
                    items,
                    language=language,
                    translate_artist=translate_artist,
                    on_result=on_result,
                    should_stop=stop.is_set,
                )
            except Exception as exc:  # noqa: BLE001 - shown to the user
                results.put(("error", ("The translation failed.", str(exc))))
            else:
                if error is not None:
                    results.put(("error", friendly_error(error)))
            results.put(("done", None))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

        self._poll = QTimer(self)
        self._poll.setInterval(150)
        self._poll.timeout.connect(self._drain)
        self._poll.start()

    def _drain(self) -> None:
        finished = False
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
                continue
            if kind == "result":
                track_id, translation = payload
                self._apply_translation(track_id, translation)

        if not finished:
            return

        if self._poll is not None:
            self._poll.stop()
        self.go_button.setEnabled(True)
        self._update_apply_state()

        added = self._translated_count
        if added:
            self.status.setText(
                f"Translated {added} song{'' if added == 1 else 's'}. "
                "Check the names, then press Apply."
            )
        elif not self.error_label.isVisible():
            self.status.setText(
                "Nothing was translated. Try a different language or model."
            )

    def _apply_translation(self, track_id: int, translation: Translation) -> bool:
        index = self._row_by_id.get(track_id)
        if index is None:
            return False

        self._translations[track_id] = translation
        self._translated_count += 1
        self._fill_row(index, translation, self._style())

        language = self._language()
        if translation.already_target:
            self.table.item(index, COL_USE).setCheckState(Qt.Unchecked)
            note = "Already there"
            tooltip = f"This song is already in {language}, so it is left as it is."
        elif translation.confidence >= CONFIDENT:
            self.table.item(index, COL_USE).setCheckState(Qt.Checked)
            note = f"{int(round(translation.confidence * 100))}% sure"
            tooltip = "Translated. Untick it if the name is wrong."
        else:
            self.table.item(index, COL_USE).setCheckState(Qt.Unchecked)
            note = f"Unsure {int(round(translation.confidence * 100))}%"
            tooltip = (
                "The model was not sure. Check the name, then tick the box "
                "if it is right."
            )

        item = self.table.item(index, COL_NOTE)
        item.setText(note)
        item.setToolTip(tooltip)
        self._update_apply_state()
        return True

    # --------------------------------------------------------------- apply

    def _update_apply_state(self) -> None:
        # Rows are built before the buttons exist, and itemChanged fires
        # during that build.
        if getattr(self, "apply_button", None) is None:
            return
        self.apply_button.setEnabled(any(
            self.table.item(index, COL_USE).checkState() == Qt.Checked
            for index in range(len(self.rows))
        ))

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

    def accept(self) -> None:
        # Apply closes the dialog while a run may still be going; stop it so
        # no further requests are sent on behalf of a closed window.
        self._stop.set()
        if self._poll is not None:
            self._poll.stop()
        super().accept()

    def reject(self) -> None:
        self._stop.set()
        if self._poll is not None:
            self._poll.stop()
        super().reject()

    def closeEvent(self, event) -> None:
        self._stop.set()
        if self._poll is not None:
            self._poll.stop()
        super().closeEvent(event)
