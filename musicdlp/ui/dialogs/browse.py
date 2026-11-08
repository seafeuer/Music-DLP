"""Browse the library by artist and album, with cover thumbnails."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog, QLabel, QListWidget, QListWidgetItem, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from musicdlp.core.models import TrackRow
from musicdlp.services.thumbnails import ThumbnailService, youtube_fallbacks
from musicdlp.ui.dialogs.footer import DialogFooter
from musicdlp.ui.dialogs.sizing import fit_dialog

TRACK_ROLE = Qt.UserRole + 1
UNKNOWN_ARTIST = "Unknown artist"
NO_ALBUM = "Singles"


class _CoverSignals(QObject):
    done = Signal(int, str)


class _CoverTask(QRunnable):
    def __init__(self, thumbnails: ThumbnailService, track, signals) -> None:
        super().__init__()
        self.thumbnails = thumbnails
        self.track = track
        self.signals = signals

    def run(self) -> None:
        fallbacks = (
            youtube_fallbacks(self.track.media_id)
            if self.track.platform == "youtube" else None
        )
        try:
            path = self.thumbnails.fetch(
                self.track.platform,
                self.track.media_id,
                self.track.thumbnail_url,
                fallbacks,
            )
        except Exception:  # noqa: BLE001 - a cover must never break browsing
            path = None
        if path:
            try:
                self.signals.done.emit(self.track.id, str(path))
            except RuntimeError:
                # The dialog was closed while the image was loading.
                pass


def group_rows(rows: Iterable[TrackRow]) -> dict[str, dict[str, list[TrackRow]]]:
    """Artist -> album -> songs, in a stable display order.

    A song with no album is filed under "Singles", the same label the
    details editor uses, rather than a blank heading.
    """
    groups: dict[str, dict[str, list[TrackRow]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        artist = (row.track.display_artist or "").strip() or UNKNOWN_ARTIST
        album = (row.track.display_album or "").strip() or NO_ALBUM
        groups[artist][album].append(row)
    for albums in groups.values():
        for songs in albums.values():
            songs.sort(
                key=lambda row: (
                    row.track.display_track or 10**6,
                    row.track.display_title.casefold(),
                )
            )
    return dict(groups)


class BrowseDialog(QDialog):
    """A tree of artists and albums, and the covers in the chosen group."""

    edit_requested = Signal(int)

    def __init__(
        self,
        rows: Iterable[TrackRow],
        thumbnails: Optional[ThumbnailService] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Browse by artist and album")
        fit_dialog(self, 980, 660)
        self.rows = list(rows)
        self.thumbnails = thumbnails
        self.groups = group_rows(self.rows)
        self.ticked_ids: list[int] = []
        self._current: list[TrackRow] = []
        self._signals = _CoverSignals(self)
        self._signals.done.connect(self._on_cover)
        self._cover_tasks = 0

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            f"{len(self.rows)} song"
            f"{'' if len(self.rows) == 1 else 's'} grouped by artist and "
            "album. Choose a group to see its covers, then tick the songs "
            "you want to work with."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        split = QSplitter(Qt.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Artist / album", "Songs"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAccessibleName("Artists and albums")
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        split.addWidget(self.tree)

        self.covers = QListWidget()
        self.covers.setViewMode(QListWidget.IconMode)
        self.covers.setIconSize(QSize(120, 120))
        self.covers.setGridSize(QSize(154, 186))
        self.covers.setResizeMode(QListWidget.Adjust)
        self.covers.setMovement(QListWidget.Static)
        self.covers.setWordWrap(True)
        self.covers.setSelectionMode(QListWidget.ExtendedSelection)
        self.covers.setAccessibleName("Songs in this group")
        self.covers.itemDoubleClicked.connect(
            lambda item: self.edit_requested.emit(
                int(item.data(TRACK_ROLE))
            )
        )
        split.addWidget(self.covers)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 620])
        layout.addWidget(split, 1)

        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        layout.addWidget(self.count_label)

        self.footer = DialogFooter(self)
        self.footer.add_action("Tick the songs shown", self._tick_shown)
        self.footer.set_primary(
            "Use ticked songs", self._use_ticked
        )
        self.footer.primary_button.setEnabled(False)

        self._populate_tree()
        self.covers.currentItemChanged.connect(
            lambda *_args: self._update_count()
        )
        self.covers.itemSelectionChanged.connect(self._update_count)

    # ------------------------------------------------------------- populating

    def _populate_tree(self) -> None:
        every = QTreeWidgetItem(["All songs", str(len(self.rows))])
        every.setData(0, Qt.UserRole, ("all", "", ""))
        self.tree.addTopLevelItem(every)
        for artist in sorted(self.groups, key=str.casefold):
            albums = self.groups[artist]
            total = sum(len(songs) for songs in albums.values())
            artist_item = QTreeWidgetItem([artist, str(total)])
            artist_item.setData(0, Qt.UserRole, ("artist", artist, ""))
            self.tree.addTopLevelItem(artist_item)
            for album in sorted(albums, key=str.casefold):
                songs = albums[album]
                album_item = QTreeWidgetItem([album, str(len(songs))])
                album_item.setData(0, Qt.UserRole, ("album", artist, album))
                artist_item.addChild(album_item)
        self.tree.expandItem(every)
        self.tree.setCurrentItem(every)

    @staticmethod
    def _placeholder() -> QIcon:
        pixmap = QPixmap(120, 120)
        pixmap.fill(QColor(90, 90, 96))
        painter = QPainter(pixmap)
        painter.setPen(QColor(210, 210, 214))
        painter.drawText(
            pixmap.rect(), Qt.AlignCenter, "♪"
        )
        painter.end()
        return QIcon(pixmap)

    def _rows_for(self, kind: str, artist: str, album: str) -> list[TrackRow]:
        if kind == "all":
            return self.rows
        if kind == "artist":
            return [
                row
                for songs in self.groups.get(artist, {}).values()
                for row in songs
            ]
        return list(self.groups.get(artist, {}).get(album, []))

    def _on_tree_selection(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        kind, artist, album = item.data(0, Qt.UserRole)
        self._current = self._rows_for(kind, artist, album)
        self._show_covers()

    def _show_covers(self) -> None:
        self.covers.clear()
        self._cover_tasks = 0
        for row in self._current:
            track = row.track
            item = QListWidgetItem(track.display_title)
            item.setData(TRACK_ROLE, track.id)
            item.setToolTip(
                "\n".join(
                    part for part in (
                        track.display_title,
                        track.display_artist,
                        track.display_album,
                    ) if part
                )
            )
            item.setIcon(self._placeholder())
            self.covers.addItem(item)
            self._set_cover(item, track)

        self._update_count()

    def _set_cover(self, item: QListWidgetItem, track) -> None:
        path = None
        if track.cover_path and Path(track.cover_path).exists():
            path = track.cover_path
        elif self.thumbnails is not None:
            cached = self.thumbnails.cached_path(
                track.platform, track.media_id
            )
            if cached.exists():
                path = str(cached)
        if path:
            item.setIcon(QIcon(path))
            return
        # Only a few uncached covers are fetched: a whole library at once
        # would start hundreds of requests and make the dialog crawl.
        if self.thumbnails is None or self._cover_tasks >= 24:
            return
        self._cover_tasks += 1
        QThreadPool.globalInstance().start(
            _CoverTask(self.thumbnails, track, self._signals)
        )

    def _on_cover(self, track_id: int, path: str) -> None:
        for index in range(self.covers.count()):
            item = self.covers.item(index)
            if int(item.data(TRACK_ROLE)) == track_id:
                item.setIcon(QIcon(path))
                return

    # --------------------------------------------------------------- actions

    def _update_count(self) -> None:
        shown = len(self._current)
        chosen = len(self.covers.selectedItems())
        self.count_label.setText(
            f"{shown} song{'' if shown == 1 else 's'} shown"
            + (f" · {chosen} highlighted" if chosen else "")
        )
        # Highlighting a cover is a valid way to choose songs, not only
        # ticking a whole group first.
        self.footer.primary_button.setEnabled(
            bool(self.ticked_ids or self.covers.selectedItems())
        )

    def _tick_shown(self) -> None:
        ids = [row.track.id for row in self._current]
        if not ids:
            return
        self.ticked_ids = sorted(set(self.ticked_ids) | set(ids))
        self.footer.primary_button.setEnabled(True)
        self.count_label.setText(
            f"{len(self.ticked_ids)} song"
            f"{'' if len(self.ticked_ids) == 1 else 's'} ticked."
        )

    def _use_ticked(self) -> None:
        # A highlighted cover is a more precise choice than the whole
        # group. Use it when the user made one.
        highlighted = [
            int(item.data(TRACK_ROLE))
            for item in self.covers.selectedItems()
        ]
        if highlighted:
            # Replace this group's ticks with the highlighted subset, but
            # keep songs ticked in groups the user visited earlier.
            current_ids = {row.track.id for row in self._current}
            kept = [
                track_id for track_id in self.ticked_ids
                if track_id not in current_ids
            ]
            self.ticked_ids = sorted(set(kept) | set(highlighted))
        if not self.ticked_ids:
            self._tick_shown()
        if not self.ticked_ids:
            return
        self.accept()