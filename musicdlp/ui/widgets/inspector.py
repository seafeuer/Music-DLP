"""Right-hand inspector: artwork, a summary, and actions.

Editing happens in the "Edit details" dialog so there is one obvious place
to change a song's title, artist, album, track number, or picture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRect, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)

from musicdlp.core.models import TrackRow, display_status, status_label
from musicdlp.services.thumbnails import ThumbnailService, youtube_fallbacks
from musicdlp.ui.theme import set_tone


class ArtworkSignals(QObject):
    done = Signal(int, str)


class ArtworkTask(QRunnable):
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
        path = self.thumbnails.fetch(
            self.track.platform,
            self.track.media_id,
            self.track.thumbnail_url,
            fallbacks,
        )
        if path:
            self.signals.done.emit(self.track.id, str(path))


class Inspector(QWidget):
    edit_details_requested = Signal(int)
    reconvert_requested = Signal(int)
    retry_requested = Signal(int)
    open_source_requested = Signal(str)
    reveal_requested = Signal(int)
    include_changed = Signal(int, bool)

    def __init__(self, thumbnails: ThumbnailService, parent=None) -> None:
        super().__init__(parent)
        self.thumbnails = thumbnails
        self.current_row: Optional[TrackRow] = None
        self._full_title = ""

        self._art_signals = ArtworkSignals(self)
        self._art_signals.done.connect(self._on_artwork)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)

        heading = QLabel("Song details")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        self.artwork = QLabel("No picture")
        self.artwork.setFixedSize(120, 120)
        self.artwork.setAlignment(Qt.AlignCenter)
        self.artwork.setObjectName("muted")
        self.artwork.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 8px;"
        )
        layout.addWidget(self.artwork, 0, Qt.AlignHCenter)

        self.title_label = QLabel("")
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(self.title_label)

        self.artist_label = QLabel("")
        self.artist_label.setWordWrap(True)
        layout.addWidget(self.artist_label)

        self.album_label = QLabel("")
        self.album_label.setWordWrap(True)
        self.album_label.setObjectName("muted")
        layout.addWidget(self.album_label)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.status_label)

        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setObjectName("muted")
        layout.addWidget(self.hint_label)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setTextFormat(Qt.PlainText)
        self.error_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        set_tone(self.error_label, "danger")
        layout.addWidget(self.error_label)

        # The actions come before the file name: on a 620-px-tall window
        # they were the part pushed out of sight, and they are the reason
        # to look at this panel.
        actions = QWidget()
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(6)
        self.actions_host = actions

        buttons = QGridLayout()
        buttons.setSpacing(6)
        self.include_check = QCheckBox("Include in the next run")
        self.include_check.setToolTip(
            "The same as ticking the box next to the song in the list"
        )
        self.include_check.toggled.connect(self._include_toggled)
        buttons.addWidget(self.include_check, 0, 0, 1, 2)

        self.edit_button = QPushButton("Edit details…")
        self.edit_button.setObjectName("primary")
        self.edit_button.setToolTip(
            "Change the title, artist, album, track number, or picture (F2)"
        )
        self.edit_button.clicked.connect(self._edit)
        self.reconvert_button = QPushButton("Re-convert")
        self.reconvert_button.setToolTip(
            "Write the file again with the current details"
        )
        self.reconvert_button.clicked.connect(self._reconvert)
        self.retry_button = QPushButton("Retry")
        self.retry_button.setToolTip(
            "Try the failed step again, from wherever it stopped"
        )
        self.retry_button.clicked.connect(self._retry)
        buttons.addWidget(self.edit_button, 1, 0, 1, 2)
        buttons.addWidget(self.reconvert_button, 2, 0)
        buttons.addWidget(self.retry_button, 2, 1)
        actions_layout.addLayout(buttons)

        link_row = QHBoxLayout()
        self.source_button = QPushButton("Open source")
        self.source_button.setToolTip("Open the original link in your browser")
        self.source_button.clicked.connect(self._open_source)
        self.reveal_button = QPushButton("Show file")
        self.reveal_button.setToolTip("Show the file in Windows Explorer")
        self.reveal_button.clicked.connect(self._reveal)
        link_row.addWidget(self.source_button)
        link_row.addWidget(self.reveal_button)
        actions_layout.addLayout(link_row)

        # Actions sit directly under the status, above the hint and the
        # error text: at 620 px a long title plus an error pushed them
        # below the fold, so the controls needed to fix a failed song were
        # the part that could not be reached without scrolling.
        layout.insertWidget(layout.indexOf(self.status_label) + 1, actions)

        self.paths_label = QLabel("")
        self.paths_label.setWordWrap(True)
        self.paths_label.setObjectName("mutedSmall")
        self.paths_label.setTextFormat(Qt.PlainText)
        self.paths_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.paths_label.setMinimumWidth(0)
        layout.addWidget(self.paths_label)

        layout.addStretch(1)
        self.actions_host.hide()

    def set_row(self, row: Optional[TrackRow], checked: bool = False) -> None:
        self.current_row = row
        self.actions_host.setVisible(row is not None)
        self._updating_include = True
        self.include_check.setChecked(bool(row) and checked)
        self._updating_include = False
        if row is None:
            self._full_title = ""
            self.title_label.setToolTip("")
            self.title_label.setText("No song selected")
            self.artist_label.setText(
                "Pick a song to see its details and edit its name."
            )
            self.album_label.clear()
            self.album_label.hide()
            self.status_label.clear()
            self.hint_label.clear()
            self.hint_label.hide()
            self.error_label.clear()
            self.error_label.hide()
            self.paths_label.clear()
            self.artwork.setPixmap(QPixmap())
            self.artwork.setText("No picture")
            return

        track = row.track
        self._full_title = track.display_title
        self.title_label.setToolTip(track.display_title)
        self._apply_title()
        self.artist_label.setText(track.display_artist or "Unknown artist")

        album = track.display_album
        track_no = track.display_track
        bits = []
        if album:
            bits.append(album)
        if track_no:
            bits.append(f"Track {track_no}")
        self.album_label.setText(" · ".join(bits))
        self.album_label.setVisible(bool(bits))

        status = display_status(track, row.file_missing)
        self.status_label.setText(status_label(status))
        self._update_hint(track, row, checked, status)

        message = track.error or ""
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

        paths = [p for p in (track.mp3_path, track.m4a_path) if p]
        if row.file_missing:
            self.paths_label.setText("")
            self.paths_label.setToolTip("\n".join(paths))
        elif paths:
            names = ", ".join(Path(p).name for p in paths)
            self.paths_label.setText(f"Saved as: {names}")
            self.paths_label.setToolTip("\n".join(paths))
        else:
            self.paths_label.setText("Not converted yet.")
            self.paths_label.setToolTip("")

        self.reconvert_button.setEnabled(
            bool(track.raw_path or track.output_paths) and not row.file_missing
        )
        self.retry_button.setEnabled(
            row.file_missing
            or status in ("error", "cancelled", "needs_update")
            or bool(track.sync_error)
        )
        self.reveal_button.setEnabled(bool(paths))

        if track.cover_path and Path(track.cover_path).exists():
            self._set_pixmap(track.cover_path)
        else:
            self.artwork.setPixmap(QPixmap())
            self.artwork.setText("Loading…" if track.thumbnail_url else "No picture")
            if track.thumbnail_url:
                QThreadPool.globalInstance().start(
                    ArtworkTask(self.thumbnails, track, self._art_signals)
                )

    def _apply_title(self) -> None:
        """Keep a very long title to three lines.

        At 620 px a 170-character title wrapped to a dozen lines and
        pushed Retry and Show file below the panel's visible area — the
        controls needed to fix the song. The tooltip keeps the full text.
        """
        if not self._full_title:
            return
        width = max(120, self.title_label.width() or self.width() - 4)
        metrics = QFontMetrics(self.title_label.font())
        limit = metrics.lineSpacing() * 3
        text = self._full_title
        if metrics.boundingRect(
            QRect(0, 0, width, 0), Qt.TextWordWrap, text
        ).height() > limit:
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                candidate = text[:middle].rstrip() + "…"
                height = metrics.boundingRect(
                    QRect(0, 0, width, 0), Qt.TextWordWrap, candidate
                ).height()
                if height <= limit:
                    low = middle
                else:
                    high = middle - 1
            text = text[:low].rstrip() + "…"
        self.title_label.setText(text)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._full_title:
            self._apply_title()

    def _set_pixmap(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.artwork.setText("No picture")
            return
        self.artwork.setText("")
        self.artwork.setPixmap(
            pixmap.scaled(120, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _on_artwork(self, track_id: int, path: str) -> None:
        if self.current_row and self.current_row.track.id == track_id:
            self._set_pixmap(path)

    def _update_hint(self, track, row, checked: bool, status: str) -> None:
        """One explanation at a time, the most urgent first."""
        if row.file_missing:
            self.hint_label.setText(
                "The audio file is missing from your computer. Press Retry to "
                "download it again."
            )
        elif track.needs_update:
            self.hint_label.setText(
                "The details were changed but the file still has the old "
                "ones. Open “Edit details” and tick “Write these details "
                "into the audio file now”, or press Re-convert."
            )
        elif status == "error" and track.sync_error:
            self.hint_label.setText(
                "The file is fine; only the Spotify copy failed. Press Retry."
            )
        elif not checked:
            # Highlighting a row and including it are two different
            # actions. Say so where the user is looking, not only in the
            # step strip at the top of the window.
            self.hint_label.setText(
                "Not in the next run. Tick “Include in the next run” below "
                "to download this song."
            )
        else:
            self.hint_label.clear()
        self.hint_label.setVisible(bool(self.hint_label.text()))

    def set_checked(self, checked: bool) -> None:
        """Reflect the list's checkbox without emitting a change back."""
        self._updating_include = True
        self.include_check.setChecked(checked)
        self._updating_include = False
        if self.current_row is not None:
            track = self.current_row.track
            self._update_hint(
                track, self.current_row, bool(checked),
                display_status(track, self.current_row.file_missing),
            )

    def _include_toggled(self, checked: bool) -> None:
        if getattr(self, "_updating_include", False) or not self.current_row:
            return
        self.include_changed.emit(self.current_row.track.id, checked)

    def _edit(self) -> None:
        if self.current_row:
            self.edit_details_requested.emit(self.current_row.track.id)

    def _reconvert(self) -> None:
        if self.current_row:
            self.reconvert_requested.emit(self.current_row.track.id)

    def _retry(self) -> None:
        if self.current_row:
            self.retry_requested.emit(self.current_row.track.id)

    def _open_source(self) -> None:
        if self.current_row:
            self.open_source_requested.emit(self.current_row.track.url)

    def _reveal(self) -> None:
        if self.current_row:
            self.reveal_requested.emit(self.current_row.track.id)
