"""Long names are shortened, never drawn over their neighbours."""

from __future__ import annotations

from PySide6.QtCore import Qt

from musicdlp.ui.widgets.elided_label import ElidedLabel


def test_a_long_text_is_elided_and_kept_in_the_tooltip(qapp):
    label = ElidedLabel("x" * 400)
    label.resize(120, 24)
    label.show()
    qapp.processEvents()

    assert label.text().endswith("…")
    assert len(label.text()) < 400
    assert label.toolTip() == "x" * 400
    assert label.full_text() == "x" * 400


def test_a_short_text_is_shown_in_full(qapp):
    label = ElidedLabel("Short")
    label.resize(300, 24)
    label.show()
    qapp.processEvents()
    assert label.text() == "Short"


def test_setting_new_text_updates_the_tooltip(qapp):
    label = ElidedLabel("First")
    label.resize(300, 24)
    label.setText("Second")
    assert label.toolTip() == "Second"
    assert label.text() == "Second"
