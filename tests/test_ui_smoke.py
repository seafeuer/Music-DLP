"""Offscreen smoke test of the main window."""

import logging

import pytest
from PySide6.QtCore import Qt

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.ui.main_window import MainWindow, build_context
from musicdlp.ui.theme import apply_theme


@pytest.fixture()
def window(qtbot, qapp, tmp_path, monkeypatch):
    # Never write the real user config from a test.
    monkeypatch.setattr(
        Settings, "save", lambda self, path=None: None
    )

    settings = Settings(
        theme="dark",
        mode="easy",
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
        first_run_done=True,
        notifications=False,
    )
    db = Database(tmp_path / "library.db")
    source_id = db.add_source(
        url="https://www.youtube.com/playlist?list=PLtest",
        kind="playlist",
        platform="youtube",
        external_id="PLtest",
        title="Test playlist",
        uploader="Test channel",
        item_count=2,
        thumbnail_url=None,
    )
    ids = []
    for index, media_id in enumerate(("vid1", "vid2"), start=1):
        ids.append(
            db.upsert_track(
                platform="youtube",
                media_id=media_id,
                url=f"https://www.youtube.com/watch?v={media_id}",
                title=f"Song {index}",
                uploader="Test channel",
                duration=200,
            )
        )
    db.set_source_tracks(source_id, [(ids[0], 1), (ids[1], 2)])

    logger = logging.getLogger("musicdlp.test")
    logger.setLevel(logging.INFO)
    apply_theme(qapp, settings.theme)

    ctx = build_context(settings, db, logger)
    main = MainWindow(ctx)
    qtbot.addWidget(main)
    main.show()
    qtbot.wait(100)
    yield main, db, ids
    try:
        db.close()
    except Exception:
        pass


def test_rows_and_default_selection(window):
    main, db, ids = window
    assert main.track_table.model.rowCount() == 2
    assert main.track_table.checked_count() == 2
    assert main.settings.mode == "easy"
    assert main.action_layout.count() == 1
    # The details panel is visible in both modes so editing stays discoverable.
    assert not main.inspector_scroll.isHidden()


def test_pro_mode_and_inspector(window):
    main, _db, _ids = window
    main.mode_combo.setCurrentIndex(1)
    assert main.settings.mode == "pro"
    assert not main.inspector_scroll.isHidden()
    assert main.action_layout.count() == 3

    main.track_table.table.selectRow(0)
    assert main.inspector.title_label.text() == "Song 1"
    assert main.inspector.artist_label.text() == "Test channel"
    assert main.track_table.edit_button.isEnabled()


def test_progress_filter_and_search(window):
    main, _db, ids = window
    main._on_progress({
        "stage": "download",
        "percent": 50,
        "item_percent": 50,
        "title": "Song 1",
        "speed": 1024 * 1024,
        "eta": 65,
        "track_id": ids[0],
    })
    assert main.track_table.model.progress[ids[0]] == 50
    assert "MB/s" in main.status.detail_label.text()

    main.track_table.filter_combo.setCurrentIndex(2)  # Downloaded
    assert main.track_table.proxy.rowCount() == 0
    main.track_table.filter_combo.setCurrentIndex(0)
    assert main.track_table.proxy.rowCount() == 2

    main.track_table.search.setText("song 2")
    assert main.track_table.proxy.rowCount() == 1
    main.track_table.search.clear()


def test_metadata_edit(window):
    main, db, ids = window
    main._apply_metadata(ids[0], "Renamed", "New Artist", "Album", 3)
    track = db.get_track(ids[0])
    assert track.meta_title == "Renamed"
    assert track.meta_artist == "New Artist"
    assert track.meta_album == "Album"
    assert track.meta_track == 3


def test_checkbox_click_keeps_the_window_working(window):
    """Regression: checking a row used to raise and freeze the run."""
    main, _db, ids = window
    model = main.track_table.model
    index = model.index(model.row_index_for_id(ids[0]), 0)

    assert model.setData(index, Qt.Checked, Qt.CheckStateRole)
    assert ids[0] in model.checked
    assert model.setData(index, Qt.Unchecked, Qt.CheckStateRole)
    assert ids[0] not in model.checked

    # The row is still selectable and the details panel follows it.
    main.track_table._select_by_track_id(ids[1])
    assert main.track_table.current_row().track.id == ids[1]
    assert main.inspector.title_label.text() == "Song 2"


def test_inspector_include_checkbox_mirrors_the_list(window):
    main, _db, ids = window
    main.track_table._select_by_track_id(ids[0])
    assert main.inspector.include_check.isChecked()

    main.inspector.include_check.setChecked(False)
    assert ids[0] not in main.track_table.model.checked

    main.inspector.include_check.setChecked(True)
    assert ids[0] in main.track_table.model.checked


def test_selection_survives_a_reload(window):
    main, _db, ids = window
    main.track_table._select_by_track_id(ids[1])
    main._reload_current_source()
    row = main.track_table.current_row()
    assert row is not None
    assert row.track.id == ids[1]
    assert main.inspector.title_label.text() == "Song 2"


def test_reconvert_on_synced_track_includes_sync(window):
    main, db, ids = window
    db.update_track(
        ids[0],
        status="synced",
        synced_at="2026-01-01T00:00:00",
        mp3_path=str(main.settings.output_path / "MP3" / "Song 1.mp3"),
    )
    main._reload_current_source()

    captured = {}
    original = main._start_pipeline

    def fake_start(rows, stages, **kwargs):
        captured["stages"] = stages
        captured["rows"] = rows

    main._start_pipeline = fake_start
    try:
        main._reconvert_track(ids[0])
    finally:
        main._start_pipeline = original

    assert captured["stages"][-1] == "sync", captured

