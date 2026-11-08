"""One dialog to change many songs at once."""

from __future__ import annotations

from typing import Iterable

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QSpinBox, QVBoxLayout, QWidget,
)

from musicdlp.core.models import TrackRow
from musicdlp.services.bulk_edit import BulkEdit, title_edit
from musicdlp.ui.dialogs.accessibility import pair_form_labels
from musicdlp.ui.dialogs.footer import DialogFooter
from musicdlp.ui.dialogs.sizing import fit_dialog


class BulkEditDialog(QDialog):
    """Set album, artist, track numbers, or a title replacement."""

    def __init__(
        self, rows: Iterable[TrackRow], parent=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit detail for several songs")
        fit_dialog(self, 620, 560)
        self.rows = list(rows)
        self.result_edit: BulkEdit | None = None
        self.write_files = False

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            f"One rule for {len(self.rows)} ticked song"
            f"{'' if len(self.rows) == 1 else 's'}. Tick only the parts you "
            "want to change; everything else stays as it is."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)

        self.album_check = QCheckBox("Set the album to")
        self.album_edit = QLineEdit(self._first_album())
        self.album_edit.setPlaceholderText("Album name")
        self.album_edit.setEnabled(False)
        self.album_check.toggled.connect(self.album_edit.setEnabled)
        form.addRow(self.album_check, self.album_edit)

        self.artist_check = QCheckBox("Set the artist to")
        self.artist_edit = QLineEdit(self._first_artist())
        self.artist_edit.setPlaceholderText("Artist name")
        self.artist_edit.setEnabled(False)
        self.artist_check.toggled.connect(self.artist_edit.setEnabled)
        form.addRow(self.artist_check, self.artist_edit)

        self.number_check = QCheckBox("Number the tracks from")
        self.number_spin = QSpinBox()
        self.number_spin.setRange(1, 999)
        self.number_spin.setValue(1)
        self.number_spin.setEnabled(False)
        self.step_spin = QSpinBox()
        self.step_spin.setRange(1, 99)
        self.step_spin.setValue(1)
        self.step_spin.setEnabled(False)
        self.number_check.toggled.connect(self._number_toggled)

        number_widget = QWidget()
        number_layout = QHBoxLayout(number_widget)
        number_layout.setContentsMargins(0, 0, 0, 0)
        number_layout.addWidget(QLabel("First"))
        number_layout.addWidget(self.number_spin)
        number_layout.addWidget(QLabel("Step"))
        number_layout.addWidget(self.step_spin)
        number_layout.addStretch(1)
        form.addRow(self.number_check, number_widget)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Text to find (leave empty to keep titles)")
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Replace with")
        self.regex_check = QCheckBox("Use a regular expression")
        self.case_check = QCheckBox("Match case")
        form.addRow("In the title, find", self.find_edit)
        form.addRow("Replace with", self.replace_edit)
        form.addRow("", self.regex_check)
        form.addRow("", self.case_check)

        pair_form_labels(form)
        layout.addLayout(form)

        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        self.preview_label.setObjectName("muted")
        layout.addWidget(self.preview_label)

        self.write_check = QCheckBox(
            "Write the new details into the files now"
        )
        self.write_check.setToolTip(
            "Rewrites the tags and file names of songs that already have a "
            "file. The audio is not re-encoded and nothing is downloaded."
        )
        layout.addWidget(self.write_check)

        self.footer = DialogFooter(self)
        self.footer.set_primary("Apply to ticked songs", self._accept)

        for widget in (
            self.album_edit, self.artist_edit, self.find_edit,
            self.replace_edit,
        ):
            widget.textChanged.connect(self._update_preview)
        self.regex_check.toggled.connect(self._update_preview)
        self._update_preview()

    # ------------------------------------------------------------- helpers

    def _first_album(self) -> str:
        for row in self.rows:
            if row.track.display_album:
                return row.track.display_album
        return ""

    def _first_artist(self) -> str:
        for row in self.rows:
            if row.track.display_artist:
                return row.track.display_artist
        return ""

    def _number_toggled(self, on: bool) -> None:
        self.number_spin.setEnabled(on)
        self.step_spin.setEnabled(on)
        self._update_preview()

    def _edit(self) -> BulkEdit:
        return BulkEdit(
            album=(
                self.album_edit.text() if self.album_check.isChecked()
                else None
            ),
            artist=(
                self.artist_edit.text() if self.artist_check.isChecked()
                else None
            ),
            track_start=(
                self.number_spin.value()
                if self.number_check.isChecked() else None
            ),
            track_step=self.step_spin.value(),
            find=self.find_edit.text(),
            replace=self.replace_edit.text(),
            use_regex=self.regex_check.isChecked(),
            case_sensitive=self.case_check.isChecked(),
        )

    def _update_preview(self, *_args) -> None:
        edit = self._edit()
        if not edit.has_changes:
            self.preview_label.setText(
                "Nothing chosen yet. Tick a field above to change it."
            )
            self.footer.primary_button.setEnabled(False)
            return

        if edit.find:
            try:
                title_edit(self.rows[0].track.display_title, edit)
            except ValueError as exc:
                self.preview_label.setText(str(exc))
                self.footer.primary_button.setEnabled(False)
                return

        self.footer.primary_button.setEnabled(True)
        from musicdlp.services.bulk_edit import fields_for

        lines = []
        for index, row in enumerate(self.rows[:3]):
            fields = fields_for(row.track, edit, index)
            bits = [
                str(fields.get("title", row.track.display_title)),
                str(fields.get("artist", row.track.display_artist)),
                str(fields.get("album", row.track.display_album)),
            ]
            number = fields.get("track_number", row.track.display_track)
            if number:
                bits.append(f"#{number}")
            lines.append(" — ".join(bit for bit in bits if bit))
        more = (
            f"\n… and {len(self.rows) - 3} more."
            if len(self.rows) > 3 else ""
        )
        self.preview_label.setText(
            "Example after applying:\n" + "\n".join(lines) + more
        )

    def _accept(self) -> None:
        edit = self._edit()
        if not edit.has_changes:
            QMessageBox.information(
                self, "Nothing to change",
                "Tick at least one field to change, then press Apply.",
            )
            return
        if edit.find:
            try:
                title_edit(self.rows[0].track.display_title, edit)
            except ValueError as exc:
                QMessageBox.warning(self, "Pattern not valid", str(exc))
                return
        if edit.album is not None and not edit.album.strip():
            QMessageBox.information(
                self, "Album is empty",
                "Type an album name, or untick “Set the album to”.",
            )
            return
        if edit.artist is not None and not edit.artist.strip():
            QMessageBox.information(
                self, "Artist is empty",
                "Type an artist name, or untick “Set the artist to”.",
            )
            return
        self.result_edit = edit
        self.write_files = self.write_check.isChecked()
        self.accept()