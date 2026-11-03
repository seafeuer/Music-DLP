"""Keyboard behaviour: Escape, Space, Delete, and multi-song removal."""

from __future__ import annotations

import logging

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.ui.main_window import MainWindow, build_context
from musicdlp.ui.widgets.search_box import SearchBox


@pytest.fixture()
def window(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    settings = Settings(
        theme="dark", mode="easy", output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"), first_run_done=True,
        notifications=False, smart_titles=False,
    )
    db = Database(tmp_path / "library.db")
    source = db.add_source(
        url="https://www.youtube.com/playlist?list=PLk",
        kind="playlist", platform="youtube", external_id="PLk",
        title="Keys", uploader="Channel", item_count=3, thumbnail_url=None,
    )
    ids = []
    for index in range(3):
        ids.append(db.upsert_track(
            platform="youtube", media_id=f"k{index}",
            url=f"https://www.youtube.com/watch?v=k{index}",
            title=f"Song {index}", uploader="Channel", duration=100,
        ))
    db.set_source_tracks(source, [(tid, i + 1) for i, tid in enumerate(ids)])
    ctx = build_context(settings, db, logging.getLogger("test"))
    main = MainWindow(ctx)
    qtbot.addWidget(main)
    main.show()
    qtbot.wait(50)
    yield main, db, {"source": source, "tracks": ids}
    try:
        db.close()
    except Exception:
        pass


def test_escape_clears_a_search_box(qtbot):
    box = SearchBox("Search")
    box.setText("drake")
    qtbot.keyClick(box, Qt.Key_Escape)
    assert box.text() == ""


def test_space_ticks_the_highlighted_song(window, qtbot):
    main, _db, data = window
    main._load_source(data["source"])
    main.track_table.model.clear_checks()
    main.track_table._select_by_track_id(data["tracks"][0])
    qtbot.keyClick(main.track_table.table, Qt.Key_Space)
    assert data["tracks"][0] in main.track_table.model.checked
    qtbot.keyClick(main.track_table.table, Qt.Key_Space)
    assert data["tracks"][0] not in main.track_table.model.checked


def test_delete_asks_once_for_several_songs(window, monkeypatch):
    main, db, data = window
    main._load_source(data["source"])
    main.track_table.table.selectAll()

    asked = []

    def answer(*args, **kwargs):
        asked.append(args)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(answer))
    main.track_table._remove_selected()

    assert len(asked) == 1
    assert all(db.get_track(tid) is None for tid in data["tracks"])


def test_one_undo_brings_back_a_multi_song_removal(window, monkeypatch):
    main, db, data = window
    main._load_source(data["source"])
    main.track_table.table.selectAll()
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.No),
    )

    main.track_table._remove_selected()
    main._undo_last()

    rows = db.get_tracks_for_source(data["source"])
    assert [row.track.id for row in rows] == data["tracks"]


def test_the_shortcuts_dialog_lists_what_is_actually_bound(window):
    from musicdlp.ui.dialogs.shortcuts import SHORTCUTS

    main, _db, _data = window
    text = " ".join(keys for keys, _description in SHORTCUTS)
    for bound in ("F2", "Ctrl+Z", "Ctrl+F", "Ctrl+T", "Ctrl+Shift+A"):
        assert bound in text


def test_return_on_a_row_opens_the_details_dialog(window, monkeypatch, qtbot):
    from PySide6.QtCore import QModelIndex

    main, _db, data = window
    main._load_source(data["source"])
    main.track_table._select_by_track_id(data["tracks"][0])
    opened = []
    monkeypatch.setattr(main, "_open_details", opened.append)

    qtbot.keyClick(main.track_table.table, Qt.Key_Return)

    assert opened == [data["tracks"][0]]


def test_escape_clears_the_library_search(window):
    main, _db, _data = window
    main.search_edit.setText("drake")
    assert main.search_edit.text() == "drake"

    main._on_escape()

    assert main.search_edit.text() == ""


def test_escape_closes_the_log_drawer(window):
    main, _db, _data = window
    main.logs_action.setChecked(True)
    assert main.log_drawer.isVisible()

    main._on_escape()

    assert not main.log_drawer.isVisible()
