"""Table model and filter proxy for tracks in the selected source."""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal

from musicdlp.core.models import (
    ERROR,
    NEEDS_UPDATE,
    NEW,
    Track,
    TrackRow,
    display_status,
    format_duration,
    is_converted,
    is_downloaded,
    status_label,
)

ROW_ROLE = Qt.UserRole + 1
PROGRESS_ROLE = Qt.UserRole + 2

COLUMNS = ["", "#", "Title", "Channel", "Length", "Format", "Status", "Progress"]
COL_CHECK = 0
COL_POSITION = 1
COL_TITLE = 2
COL_ARTIST = 3
COL_DURATION = 4
COL_FORMAT = 5
COL_STATUS = 6
COL_PROGRESS = 7


def _format_for(track: Track) -> str:
    has_mp3 = bool(track.mp3_path)
    has_m4a = bool(track.m4a_path)
    if has_mp3 and has_m4a:
        return "MP3 + M4A"
    if has_mp3:
        return "MP3"
    if has_m4a:
        return "M4A"
    return "-"


class TrackTableModel(QAbstractTableModel):
    check_changed = Signal()
    check_toggled = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[TrackRow] = []
        self.checked: set[int] = set()
        self.progress: dict[int, float] = {}
        self._index_by_id: dict[int, int] = {}

    # ------------------------------------------------------------ model API

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal and 0 <= section < len(COLUMNS):
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.rows)):
            return None
        row = self.rows[index.row()]
        track = row.track
        column = index.column()

        if role == Qt.DisplayRole:
            if column == COL_POSITION:
                return str(row.position)
            if column == COL_TITLE:
                return row.display_title
            if column == COL_ARTIST:
                return row.display_artist
            if column == COL_DURATION:
                return format_duration(row.duration)
            if column == COL_FORMAT:
                return _format_for(track)
            if column == COL_STATUS:
                return status_label(display_status(track))
            if column == COL_PROGRESS:
                percent = self.progress.get(track.id)
                return "" if percent is None else f"{percent:.0f}%"
            return ""

        if role == Qt.CheckStateRole and column == COL_CHECK:
            return Qt.Checked if track.id in self.checked else Qt.Unchecked

        if role == Qt.TextAlignmentRole:
            if column in (COL_POSITION, COL_DURATION, COL_PROGRESS):
                return int(Qt.AlignCenter)
            return int(Qt.AlignVCenter | Qt.AlignLeft)

        if role == Qt.ToolTipRole:
            if column == COL_STATUS:
                return (
                    track.error
                    or track.sync_error
                    or status_label(display_status(track))
                )
            if column == COL_TITLE:
                return track.display_title
            if column == COL_ARTIST:
                return track.display_artist
            if column == COL_PROGRESS:
                percent = self.progress.get(track.id)
                return "Not started" if percent is None else f"{percent:.0f}%"
            return None

        if role == ROW_ROLE:
            return row

        if role == PROGRESS_ROLE:
            return self.progress.get(track.id)

        return None

    def setData(self, index: QModelIndex, value, role=Qt.EditRole) -> bool:
        if (
            role == Qt.CheckStateRole
            and index.isValid()
            and index.column() == COL_CHECK
        ):
            track_id = self.rows[index.row()].track.id
            if value == Qt.Checked:
                self.checked.add(track_id)
            else:
                self.checked.discard(track_id)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            self.check_toggled.emit(track_id)
            self.check_changed.emit()
            return True
        return False

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == COL_CHECK:
            flags |= Qt.ItemIsUserCheckable
        return flags

    # ------------------------------------------------------------- contents

    def set_rows(self, rows: Iterable[TrackRow], checked: Optional[Iterable[int]] = None) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.progress.clear()
        self._index_by_id = {
            row.track.id: index for index, row in enumerate(self.rows)
        }
        available = set(self._index_by_id)
        self.checked = set(checked or ()) & available
        self.endResetModel()
        self.check_changed.emit()

    def replace_track(self, track: Track) -> None:
        index = self._index_by_id.get(track.id)
        if index is None:
            return
        row = self.rows[index]
        self.rows[index] = TrackRow(
            source=row.source, track=track, position=row.position
        )
        left = self.index(index, 0)
        right = self.index(index, len(COLUMNS) - 1)
        self.dataChanged.emit(left, right)

    def update_row(self, track_id: int) -> None:
        index = self._index_by_id.get(track_id)
        if index is None:
            return
        left = self.index(index, 0)
        right = self.index(index, len(COLUMNS) - 1)
        self.dataChanged.emit(left, right)

    def set_progress(self, track_id: int, percent: float) -> None:
        self.progress[track_id] = max(0.0, min(100.0, float(percent)))
        index = self._index_by_id.get(track_id)
        if index is None:
            return
        cell = self.index(index, COL_PROGRESS)
        self.dataChanged.emit(cell, cell, [Qt.DisplayRole, PROGRESS_ROLE])

    def clear_progress(self) -> None:
        if not self.progress:
            return
        self.progress.clear()
        if self.rows:
            top = self.index(0, COL_PROGRESS)
            bottom = self.index(len(self.rows) - 1, COL_PROGRESS)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, PROGRESS_ROLE])

    def row_for_id(self, track_id: int) -> Optional[TrackRow]:
        index = self._index_by_id.get(track_id)
        return self.rows[index] if index is not None else None

    # ------------------------------------------------------------ selection

    def checked_rows(self) -> list[TrackRow]:
        return [row for row in self.rows if row.track.id in self.checked]

    def checked_ids(self) -> set[int]:
        return set(self.checked)

    def set_checked(self, ids: Iterable[int]) -> None:
        self.checked = set(ids) & set(self._index_by_id)
        if self.rows:
            top = self.index(0, COL_CHECK)
            bottom = self.index(len(self.rows) - 1, COL_CHECK)
            self.dataChanged.emit(top, bottom, [Qt.CheckStateRole])
        self.check_changed.emit()

    def check_all(self) -> None:
        self.set_checked(self._index_by_id.keys())

    def clear_checks(self) -> None:
        self.set_checked(())

    def check_status(self, status: str) -> None:
        ids = [
            row.track.id for row in self.rows
            if row.track.status == status
        ]
        self.set_checked(ids)


class TrackFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.status_filter = "all"
        self.search = ""
        self.setDynamicSortFilter(True)

    def set_status_filter(self, value: str) -> None:
        self.beginFilterChange()
        self.status_filter = value or "all"
        self.endFilterChange()

    def set_search(self, text: str) -> None:
        self.beginFilterChange()
        self.search = (text or "").strip().lower()
        self.endFilterChange()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None or not (0 <= source_row < len(model.rows)):
            return True
        row = model.rows[source_row]
        track = row.track
        status = track.status

        if self.status_filter == "new" and status != NEW:
            return False
        if self.status_filter == "downloaded" and not is_downloaded(status):
            return False
        if self.status_filter == "converted" and not is_converted(status):
            return False
        if self.status_filter == "errors" and not (
            status == ERROR or track.sync_error
        ):
            return False
        if self.status_filter == "needs_update" and not (
            track.needs_update and is_converted(status)
        ):
            return False

        if self.search:
            haystack = f"{row.display_title} {row.display_artist}".lower()
            if self.search not in haystack:
                return False
        return True
