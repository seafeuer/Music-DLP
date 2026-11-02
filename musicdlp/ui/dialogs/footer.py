"""One footer for every dialog.

Every dialog used to lay its buttons out with ``QDialogButtonBox``, and
Windows orders those as [Save] [Cancel] — the primary action on the left,
where a right-handed user's pointer is not. The order is also different
from the one place to the next: some dialogs put their "run" action first,
some last, and the review dialogs mixed a run button in with Apply.

This footer fixes the rule in one place: actions that do something inside
the dialog sit on the left, then a stretch, then Cancel, then the primary
button at the right edge. Escape always cancels, Return always presses the
primary button.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class DialogFooter(QWidget):
    def __init__(
        self,
        dialog,
        *,
        cancel_text: str = "Cancel",
        on_cancel: Optional[Callable[[], None]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent or dialog)
        self.dialog = dialog
        self._cancel_handler = on_cancel
        self._slot: Optional[Callable[[], None]] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.cancel_button = QPushButton(cancel_text)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(False)
        self.cancel_button.clicked.connect(self.cancel)

        self.primary_button = QPushButton("")
        self.primary_button.setObjectName("primary")
        self.primary_button.setDefault(True)

        layout.addWidget(self.cancel_button)
        layout.addWidget(self.primary_button)
        self._stretch = layout.count() - 2
        layout.insertStretch(self._stretch, 1)

    # ------------------------------------------------------------------ build

    def add_action(
        self, text: str, slot: Optional[Callable[[], None]] = None
    ) -> QPushButton:
        """A button that does something without closing the dialog.

        It goes to the left of the stretch, so the eye still lands on the
        primary action at the right edge.
        """
        button = QPushButton(text)
        button.setAutoDefault(False)
        button.setDefault(False)
        if slot is not None:
            button.clicked.connect(lambda _checked=False: slot())
        self.layout().insertWidget(self._stretch, button)
        self._stretch += 1
        return button

    def set_primary(
        self,
        text: str,
        slot: Optional[Callable[[], None]] = None,
    ) -> QPushButton:
        self.primary_button.setText(text)
        self.primary_button.setAutoDefault(True)
        self.primary_button.setDefault(True)
        if slot is not None and self._slot is not slot:
            # Re-pointing the button must not leave it connected to the
            # previous handler. Qt warns when there is nothing to
            # disconnect, so only do it once a slot is actually set.
            if self._slot is not None:
                try:
                    self.primary_button.clicked.disconnect(self._slot)
                except (RuntimeError, TypeError):
                    pass
            self._slot = slot
            self.primary_button.clicked.connect(slot)
        return self.primary_button

    def hide_cancel(self) -> None:
        self.cancel_button.hide()

    # ---------------------------------------------------------------- actions

    def cancel(self) -> None:
        if self._cancel_handler is not None:
            self._cancel_handler()
            return
        self.dialog.reject()

    def accept(self) -> None:
        self.dialog.accept()
