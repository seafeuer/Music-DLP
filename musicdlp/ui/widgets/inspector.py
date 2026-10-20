"""Right-hand inspector: artwork, metadata editing, paths, actions."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

from musicdlp.core.models import TrackRow, status_label
from musicdlp.services.thumbnails import ThumbnailService, youtube_fallbacks


class ArtworkSignals(QObject):
    done = Signal(int, str)


class ArtworkTask(QRunnable):
    def __init__(
        self,
        thumbnails: ThumbnailService,
        track,
        signals: ArtworkSignals,
    ) -> None:
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
    metadata_applied = Signal(int, str, str, str, int)
    reconvert_requested = Signal(int)
    retry_requested = Signal(int)
    open_source_requested = Signal(str)
    reveal_requested = Signal(int)

    def __init__(self, thumbnails: ThumbnailService, parent=None) -> None:
        super().__init__(parent)
        self.thumbnails = thumbnails
        self.current_row: Optional[TrackRow] = None

        self._art_signals = ArtworkSignals(self)
        self._art_signals.done.connect(self._on_artwork)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        heading = QLabel("Details")
        heading.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(heading)

        self.artwork = QLabel("No artwork")
        self.artwork.setFixedSize(240, 240)
        self.artwork.setAlignment(Qt.AlignCenter)
        self.artwork.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 8px;"
        )
        self.artwork.setObjectName("artwork")
        layout.addWidget(self.artwork, 0, Qt.AlignHCenter)

        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignLeft)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        self.title_edit = QLineEdit()
        self.artist_edit = QLineEdit()
        self.album_edit = QLineEdit()
        for edit in (self.title_edit, self.artist_edit, self.album_edit):
            edit.setMinimumWidth(120)
        self.track_spin = QSpinBox()
        self.track_spin.setRange(0, 999)
        self.track_spin.setSpecialValueText("—")
        self.track_spin.setFixedWidth(90)
        self.form.addRow("Title", self.title_edit)
        self.form.addRow("Artist", self.artist_edit)
        self.form.addRow("Album", self.album_edit)
        self.form.addRow("Track", self.track_spin)
        layout.addLayout(self.form)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.status_label)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #f87171;")
        layout.addWidget(self.error_label)

        self.paths_label = QLabel("")
        self.paths_label.setWordWrap(True)
        self.paths_label.setObjectName("mutedSmall")
        layout.addWidget(self.paths_label)

        button_row = QGridLayout()
        button_row.setSpacing(6)
        self.apply_button = QPushButton("Apply metadata")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self._apply)
        self.reconvert_button = QPushButton("Re-convert")
        self.reconvert_button.clicked.connect(self._reconvert)
        self.retry_button = QPushButton("Retry")
        self.retry_button.clicked.connect(self._retry)
        button_row.addWidget(self.apply_button, 0, 0, 1, 2)
        button_row.addWidget(self.reconvert_button, 1, 0)
        button_row.addWidget(self.retry_button, 1, 1)
        layout.addLayout(button_row)

        link_row = QHBoxLayout()
        self.source_button = QPushButton("Open source")
        self.source_button.clicked.connect(self._open_source)
        self.reveal_button = QPushButton("Show file")
        self.reveal_button.clicked.connect(self._reveal)
        link_row.addWidget(self.source_button)
        link_row.addWidget(self.reveal_button)
        layout.addLayout(link_row)

        layout.addStretch(1)
        self.setEnabled(False)

    def set_row(self, row: Optional[TrackRow]) -> None:
        self.current_row = row
        self.setEnabled(row is not None)
        if row is None:
            self.title_edit.clear()
            self.artist_edit.clear()
            self.album_edit.clear()
            self.track_spin.setValue(0)
            self.status_label.clear()
            self.error_label.clear()
            self.paths_label.clear()
            self.artwork.setPixmap(QPixmap())
            self.artwork.setText("No artwork")
            return

        track = row.track
        self.title_edit.setText(track.display_title)
        self.artist_edit.setText(track.display_artist)
        self.album_edit.setText(track.display_album)
        self.track_spin.setValue(track.display_track or 0)

        self.status_label.setText(status_label(track.status))
        message = track.error or track.sync_error or ""
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

        paths = [p for p in (track.mp3_path, track.m4a_path) if p]
        self.paths_label.setText(
            "\n".join(paths) if paths else "Not converted yet."
        )

        self.reconvert_button.setEnabled(bool(track.raw_path or track.output_paths))
        self.retry_button.setEnabled(track.status in ("error", "cancelled"))

        if track.cover_path and Path(track.cover_path).exists():
            self._set_pixmap(track.cover_path)
        else:
            self.artwork.setPixmap(QPixmap())
            self.artwork.setText("Loading…" if track.thumbnail_url else "No artwork")
            if track.thumbnail_url:
                QThreadPool.globalInstance().start(
                    ArtworkTask(self.thumbnails, track, self._art_signals)
                )

    def _set_pixmap(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.artwork.setText("No artwork")
            return
        self.artwork.setText("")
        self.artwork.setPixmap(
            pixmap.scaled(
                240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def _on_artwork(self, track_id: int, path: str) -> None:
        if self.current_row and self.current_row.track.id == track_id:
            self._set_pixmap(path)

    def _apply(self) -> None:
        if not self.current_row:
            return
        self.metadata_applied.emit(
            self.current_row.track.id,
            self.title_edit.text().strip(),
            self.artist_edit.text().strip(),
            self.album_edit.text().strip(),
            self.track_spin.value(),
        )

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
