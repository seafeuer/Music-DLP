"""The central track table: header, filters, virtualized rows, selection."""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QPushButton, QStackedWidget, QTableView, QVBoxLayout,
    QWidget,
)

from musicdlp.core.models import ERROR, NEW, Source, Track, TrackRow
from musicdlp.ui.delegates import ProgressDelegate, StatusDelegate
from musicdlp.ui.models.track_model import (
    COL_ARTIST, COL_CHECK, COL_DURATION, COL_FORMAT, COL_POSITION,
    COL_PROGRESS, COL_STATUS, COL_TITLE, ROW_ROLE, TrackFilterProxy,
    TrackTableModel,
)


class TrackTableWidget(QWidget):
    checked_changed = Signal()
    edit_details_requested = Signal(int)
    retry_requested = Signal(int)
    reconvert_requested = Signal(int)
    remove_requested = Signal(int)
    open_source_requested = Signal(str)

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
        self.title_label = QLabel("No source selected")
        self.title_label.setStyleSheet("font-size: 17px; font-weight: 700;")
        self.subtitle_label = QLabel("Add a link to get started.")
        self.subtitle_label.setObjectName("muted")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.subtitle_label)
        header_row.addLayout(title_box, 1)
        layout.addLayout(header_row)

        # Filter row -------------------------------------------------------
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        self.filter_combo = QComboBox()
        for label, value in [
            ("All songs", "all"),
            ("Not downloaded", "new"),
            ("Downloaded", "downloaded"),
            ("Converted", "converted"),
            ("Needs update", "needs_update"),
            ("Errors", "errors"),
        ]:
            self.filter_combo.addItem(label, value)
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.filter_combo)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search titles or channels…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.proxy.set_search)
        filter_row.addWidget(self.search, 1)

        self.edit_button = QPushButton("Edit details…")
        self.edit_button.setToolTip(
            "Change the title, artist, album, track number, or picture "
            "for the highlighted song"
        )
        self.edit_button.clicked.connect(self._edit_current)
        self.edit_button.setEnabled(False)
        filter_row.addWidget(self.edit_button)

        for text, handler in [
            ("Select all", self.model.check_all),
            ("Select new", lambda: self.model.check_status(NEW)),
            ("Select failed", lambda: self.model.check_status(ERROR)),
            ("Clear", self.model.clear_checks),
        ]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            filter_row.addWidget(button)

        layout.addLayout(filter_row)

        # Table ------------------------------------------------------------
        self.stack = QStackedWidget()
        self.table = QTableView()
        self.table.setModel(self.proxy)
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
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_CHECK, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_POSITION, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            COL_ARTIST, QHeaderView.Interactive
        )
        header.setSectionResizeMode(COL_DURATION, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_FORMAT, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_PROGRESS, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_CHECK, 30)
        self.table.setColumnWidth(COL_POSITION, 38)
        self.table.setColumnWidth(COL_ARTIST, 130)
        self.table.setColumnWidth(COL_DURATION, 58)
        self.table.setColumnWidth(COL_FORMAT, 72)
        self.table.setColumnWidth(COL_STATUS, 142)
        self.table.setColumnWidth(COL_PROGRESS, 92)

        self.stack.addWidget(self.table)
        self.empty_label = QLabel("Nothing here yet.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setObjectName("muted")
        self.empty_label.setTextFormat(Qt.RichText)
        self.empty_label.setWordWrap(True)
        self.stack.addWidget(self.empty_label)
        layout.addWidget(self.stack, 1)

        self.model.check_changed.connect(self._on_checks_changed)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

    # ------------------------------------------------------------------ data

    def set_source(
        self,
        source: Optional[Source],
        rows: Iterable[TrackRow],
        checked: Optional[Iterable[int]] = None,
    ) -> None:
        self.source = source
        self.search.clear()
        self.filter_combo.setCurrentIndex(0)
        self.model.set_rows(rows, checked)
        self.table.sortByColumn(COL_POSITION, Qt.AscendingOrder)

        if source is None:
            self.title_label.setText("No source selected")
            self.subtitle_label.setText("Add a link to get started.")
        else:
            self.title_label.setText(source.title)
            uploader = source.uploader or "Unknown channel"
            self.subtitle_label.setText(
                f"{uploader} · {len(self.model.rows)} tracks"
            )
        self._update_counts()
        self._update_empty_state()
        self.edit_button.setEnabled(self.current_row() is not None)

    def update_track(self, track: Track) -> None:
        self.model.replace_track(track)
        self._update_counts()

    def set_progress(self, track_id: int, percent: float) -> None:
        self.model.set_progress(track_id, percent)

    def clear_progress(self) -> None:
        self.model.clear_progress()

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
        self.table.setColumnHidden(COL_FORMAT, mode == "easy")

    # --------------------------------------------------------------- helpers

    def _on_filter_changed(self, _index: int) -> None:
        self.proxy.set_status_filter(self.filter_combo.currentData())
        self._update_empty_state()

    def _on_checks_changed(self) -> None:
        self._update_counts()
        self.checked_changed.emit()

    def _on_selection_changed(self, *_args) -> None:
        self._update_counts()
        self.edit_button.setEnabled(self.current_row() is not None)

    def _update_counts(self) -> None:
        if self.source is None:
            return
        total = len(self.model.rows)
        checked = len(self.model.checked)
        bits = [self.source.uploader or "Unknown channel", f"{total} tracks"]
        if checked:
            bits.append(f"{checked} selected")
        self.subtitle_label.setText(" · ".join(bits))

    def _update_empty_state(self) -> None:
        if self.model.rowCount() == 0:
            if self.source is None:
                self.empty_label.setText(
                    "<div style='line-height:150%'>"
                    "<div style='font-size:18px; font-weight:700'>"
                    "Nothing added yet</div><br>"
                    "<b>1.</b> Paste a link in the bar at the top "
                    "(YouTube, X, or another site).<br>"
                    "<b>2.</b> Press <b>Add</b>.<br>"
                    "<b>3.</b> Pick the songs you want, then press "
                    "<b>Download to Spotify</b>."
                    "</div>"
                )
            else:
                self.empty_label.setText(
                    "This link has no songs yet.<br>"
                    "Press <b>Refresh</b> on the left to check it again."
                )
            self.stack.setCurrentWidget(self.empty_label)
            return
        if self.proxy.rowCount() == 0:
            self.empty_label.setText("No songs match this filter.")
            self.stack.setCurrentWidget(self.empty_label)
            return
        self.stack.setCurrentWidget(self.table)

    def _on_double_click(self, index: QModelIndex) -> None:
        source_index = self.proxy.mapToSource(index)
        row = source_index.row()
        if 0 <= row < len(self.model.rows):
            self.edit_details_requested.emit(self.model.rows[row].track.id)

    def _edit_current(self) -> None:
        row = self.current_row()
        if row is not None:
            self.edit_details_requested.emit(row.track.id)

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
        menu.addAction(
            "Remove from library…",
            lambda: self.remove_requested.emit(track.id),
        )
        menu.exec(self.table.viewport().mapToGlobal(position))
