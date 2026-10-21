"""The "Edit details" dialog: title, artist, album, track, and cover art."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from musicdlp.core.models import Track, is_converted
from musicdlp.services.filenames import render_template


class TrackDetailsDialog(QDialog):
    """Edit one track's metadata and optionally rewrite its file."""

    def __init__(
        self,
        track: Track,
        *,
        album_default: str,
        track_default: int,
        filename_template: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit details")
        self.setMinimumWidth(620)
        self.track = track
        self.filename_template = filename_template

        self.cover_path: Optional[str] = track.cover_path
        self.cover_changed = False
        self.result_values: Optional[dict] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(
            "These details are written into the MP3 file and shown in Spotify."
        )
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(16)
        # Cover art -----------------------------------------------------
        art_box = QVBoxLayout()
        self.artwork = QLabel()
        self.artwork.setFixedSize(168, 168)
        self.artwork.setAlignment(Qt.AlignCenter)
        self.artwork.setStyleSheet(
            "border: 1px solid palette(mid); border-radius: 8px;"
        )
        art_box.addWidget(self.artwork)

        choose = QPushButton("Choose picture…")
        choose.clicked.connect(self._choose_picture)
        art_box.addWidget(choose)

        self.video_button = QPushButton("Use video thumbnail")
        self.video_button.clicked.connect(self._use_video_thumbnail)
        art_box.addWidget(self.video_button)
        art_box.addStretch(1)
        body.addLayout(art_box)

        # Fields --------------------------------------------------------
        fields = QFormLayout()
        fields.setSpacing(8)
        self.title_edit = QLineEdit(track.display_title)
        self.artist_edit = QLineEdit(track.display_artist)
        self.album_edit = QLineEdit(track.display_album or album_default)
        self.track_spin = QSpinBox()
        self.track_spin.setRange(0, 999)
        self.track_spin.setSpecialValueText("—")
        self.track_spin.setValue(track.display_track or track_default)

        fields.addRow("Song title", self.title_edit)
        fields.addRow("Artist", self.artist_edit)
        fields.addRow("Album", self.album_edit)
        fields.addRow("Track number", self.track_spin)

        self.preview = QLabel()
        self.preview.setObjectName("mutedSmall")
        self.preview.setWordWrap(True)
        fields.addRow("File name", self.preview)

        body.addLayout(fields, 1)
        layout.addLayout(body)

        for edit in (self.title_edit, self.artist_edit, self.album_edit):
            edit.textChanged.connect(self._update_preview)
        self.track_spin.valueChanged.connect(self._update_preview)

        # A suggestion worked out from the upload title, if it differs from
        # what is filled in.
        self.suggestion_box = QWidget()
        suggestion_layout = QHBoxLayout(self.suggestion_box)
        suggestion_layout.setContentsMargins(0, 0, 0, 0)
        suggestion_layout.setSpacing(8)
        self.suggestion_label = QLabel("")
        self.suggestion_label.setWordWrap(True)
        self.suggestion_label.setObjectName("muted")
        suggestion_layout.addWidget(self.suggestion_label, 1)
        self.use_suggestion_button = QPushButton("Use suggestion")
        self.use_suggestion_button.clicked.connect(self._use_suggestion)
        suggestion_layout.addWidget(self.use_suggestion_button)
        layout.addWidget(self.suggestion_box)
        self._update_suggestion()

        # What happens on save ------------------------------------------
        converted = is_converted(track.status)
        self.update_check = QCheckBox("Update the audio file now")
        self.update_check.setChecked(converted)
        self.update_check.setEnabled(converted)
        layout.addWidget(self.update_check)

        if converted:
            hint = (
                "Saves your changes and rewrites the file with the new "
                "details. If the song is already in Spotify, the copy there "
                "is refreshed too."
            )
            if track.sync_error:
                hint = "Saves your changes and rewrites the file."
        else:
            hint = (
                "This song has not been downloaded yet. Your details are "
                "saved and used when you download it."
            )
        hint_label = QLabel(hint)
        hint_label.setObjectName("muted")
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        save = buttons.addButton("Save", QDialogButtonBox.AcceptRole)
        save.setObjectName("primary")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_preview()
        self._refresh_artwork()

    # ------------------------------------------------------------------ art

    def _refresh_artwork(self) -> None:
        if self.cover_path and Path(self.cover_path).exists():
            pixmap = QPixmap(self.cover_path)
            if not pixmap.isNull():
                self.artwork.setPixmap(
                    pixmap.scaled(
                        168, 168, Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                )
                return
        self.artwork.setText(
            "No picture yet" if not self.track.thumbnail_url
            else "No picture yet\n(the video's picture is used)"
        )
        self.artwork.setObjectName("muted")
        self.video_button.setEnabled(bool(self.track.thumbnail_url))

    def _choose_picture(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a cover picture",
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not filename:
            return
        self.cover_path = filename
        self.cover_changed = True
        self._refresh_artwork()

    def _use_video_thumbnail(self) -> None:
        self.cover_path = None
        self.cover_changed = True
        self.artwork.setText(
            "The video's picture will be used when the file is written."
        )

    # ------------------------------------------------------------- preview

    def _update_suggestion(self) -> None:
        track = self.track
        if not track.smart_title:
            self.suggestion_box.hide()
            return

        suggested = track.smart_title
        artist = track.smart_artist or ""
        current_title = self.title_edit.text().strip()
        current_artist = self.artist_edit.text().strip()

        if suggested == current_title and artist == current_artist:
            self.suggestion_box.hide()
            return

        confidence = int(round((track.smart_confidence or 0) * 100))
        text = f"From the upload title: “{suggested}”"
        if artist:
            text += f" — {artist}"
        text += f"  ({confidence}% sure)"
        self.suggestion_label.setText(text)
        self.suggestion_box.show()

    def _use_suggestion(self) -> None:
        if self.track.smart_title:
            self.title_edit.setText(self.track.smart_title)
        if self.track.smart_artist:
            self.artist_edit.setText(self.track.smart_artist)
        self.suggestion_box.hide()

    def _update_preview(self) -> None:
        stem = render_template(
            self.filename_template,
            title=self.title_edit.text().strip() or "Untitled",
            artist=self.artist_edit.text().strip(),
            album=self.album_edit.text().strip(),
            track=self.track_spin.value() or None,
            media_id=self.track.media_id,
            source="",
        )
        self.preview.setText(f"{stem}.mp3")

    # ---------------------------------------------------------------- save

    def _save(self) -> None:
        title = self.title_edit.text().strip()
        if not title:
            self.title_edit.setFocus()
            self.title_edit.setPlaceholderText("A title is required")
            return

        self.result_values = {
            "title": title,
            "artist": self.artist_edit.text().strip(),
            "album": self.album_edit.text().strip(),
            "track_number": self.track_spin.value() or None,
            "cover_path": self.cover_path,
            "cover_changed": self.cover_changed,
            "update_file": self.update_check.isChecked()
            and self.update_check.isEnabled(),
        }
        self.accept()
