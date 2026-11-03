"""The central track table: header, filters, virtualized rows, selection."""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import QMimeData, QModelIndex, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QPushButton, QStackedWidget, QTableView, QToolButton,
    QVBoxLayout, QWidget,
)

from musicdlp.core.models import ERROR, NEW, Source, Track, TrackRow
from musicdlp.ui.delegates import ProgressDelegate, StatusDelegate
from musicdlp.ui.widgets.elided_label import ElidedLabel
from musicdlp.ui.widgets.search_box import SearchBox
from musicdlp.ui.models.track_model import (
    COL_ARTIST, COL_CHECK, COL_DURATION, COL_FORMAT, COL_POSITION,
    COL_PROGRESS, COL_STATUS, COL_TITLE, ROW_ROLE, TrackFilterProxy,
    TrackTableModel,
)


class TrackTableView(QTableView):
    """Table view that lets selected rows be dragged into a library folder."""

    remove_pressed = Signal()
    toggle_pressed = Signal(int)

    def keyPressEvent(self, event) -> None:
        """Keyboard behaviour a list of songs is expected to have.

        Delete removes the highlighted songs, and Space ticks or unticks
        them — the two things a user reaches for without looking for a
        button. Enter is left to the table, which opens the details dialog
        through doubleClicked/activated handling in the parent widget.
        """
        key = event.key()
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.selected_track_ids():
                self.remove_pressed.emit()
                event.accept()
                return
        if key == Qt.Key_Space:
            index = self.currentIndex()
            if index.isValid():
                model = self.model()
                source_index = model.mapToSource(index)
                row = model.sourceModel().rows[source_index.row()]
                self.toggle_pressed.emit(row.track.id)
                event.accept()
                return
        super().keyPressEvent(event)

    def selected_track_ids(self) -> list[int]:
        model = self.model()
        if model is None:
            return []
        ids: list[int] = []
        for index in self.selectionModel().selectedRows():
            source_index = model.mapToSource(index)
            row = model.sourceModel().rows[source_index.row()]
            ids.append(row.track.id)
        return ids

    def build_drag_mime(self) -> Optional[QMimeData]:
        """The payload for dragging the selected songs.

        Kept separate from startDrag so it can be tested without driving a
        real drag-and-drop session.
        """
        from musicdlp.ui.widgets.source_list import TRACKS_MIME

        model = self.model()
        ids = self.selected_track_ids()
        if not ids or model is None:
            return None

        payload = QMimeData()
        payload.setData(
            TRACKS_MIME, ",".join(str(i) for i in ids).encode("ascii")
        )
        titles = [
            model.sourceModel().rows[model.mapToSource(index).row()].display_title
            for index in self.selectionModel().selectedRows()[:5]
        ]
        payload.setText(", ".join(titles))
        return payload

    def startDrag(self, supported_actions) -> None:
        payload = self.build_drag_mime()
        if payload is None:
            return
        drag = QDrag(self)
        drag.setMimeData(payload)
        drag.exec(Qt.MoveAction)


class TrackTableWidget(QWidget):
    checked_changed = Signal()
    edit_details_requested = Signal(int)
    suggestions_requested = Signal()
    retry_requested = Signal(int)
    reconvert_requested = Signal(int)
    remove_requested = Signal(int)
    remove_selected_requested = Signal(list)
    open_source_requested = Signal(str)
    clear_search_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.source: Optional[Source] = None

        self.model = TrackTableModel(self)
        self.proxy = TrackFilterProxy(self)
        self.proxy.setSourceModel(self.model)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Header -----------------------------------------------------------
        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.title_label = ElidedLabel("Your library")
        self.title_label.setStyleSheet("font-size: 17px; font-weight: 700;")
        self.subtitle_label = ElidedLabel(
            "Paste a link in the bar above and press Add"
        )
        self.subtitle_label.setObjectName("muted")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.subtitle_label)
        header_row.addLayout(title_box, 1)
        layout.addLayout(header_row)

        # Filter row -------------------------------------------------------
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        self.filter_combo = QComboBox()
        self.filter_combo.setToolTip("Show only songs in a particular state")
        for label, value in [
            ("All songs", "all"),
            ("Not downloaded", "new"),
            ("Downloaded", "downloaded"),
            ("Converted", "converted"),
            ("Needs update", "needs_update"),
            ("Problems", "errors"),
        ]:
            self.filter_combo.addItem(label, value)
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.filter_combo)

        self.search = SearchBox("Filter these songs…")
        self.search.setMinimumWidth(150)
        self.search.setToolTip("Hide every song whose title or artist does not match")
        self.search.textChanged.connect(self._on_search_changed)
        filter_row.addWidget(self.search, 1)

        self.edit_button = QPushButton("Edit details…")
        self.edit_button.setToolTip(
            "Change the title, artist, album, track number, or picture "
            "for the highlighted song (F2)"
        )
        self.edit_button.clicked.connect(self._edit_current)
        self.edit_button.setEnabled(False)
        filter_row.addWidget(self.edit_button)

        self.titles_button = QPushButton("Suggested titles…")
        self.titles_button.setToolTip(
            "Review song and artist names worked out from the upload titles"
        )
        self.titles_button.clicked.connect(self.suggestions_requested)
        filter_row.addWidget(self.titles_button)

        # Selection lives in one menu so the row stays readable when the
        # window is narrow.
        self.select_button = QToolButton()
        self.select_button.setText("Select")
        self.select_button.setPopupMode(QToolButton.InstantPopup)
        self.select_button.setToolTip("Tick songs by their state")
        select_menu = QMenu(self.select_button)
        select_menu.addAction("All songs", self.model.check_all)
        select_menu.addAction("Not downloaded", lambda: self.model.check_status(NEW))
        select_menu.addAction("Needs attention", self.model.check_problems)
        select_menu.addSeparator()
        select_menu.addAction("Clear selection", self.model.clear_checks)
        self.select_button.setMenu(select_menu)
        filter_row.addWidget(self.select_button)

        # A single menu for the same actions when there is no room for them.
        self.actions_button = QToolButton()
        self.actions_button.setText("⋯")
        self.actions_button.setMinimumWidth(34)
        self.actions_button.setPopupMode(QToolButton.InstantPopup)
        self.actions_button.setToolTip("More actions for the song list")
        actions_menu = QMenu(self.actions_button)
        self._edit_action = actions_menu.addAction(
            "Edit details…", self._edit_current
        )
        self._suggestions_action = actions_menu.addAction(
            # A bound signal is not callable, so the menu entry must call
            # emit itself; passing the signal raised "not callable" and the
            # dialog never opened.
            "Suggested titles…", self.suggestions_requested.emit
        )
        actions_menu.addSeparator()
        actions_menu.addAction("Select all", self.model.check_all)
        actions_menu.addAction(
            "Select not downloaded", lambda: self.model.check_status(NEW)
        )
        actions_menu.addAction(
            "Select needs attention", self.model.check_problems
        )
        actions_menu.addAction("Clear selection", self.model.clear_checks)
        self.actions_button.setMenu(actions_menu)
        self.actions_button.setAccessibleName("More actions")
        filter_row.addWidget(self.actions_button)

        self.filter_bar = QWidget()
        self.filter_bar.setLayout(filter_row)
        layout.addWidget(self.filter_bar)
        self.filter_bar.hide()

        # Table ------------------------------------------------------------
        self.stack = QStackedWidget()
        self.table = TrackTableView()
        self.mode = "easy"
        self.table.setModel(self.proxy)
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragOnly)
        self.table.setDefaultDropAction(Qt.MoveAction)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COL_POSITION, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setItemDelegateForColumn(COL_STATUS, StatusDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_PROGRESS, ProgressDelegate(self.table))
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.remove_pressed.connect(self._remove_selected)
        self.table.toggle_pressed.connect(self._toggle_one)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.setAccessibleName("Songs")

        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSectionResizeMode(COL_CHECK, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_POSITION, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_ARTIST, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_DURATION, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_FORMAT, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_PROGRESS, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_CHECK, 32)
        self.table.setColumnWidth(COL_POSITION, 38)
        self.table.setColumnWidth(COL_ARTIST, 150)
        self.table.setColumnWidth(COL_DURATION, 58)
        self.table.setColumnWidth(COL_FORMAT, 76)
        self.table.setColumnWidth(COL_STATUS, 136)
        self.table.setColumnWidth(COL_PROGRESS, 92)

        self.stack.addWidget(self.table)
        self.empty_label = QLabel("Nothing here yet.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setObjectName("muted")
        self.empty_label.setTextFormat(Qt.RichText)
        self.empty_label.setWordWrap(True)
        self.empty_label.setOpenExternalLinks(False)
        self.empty_label.linkActivated.connect(self._on_empty_link)
        self.stack.addWidget(self.empty_label)
        layout.addWidget(self.stack, 1)

        self.model.check_changed.connect(self._on_checks_changed)
        self.model.check_toggled.connect(self._select_by_track_id)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._apply_column_visibility()

    # ------------------------------------------------------------------ data

    def set_source(
        self,
        source: Optional[Source],
        rows: Iterable[TrackRow],
        checked: Optional[Iterable[int]] = None,
        selected_track_id: Optional[int] = None,
    ) -> None:
        self.source = source
        self.search.clear()
        self.filter_combo.setCurrentIndex(0)
        # "All songs" and a folder gather songs from several links, where
        # each link's own position would repeat (1, 1, 2...). Those views
        # number the rows instead. A single link keeps its own positions.
        self.model.numbered_rows = bool(
            source is not None and (source.is_all or source.is_folder)
        )
        self.model.set_rows(rows, checked)
        self.table.sortByColumn(COL_POSITION, Qt.AscendingOrder)

        if source is None:
            self.title_label.setText("Your library")
            self.subtitle_label.setText(
                "Paste a link in the bar above and press Add"
            )
        else:
            self.title_label.setText(source.title)
            count = len(self.model.rows)
            songs = f"{count} song{'' if count == 1 else 's'}"
            if source.is_folder:
                self.subtitle_label.setText(f"Folder · {songs}")
            elif source.is_all:
                self.subtitle_label.setText(
                    f"Everything you have · {songs}"
                )
            else:
                uploader = source.uploader or "Unknown artist"
                self.subtitle_label.setText(f"{uploader} · {songs}")

        # Keep the highlighted song across a refresh, so the details panel
        # does not lose the song the user was working on.
        if selected_track_id is not None:
            self._select_by_track_id(selected_track_id)

        self.filter_bar.setVisible(source is not None)
        self._update_counts()
        self._update_empty_state()
        self.refresh_progress_column()
        self.edit_button.setEnabled(self.current_row() is not None)

    def _select_by_track_id(self, track_id: int) -> None:
        row = self.model.row_index_for_id(track_id)
        if row is None:
            return
        source_index = self.model.index(row, COL_TITLE)
        proxy_index = self.proxy.mapFromSource(source_index)
        if proxy_index.isValid():
            self.table.setCurrentIndex(proxy_index)
            self.table.scrollTo(
                proxy_index, QAbstractItemView.PositionAtCenter
            )

    def update_track(self, track: Track) -> None:
        self.model.replace_track(track)
        self._update_counts()
        self.refresh_progress_column()

    def set_progress(self, track_id: int, percent: float) -> None:
        self.model.set_progress(track_id, percent)
        self.refresh_progress_column()

    def clear_progress(self) -> None:
        self.model.clear_progress()
        self.refresh_progress_column()

    def checked_rows(self) -> list[TrackRow]:
        return self.model.checked_rows()

    def checked_count(self) -> int:
        return len(self.model.checked)

    def current_row(self) -> Optional[TrackRow]:
        index = self.table.currentIndex()
        if not index.isValid():
            return None
        source_index = self.proxy.mapToSource(index)
        row = source_index.row()
        if 0 <= row < len(self.model.rows):
            return self.model.rows[row]
        return None

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._apply_column_visibility()

    def _apply_column_visibility(self) -> None:
        """Hide optional columns when there is not enough width for them.

        This keeps the status readable instead of forcing a horizontal
        scrollbar and clipping it. The progress column only appears while
        something is actually running — a column of dashes on every row is
        noise that looks like an unfinished feature.
        """
        width = self.table.viewport().width()
        mode = getattr(self, "mode", "easy")

        show_format = mode == "pro" and width >= 560
        show_progress = self._has_progress() and width >= 430
        show_artist = width >= 580

        self.table.setColumnHidden(COL_FORMAT, not show_format)
        self.table.setColumnHidden(COL_PROGRESS, not show_progress)
        self.table.setColumnHidden(COL_ARTIST, not show_artist)

        # Below this width the buttons clip, so the same actions move into
        # one menu rather than being cut off mid-word.
        compact = self.width() < 900
        for widget in (self.edit_button, self.titles_button,
                       self.select_button):
            widget.setVisible(not compact)
        self.actions_button.setVisible(compact)

    def _has_progress(self) -> bool:
        if self.model.progress:
            return True
        return any(
            row.track.status in ("queued", "downloading", "converting", "syncing")
            for row in self.model.rows
        )

    def refresh_progress_column(self) -> None:
        """Show the progress column only while a job is in flight."""
        self._apply_column_visibility()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_column_visibility()

    # --------------------------------------------------------------- helpers

    def _on_filter_changed(self, _index: int) -> None:
        self.proxy.set_status_filter(self.filter_combo.currentData())
        self._update_empty_state()

    def _on_search_changed(self, text: str) -> None:
        self.proxy.set_search(text)
        self._update_empty_state()

    def _on_checks_changed(self) -> None:
        self._update_counts()
        self.checked_changed.emit()

    def _on_selection_changed(self, *_args) -> None:
        self._update_counts()
        self.edit_button.setEnabled(self.current_row() is not None)

    def _update_counts(self) -> None:
        if self.source is None:
            # Nothing is selected, so the actions that need a song must
            # not look available.
            self.titles_button.setEnabled(False)
            self._suggestions_action.setEnabled(False)
            self.edit_button.setEnabled(False)
            self._edit_action.setEnabled(False)
            return
        total = len(self.model.rows)
        checked = len(self.model.checked)
        if self.source.is_folder:
            bits = ["Folder", _songs(total)]
        elif self.source.is_all:
            bits = ["Everything you have", _songs(total)]
        else:
            bits = [self.source.uploader or "Unknown artist", _songs(total)]
        if checked:
            bits.append(f"{checked} ticked")
        self.subtitle_label.setText(" · ".join(bits))

        pending = sum(
            1 for row in self.model.rows if row.track.has_suggestion
        )
        label = (
            f"Suggested titles ({pending})" if pending else "Suggested titles…"
        )
        self.titles_button.setText(label)
        self.titles_button.setEnabled(pending > 0)
        self._suggestions_action.setText(
            f"Suggested titles ({pending})" if pending else "Suggested titles…"
        )
        self._suggestions_action.setEnabled(pending > 0)
        self._edit_action.setEnabled(self.current_row() is not None)

    def _update_empty_state(self) -> None:
        if self.model.rowCount() == 0:
            if self.source is None:
                self.empty_label.setText(
                    "<div style='line-height:150%'>"
                    "<div style='font-size:18px; font-weight:700'>"
                    "Nothing here yet</div><br>"
                    "<b>1.</b> Paste a link in the bar at the top "
                    "(YouTube, X, or another site).<br>"
                    "<b>2.</b> Press <b>Add</b>.<br>"
                    "<b>3.</b> Tick the songs you want, then press "
                    "<b>Download to Spotify</b>."
                    "</div>"
                )
            elif self.source.is_folder:
                self.empty_label.setText(
                    "This folder is empty.<br><br>"
                    "Drag songs onto it in the library on the left, or add "
                    "that link and drag it in."
                )
            else:
                self.empty_label.setText(
                    "This link has no songs yet.<br>"
                    "Press <b>Refresh</b> for the selected link to read it "
                    "again."
                )
            self.stack.setCurrentWidget(self.empty_label)
            return
        if self.proxy.rowCount() == 0:
            reason = "this filter" if self.search.text().strip() else "this view"
            self.empty_label.setText(
                f"No songs match {reason}.<br><br>"
                "<a href='clear'>Show every song here</a>"
            )
            self.stack.setCurrentWidget(self.empty_label)
            return
        self.stack.setCurrentWidget(self.table)

    def _on_empty_link(self, link: str) -> None:
        if link == "clear":
            self.search.clear()
            self.filter_combo.setCurrentIndex(0)
            # The visible query may live in the window's library search
            # box rather than in this list's own filter. Ask for both to
            # be cleared, or the screen shows every song under a query
            # that matches none.
            self.clear_search_requested.emit()

    def _on_double_click(self, index: QModelIndex) -> None:
        source_index = self.proxy.mapToSource(index)
        row = source_index.row()
        if 0 <= row < len(self.model.rows):
            self.edit_details_requested.emit(self.model.rows[row].track.id)

    def _edit_current(self) -> None:
        row = self.current_row()
        if row is not None:
            self.edit_details_requested.emit(row.track.id)

    def _toggle_one(self, track_id: int) -> None:
        checked = track_id in self.model.checked
        self.model.toggle_check(track_id, not checked)

    def _remove_selected(self) -> None:
        """Remove every highlighted song, after one confirmation upstream."""
        ids = self.table.selected_track_ids()
        if ids:
            self.remove_selected_requested.emit(ids)

    def suggestion_rows(self) -> list[TrackRow]:
        return [row for row in self.model.rows if row.track.has_suggestion]

    def _context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        source_index = self.proxy.mapToSource(index)
        row = source_index.row()
        if not (0 <= row < len(self.model.rows)):
            return
        track_row = self.model.rows[row]
        track = track_row.track

        menu = QMenu(self)
        menu.addAction(
            "Edit details…",
            lambda: self.edit_details_requested.emit(track.id),
        )
        menu.addSeparator()
        menu.addAction(
            "Open source link",
            lambda: self.open_source_requested.emit(track.url),
        )
        menu.addAction("Retry", lambda: self.retry_requested.emit(track.id))
        menu.addAction(
            "Re-convert",
            lambda: self.reconvert_requested.emit(track.id),
        )
        menu.addSeparator()
        selected = self.table.selected_track_ids()
        if len(selected) > 1:
            menu.addAction(
                f"Remove {len(selected)} songs from the library…",
                self._remove_selected,
            )
        else:
            menu.addAction(
                "Remove from library…",
                lambda: self.remove_requested.emit(track.id),
            )
        menu.exec(self.table.viewport().mapToGlobal(position))


def _songs(count: int) -> str:
    return f"{count} song{'' if count == 1 else 's'}"
