"""Keep dialogs inside the screen they open on.

A dialog sized for a 1920-px monitor (the review tables were 1040-1213 px
wide) is wider than a small laptop screen and gets clipped by Windows.
Each dialog asks for a preferred size; this clamps it to the space the
screen actually has, and centres it there.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QDialog

# Leave room for the window frame, the taskbar, and the shadow.
MARGIN = 48


def fit_dialog(dialog: QDialog, width: int, height: int) -> QDialog:
    screen = dialog.screen() or QApplication.primaryScreen()
    if screen is None:
        dialog.resize(width, height)
        return dialog

    available = screen.availableGeometry()
    width = min(width, max(420, available.width() - MARGIN))
    height = min(height, max(320, available.height() - MARGIN))
    dialog.resize(width, height)

    frame = dialog.frameGeometry()
    frame.moveCenter(available.center())
    top_left = frame.topLeft()
    top_left.setX(max(available.left(), top_left.x()))
    top_left.setY(max(available.top(), top_left.y()))
    dialog.move(top_left)
    return dialog