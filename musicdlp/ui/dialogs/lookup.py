"""Fill in album, artist, and track number from a free catalogue.

No AI and no account: MusicBrainz is asked what release each song belongs
to. The results are shown for review, and nothing is saved until Apply.
"""

from __future__ import annotations

import queue
import threading
from typing import Iterable, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHeaderView,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from musicdlp.core.models import TrackRow
from musicdlp.services.metadata_lookup import Lookup, lookup

COL_USE = 0
COL_TITLE = 1
COL_CURRENT_ALBUM = 2
COL_ALBUM = 3
COL_TRACK = 4
COL_ARTIST = 5
COL_NOTE = 6


class LookupDialog(QDialog):
    """Look up album details for the songs listed, then apply them."""

    def __init__(self, rows: Iterable[TrackRow], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Look up details online")
        self.resize(1060, 620)

        self.rows = list(rows)
        self.accepted_values: list[tuple[int, str, str, Optional[int]]] = []
        self._row_by_id = {
            row.track.id: index for index, row in enumerate(self.rows)
        }

        self._results: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._poll: Optional[QTimer] = None
        self._found_count = 0

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "Looks each song up in MusicBrainz, a free public catalogue, to "
            "find the album it belongs to and where it sits on that album. "
            "This is not AI and needs no account. Nothing is saved until you "
            "press Apply."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        self.artist_check = QCheckBox("Also fill in artist names")
        self.artist_check.setToolTip(
            "Only fills artists that are blank, or that match the channel name"
        )
        layout.addWidget(self.artist_check)

        self.table = QTableWidget(len(self.rows), 7)
        self.table.setHorizontalHeaderLabels(
            ["Use", "Song", "Current album", "New album", "Track",
             "New artist", "Note"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_USE, QHeaderView.Fixed)
        for column in (COL_TITLE, COL_CURRENT_ALBUM, COL_ALBUM):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        for column in (COL_TRACK, COL_ARTIST, COL_NOTE):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(COL_USE, 44)
        self.table.itemChanged.connect(lambda _item: self._update_apply_state())

        for index, row in enumerate(self.rows):
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

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.apply_button = buttons.addButton(
            "Apply selected", QDialogButtonBox.AcceptRole
        )
        self.apply_button.setObjectName("primary")
        self.apply_button.setEnabled(False)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)

        self.go_button = QPushButton("Look up")
        self.go_button.setDefault(True)
        self.go_button.clicked.connect(self._start)
        buttons.addButton(self.go_button, QDialogButtonBox.ActionRole)

        layout.addWidget(buttons)

    # ------------------------------------------------------------------ rows

    def _build_row(self, index: int, row: TrackRow) -> None:
        track = row.track

        use = QTableWidgetItem()
        use.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        use.setCheckState(Qt.Unchecked)
        self.table.setItem(index, COL_USE, use)

        title = QTableWidgetItem(track.display_title)
        title.setFlags(Qt.ItemIsEnabled)
        title.setToolTip(track.url)
        self.table.setItem(index, COL_TITLE, title)

        current = QTableWidgetItem(track.display_album or "—")
        current.setFlags(Qt.ItemIsEnabled)
        self.table.setItem(index, COL_CURRENT_ALBUM, current)

        for column in (COL_ALBUM, COL_ARTIST, COL_NOTE):
            item = QTableWidgetItem("")
            item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(index, column, item)

        track_item = QTableWidgetItem("")
        track_item.setFlags(Qt.ItemIsEnabled)
        self.table.setItem(index, COL_TRACK, track_item)

    # ---------------------------------------------------------------- lookup

    def _start(self) -> None:
        if not self.rows:
            return
        self._stop.clear()
        self._found_count = 0
        self.error_label.hide()
        self.go_button.setEnabled(False)
        self.status.setText(f"Looking up {len(self.rows)} song(s)…")

        items = [
            (row.track.id, row.track.display_title,
             row.track.display_artist, row.track.display_album)
            for row in self.rows
        ]
        results = self._results
        stop = self._stop

        def work() -> None:
            for track_id, title, artist, album in items:
                if stop.is_set():
                    break
                try:
                    found = lookup(artist, title, album=album)
                except Exception as exc:  # noqa: BLE001 - reported in the UI
                    results.put(("error", str(exc)))
                    break
                results.put(("result", (track_id, found)))
            results.put(("done", None))

        threading.Thread(target=work, daemon=True).start()

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
                self.error_label.setText(f"The lookup stopped: {payload}")
                self.error_label.show()
                continue
            if kind == "result":
                track_id, found = payload
                if self._apply_lookup(track_id, found):
                    # Counted across polls: results arrive one at a time.
                    self._found_count += 1

        if not finished:
            return

        if self._poll is not None:
            self._poll.stop()
        self.go_button.setEnabled(True)
        self._update_apply_state()
        found_count = self._found_count
        if found_count:
            self.status.setText(
                f"Found details for {found_count} song"
                f"{'' if found_count == 1 else 's'}. Check them, then press "
                "Apply."
            )
        elif not self.error_label.isVisible():
            self.status.setText(
                "Nothing was found. The catalogue may not have these songs."
            )

    def _apply_lookup(self, track_id: int, found: Optional[Lookup]) -> bool:
        index = self._row_by_id.get(track_id)
        if index is None:
            return False
        if found is None or not found.usable:
            self.table.item(index, COL_NOTE).setText("Not found")
            return False

        self.table.item(index, COL_ALBUM).setText(found.album)
        if found.track is not None:
            self.table.item(index, COL_TRACK).setText(str(found.track))

        # Only offer an artist when the song has none of its own; otherwise
        # the catalogue's credit would overwrite a deliberate choice.
        row = self.rows[index]
        track = row.track
        has_artist = bool((track.meta_artist or "").strip())
        if self.artist_check.isChecked() and not has_artist:
            self.table.item(index, COL_ARTIST).setText(found.artist)
        else:
            self.table.item(index, COL_ARTIST).setText("")

        note = "Found"
        if found.year:
            note = f"Found ({found.year})"
        self.table.item(index, COL_NOTE).setText(note)
        self.table.item(index, COL_USE).setCheckState(Qt.Checked)
        self._update_apply_state()
        return True

    # ---------------------------------------------------------------- apply

    def _update_apply_state(self) -> None:
        if getattr(self, "apply_button", None) is None:
            return
        self.apply_button.setEnabled(any(
            self.table.item(index, COL_USE).checkState() == Qt.Checked
            for index in range(len(self.rows))
        ))

    def _apply(self) -> None:
        values: list[tuple[int, str, str, Optional[int]]] = []
        for index, row in enumerate(self.rows):
            if self.table.item(index, COL_USE).checkState() != Qt.Checked:
                continue
            album = self.table.item(index, COL_ALBUM).text().strip()
            artist = self.table.item(index, COL_ARTIST).text().strip()
            raw_track = self.table.item(index, COL_TRACK).text().strip()
            try:
                number = int(raw_track) if raw_track else None
            except ValueError:
                number = None
            values.append((row.track.id, album, artist, number))

        if not values:
            QMessageBox.information(
                self,
                "Nothing selected",
                "Tick at least one song to apply its details.",
            )
            return

        self.accepted_values = values
        self.accept()

    # ---------------------------------------------------------------- close

    def accept(self) -> None:
        # Apply can close the dialog while lookups are still queued.
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
