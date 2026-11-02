"""A short, honest list of the keys that work anywhere in the window."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QLabel, QVBoxLayout,
)

SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+N", "Put the cursor in the link box"),
    ("Enter", "Add the link in the box (or search, in a search box)"),
    ("Ctrl+Shift+V", "Add several links at once"),
    ("F5", "Read the selected link again"),
    ("Ctrl+F", "Search every song in the library"),
    ("Escape", "Clear the search and go back to the link you came from"),
    ("Ctrl+A", "Tick every song shown"),
    ("Ctrl+Shift+A", "Tick every song in the library"),
    ("Space", "Tick or untick the highlighted song"),
    ("F2  /  Enter", "Edit the highlighted song's details"),
    ("Delete", "Remove the highlighted songs from the library"),
    ("Ctrl+Z", "Undo the last rename, move, or removal"),
    ("Ctrl+T", "Translate the ticked songs"),
    ("Ctrl+Shift+R", "Rewrite the files of the ticked songs"),
    ("Ctrl+L", "Show or hide the activity log"),
    ("Ctrl+,", "Open Settings"),
    ("Ctrl+Q", "Quit"),
)


class ShortcutsDialog(QDialog):
    """Shows the keyboard shortcuts, in two columns."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "These work anywhere in the main window. Shortcuts for a "
            "single dialog are shown on its buttons."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(8)
        for index, (keys, description) in enumerate(SHORTCUTS):
            column = 0 if index < (len(SHORTCUTS) + 1) // 2 else 2
            row = index if column == 0 else index - (len(SHORTCUTS) + 1) // 2
            key_label = QLabel(keys)
            key_label.setStyleSheet("font-weight: 600;")
            key_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(key_label, row, column)
            grid.addWidget(QLabel(description), row, column + 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
