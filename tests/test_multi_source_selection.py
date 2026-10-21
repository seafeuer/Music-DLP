"""Selecting songs across several links and shipping them together."""

import logging

import pytest
from PySide6.QtCore import Qt

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.models import ALL_SONGS_ID
from musicdlp.ui.main_window import MainWindow, build_context


def _add_source(db, index, title, track_ids):
    source_id = db.add_source(
        url=f"https://www.youtube.com/playlist?list=PL{index}",
        kind="playlist",
        platform="youtube",
        external_id=f"PL{index}",
        title=title,
        uploader=f"Channel {index}",
        item_count=len(track_ids),
        thumbnail_url=None,
    )
    db.set_source_tracks(
        source_id, [(track_id, position + 1)
                    for position, track_id in enumerate(track_ids)]
    )
    return source_id


@pytest.fixture()
def window(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    settings = Settings(
        theme="dark",
        mode="easy",
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
        first_run_done=True,
        notifications=False,
        smart_titles=False,
    )
    db = Database(tmp_path / "library.db")

    ids = []
    for index in range(1, 4):
        track_ids = []
        for n in range(2):
            track_id = db.upsert_track(
                platform="youtube",
                media_id=f"v{index}{n}",
                url=f"https://www.youtube.com/watch?v=v{index}{n}",
                title=f"Song {index}-{n}",
                uploader=f"Channel {index}",
                duration=100,
            )
            track_ids.append(track_id)
        ids.extend(track_ids)
        _add_source(db, index, f"Link {index}", track_ids)

    ctx = build_context(settings, db, logging.getLogger("test"))
    main = MainWindow(ctx)
    qtbot.addWidget(main)
    main.show()
    qtbot.wait(50)
    yield main, db, ids
    try:
        db.close()
    except Exception:
        pass


def test_all_songs_lists_every_link(window):
    main, _db, ids = window
    main._refresh_sources(ALL_SONGS_ID)
    assert main._current_source_id == ALL_SONGS_ID
    assert main.track_table.model.rowCount() == len(ids)


def test_selection_survives_switching_links(window):
    main, db, ids = window
    sources = db.get_sources()
    main._load_source(sources[0].id)
    main.track_table.model.check_all()
    assert len(main._checked) == 2

    main._load_source(sources[1].id)
    main.track_table.model.check_all()
    assert len(main._checked) == 4

    # Going back shows the first link's songs still ticked.
    main._load_source(sources[0].id)
    assert main.track_table.checked_count() == 2


def test_shipping_sends_songs_from_every_link(window, monkeypatch):
    main, db, _ids = window
    sources = db.get_sources()
    for source in sources:
        main._load_source(source.id)
        main.track_table.model.check_all()

    captured = {}

    def fake_start(rows, stages, **kwargs):
        captured["rows"] = rows
        captured["stages"] = stages
        return True

    main._start_pipeline = fake_start
    main._start_easy()

    assert len(captured["rows"]) == 6
    assert captured["stages"] == ["download", "convert", "sync"]
    # Every row keeps its own link, which is used for album naming.
    assert len({row.source.id for row in captured["rows"]}) == 3


def test_all_songs_view_can_ship_everything(window, monkeypatch):
    main, _db, _ids = window
    main._refresh_sources(ALL_SONGS_ID)
    main.track_table.model.check_all()
    assert len(main._checked) == 6

    captured = {}
    main._start_pipeline = lambda rows, stages, **kw: captured.setdefault(
        "rows", rows
    )
    main._start_easy()
    assert len(captured["rows"]) == 6


def test_select_every_song_from_a_single_link_view(window):
    main, db, _ids = window
    main._load_source(db.get_sources()[0].id)
    main._select_every_song()
    assert len(main._checked) == 6
    assert main.track_table.checked_count() == 2


def test_action_button_counts_the_whole_selection(window):
    main, db, _ids = window
    main._load_source(db.get_sources()[0].id)
    main.track_table.model.check_all()
    main._load_source(db.get_sources()[1].id)
    main.track_table.model.check_all()

    button = main.action_layout.itemAt(0).widget()
    assert "(4)" in button.text(), button.text()


def test_unchecking_one_link_does_not_clear_others(window):
    main, db, ids = window
    sources = db.get_sources()  # newest first
    main._load_source(sources[0].id)
    main.track_table.model.check_all()
    main._load_source(sources[1].id)
    main.track_table.model.check_all()

    main.track_table.model.clear_checks()
    assert len(main._checked) == 2

    # The remaining ticks belong to the other link, not the one just cleared.
    remaining = {row.track.id for row in db.get_tracks_for_source(sources[0].id)}
    assert main._checked == remaining

