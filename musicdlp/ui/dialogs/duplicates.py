"""Show songs that look like duplicates, and let the user keep one.

Duplicates come from the same recording being uploaded twice, or from a
song being added through two different links. Media ids differ, so the
groups come from matching names (see Database.find_duplicate_groups).
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from musicdlp.core.models import TrackRow, status_label

# Prefer keeping the copy that is furthest along.
_STATUS_RANK = {
    "synced": 5,
    "converted": 4,
    "downloaded": 3,
    "needs_update": 3,
    "missing": 1,
}


def _rank(row: TrackRow) -> tuple[int, int]:
    """Higher is the better copy to keep."""
    return (_STATUS_RANK.get(row.track.status, 0), -row.track.id)


class DuplicateDialog(QDialog):
    """Groups of duplicate songs; ticked rows are the ones to remove."""

    def __init__(self, groups: Iterable[list[TrackRow]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Duplicate songs")
        self.resize(980, 600)
        self.groups = [list(group) for group in groups]
        self.remove_ids: list[int] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "These songs look like the same recording added more than once. "
            "The best copy is kept by default; tick any other copy you want "
            "removed. Nothing is removed until you press Remove."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(
            ["Song", "From", "Length", "Status", "File"]
        )
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column in (2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(self.tree, 1)

        for group in self.groups:
            self._add_group(group)

        self.count_label = QLabel("")
        self.count_label.setObjectName("muted")
        layout.addWidget(self.count_label)
        self.tree.itemChanged.connect(lambda *_: self._update_count())
        self._update_count()

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.remove_button = buttons.addButton(
            "Remove ticked", QDialogButtonBox.AcceptRole
        )
        self.remove_button.setObjectName("primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ rows

    def _add_group(self, group: list[TrackRow]) -> None:
        group.sort(key=_rank, reverse=True)
        best = group[0]

        top = QTreeWidgetItem([
            best.track.display_title,
            f"{len(group)} copies",
            "",
            "",
            "",
        ])
        top.setFirstColumnSpanned(False)
        top.setFlags(Qt.ItemIsEnabled)
        self.tree.addTopLevelItem(top)

        for row in group:
            track = row.track
            path = ""
            for candidate in (track.mp3_path, track.m4a_path, track.raw_path):
                if candidate:
                    path = candidate
                    break

            child = QTreeWidgetItem([
                f"{track.display_title}",
                row.source.title,
                _length(track.duration),
                status_label(track.status),
                path,
            ])
            child.setFlags(
                Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
                | Qt.ItemIsSelectable
            )
            # The copy that is furthest along stays; the rest are ticked.
            child.setCheckState(
                0, Qt.Unchecked if row is best else Qt.Checked
            )
            child.setData(0, Qt.UserRole, track.id)
            child.setToolTip(0, track.url)
            if row is best:
                child.setToolTip(
                    0, f"{track.url}\nKept by default: this copy is the "
                       "furthest along."
                )
            top.addChild(child)

        top.setExpanded(True)

    # ---------------------------------------------------------------- apply

    def _ticked_ids(self) -> list[int]:
        ids: list[int] = []
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            for child_index in range(top.childCount()):
                child = top.child(child_index)
                if child.checkState(0) == Qt.Checked:
                    track_id = child.data(0, Qt.UserRole)
                    if track_id is not None:
                        ids.append(int(track_id))
        return ids

    def _update_count(self) -> None:
        count = len(self._ticked_ids())
        self.count_label.setText(
            f"{count} song{'' if count == 1 else 's'} will be removed."
            if count else "Nothing will be removed."
        )
        if getattr(self, "remove_button", None) is not None:
            self.remove_button.setEnabled(count > 0)

    def _accept(self) -> None:
        ids = self._ticked_ids()
        if not ids:
            QMessageBox.information(
                self, "Nothing ticked", "Tick the copies you want removed."
            )
            return
        self.remove_ids = ids
        self.accept()


def _length(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"
