"""Table model and filter proxy for tracks in the selected source."""

from __future__ import annotations

from pathlib import Path
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


def _number(value) -> float:
    """A value for numeric sorting; anything unreadable sorts last."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


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


def _is_checked_value(value) -> bool:
    """Read a check state from whatever Qt hands us.

    Qt passes the state as a plain int (2 for checked) when a user clicks,
    but as a ``Qt.CheckState`` member when code calls ``setData``. Comparing
    either form directly with ``Qt.Checked`` is unreliable, and made a
    ticked box impossible to tick again.
    """
    raw = getattr(value, "value", value)
    try:
        return int(raw) == 2
    except (TypeError, ValueError):
        return False


class TrackTableModel(QAbstractTableModel):
    check_changed = Signal()
    check_toggled = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[TrackRow] = []
        self.checked: set[int] = set()
        self.progress: dict[int, float] = {}
        self._index_by_id: dict[int, int] = {}
        self.numbered_rows = False

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
                # In the "all songs" view each link's own position would
                # repeat (1, 1, 1...), so number the rows instead.
                if self.numbered_rows:
                    return str(index.row() + 1)
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
                return status_label(display_status(track, row.file_missing))
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
                if row.file_missing:
                    return "The audio file is missing. Use Retry to fetch it again."
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
            if _is_checked_value(value):
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
        # ItemIsDragEnabled is required: without it Qt refuses to start a
        # drag no matter what the view says, which made dragging do nothing.
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
        if index.column() == COL_CHECK:
            flags |= Qt.ItemIsUserCheckable
        return flags

    # ------------------------------------------------------------- contents

    def set_rows(self, rows: Iterable[TrackRow], checked: Optional[Iterable[int]] = None) -> None:
        self.beginResetModel()
        self.rows = [self._with_file_state(row) for row in rows]
        self.progress.clear()
        self._index_by_id = {
            row.track.id: index for index, row in enumerate(self.rows)
        }
        available = set(self._index_by_id)
        self.checked = set(checked or ()) & available
        self.endResetModel()
        self.check_changed.emit()

    @staticmethod
    def _with_file_state(row: TrackRow) -> TrackRow:
        """Flag converted songs whose audio file has gone missing."""
        if not is_converted(row.track.status):
            return row
        paths = [p for p in (row.track.mp3_path, row.track.m4a_path) if p]
        missing = bool(paths) and not any(Path(p).exists() for p in paths)
        if missing == row.file_missing:
            return row
        return TrackRow(
            source=row.source, track=row.track, position=row.position,
            file_missing=missing,
        )

    def replace_track(self, track: Track) -> None:
        index = self._index_by_id.get(track.id)
        if index is None:
            return
        row = self.rows[index]
        self.rows[index] = self._with_file_state(
            TrackRow(source=row.source, track=track, position=row.position)
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

    def row_index_for_id(self, track_id: int) -> Optional[int]:
        """Row number for a track id, for use with QModelIndex."""
        return self._index_by_id.get(track_id)

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

    def set_checked_ids(self, ids: Iterable[int]) -> None:
        self.set_checked(ids)

    def toggle_check(self, track_id: int, checked: bool) -> None:
        index = self._index_by_id.get(track_id)
        if index is None:
            return
        self.setData(self.index(index, COL_CHECK),
                     Qt.Checked if checked else Qt.Unchecked,
                     Qt.CheckStateRole)

    def check_problems(self) -> None:
        """Select every song that needs attention.

        This covers errors, missing files, sync failures, and songs whose
        details changed after their file was written.
        """
        ids = [
            row.track.id for row in self.rows
            if row.file_missing
            or row.track.status == ERROR
            or row.track.sync_error
            or row.track.needs_update
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

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        """Sort numbers as numbers.

        Without this the position and length columns sort as text, so a
        playlist reads 1, 10, 11, 2 — which is what a user sees when they
        click the "#" heading.
        """
        model = self.sourceModel()
        if model is None or left.column() != right.column():
            return super().lessThan(left, right)

        column = left.column()
        if column == COL_POSITION:
            if model.numbered_rows:
                # The All Songs view shows its own row numbers, not each
                # link's position, so sorting by position would shuffle
                # them (1, 4, 2, 5...). Its order is the row order.
                return left.row() < right.row()
            return _number(model.rows[left.row()].position) < \
                _number(model.rows[right.row()].position)
        if column == COL_DURATION:
            return _number(model.rows[left.row()].duration) < \
                _number(model.rows[right.row()].duration)
        if column == COL_PROGRESS:
            progress = model.progress
            return _number(
                progress.get(model.rows[left.row()].track.id)
            ) < _number(
                progress.get(model.rows[right.row()].track.id)
            )
        return super().lessThan(left, right)

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
