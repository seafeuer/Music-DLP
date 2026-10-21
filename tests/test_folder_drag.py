"""Dragging songs into library folders."""

import logging

import pytest
from PySide6.QtCore import Qt

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.ui.main_window import MainWindow, build_context


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

    # A playlist with two songs, and one song added on its own.
    playlist = db.add_source(
        url="https://www.youtube.com/playlist?list=PLmix",
        kind="playlist", platform="youtube", external_id="PLmix",
        title="A playlist", uploader="Channel", item_count=2,
        thumbnail_url=None,
    )
    playlist_tracks = []
    for index, media_id in enumerate(("p1", "p2"), start=1):
        track_id = db.upsert_track(
            platform="youtube", media_id=media_id,
            url=f"https://www.youtube.com/watch?v={media_id}",
            title=f"Playlist song {index}", uploader="Channel", duration=100,
        )
        playlist_tracks.append(track_id)
    db.set_source_tracks(playlist, [(tid, i + 1)
                                    for i, tid in enumerate(playlist_tracks)])

    single = db.add_source(
        url="https://x.com/user/status/1",
        kind="item", platform="twitter", external_id="1",
        title="A single post", uploader="Poster", item_count=1,
        thumbnail_url=None,
    )
    single_track = db.upsert_track(
        platform="twitter", media_id="1",
        url="https://x.com/user/status/1",
        title="Single song", uploader="Poster", duration=60,
    )
    db.set_source_tracks(single, [(single_track, 1)])

    ctx = build_context(settings, db, logging.getLogger("test"))
    main = MainWindow(ctx)
    qtbot.addWidget(main)
    main.show()
    qtbot.wait(50)
    yield main, db, {
        "playlist": playlist,
        "playlist_tracks": playlist_tracks,
        "single": single,
        "single_track": single_track,
    }
    try:
        db.close()
    except Exception:
        pass


def test_folder_can_be_created_and_appears_in_the_library(window):
    main, db, _ = window
    main._refresh_sources()
    folder_id = db.add_folder("Shiurim")
    main._refresh_sources(folder_id)

    folders = [source for source in db.get_sources() if source.is_folder]
    assert [source.title for source in folders] == ["Shiurim"]
    assert main.sidebar.model.row_for_id(folder_id) is not None


def test_dropping_a_single_song_removes_its_own_entry(window):
    main, db, data = window
    folder_id = db.add_folder("Shiurim")

    main._tracks_dropped(folder_id, [data["single_track"]])

    # The song is in the folder...
    folder_rows = db.get_tracks_for_source(folder_id)
    assert [row.track.id for row in folder_rows] == [data["single_track"]]
    # ...and its one-song library entry is gone.
    assert db.get_source(data["single"]) is None
    assert db.get_track(data["single_track"]) is not None


def test_dropping_a_playlist_song_keeps_the_playlist(window):
    main, db, data = window
    folder_id = db.add_folder("Shiurim")

    main._tracks_dropped(folder_id, [data["playlist_tracks"][0]])

    assert db.get_source(data["playlist"]) is not None
    remaining = [row.track.id for row in db.get_tracks_for_source(
        data["playlist"]
    )]
    assert data["playlist_tracks"][0] not in remaining
    assert data["playlist_tracks"][1] in remaining
    assert db.get_source(data["playlist"]).item_count == 1


def test_dragging_several_songs_at_once(window):
    main, db, data = window
    folder_id = db.add_folder("Shiurim")
    ids = data["playlist_tracks"] + [data["single_track"]]

    moved = db.move_tracks_to_folder(ids, folder_id)

    assert moved == 3
    assert len(db.get_tracks_for_source(folder_id)) == 3
    assert db.get_source(data["single"]) is None


def test_folder_is_selectable_and_lists_its_songs(window):
    main, db, data = window
    folder_id = db.add_folder("Shiurim")
    db.move_tracks_to_folder([data["single_track"]], folder_id)

    main._refresh_sources(folder_id)
    assert main._current_source_id == folder_id
    assert main.track_table.model.rowCount() == 1
    assert main.track_table.model.rows[0].track.id == data["single_track"]


def test_dragging_into_a_folder_twice_does_not_duplicate(window):
    main, db, data = window
    folder_id = db.add_folder("Shiurim")
    db.move_tracks_to_folder([data["single_track"]], folder_id)
    db.move_tracks_to_folder([data["single_track"]], folder_id)

    assert len(db.get_tracks_for_source(folder_id)) == 1


def test_deleting_a_folder_keeps_its_songs(window):
    main, db, data = window
    folder_id = db.add_folder("Shiurim")
    db.move_tracks_to_folder([data["single_track"]], folder_id)

    db.delete_folder(folder_id)

    assert db.get_source(folder_id) is None
    assert db.get_track(data["single_track"]) is not None


def test_links_naming_a_folder_get_a_folder_entry(window):
    main, db, data = window
    db.set_source_folder(data["playlist"], "Shiurim")
    main._refresh_sources()

    folders = [source.title for source in db.get_sources() if source.is_folder]
    assert "Shiurim" in folders


def test_folder_rows_are_marked_for_the_view(window):
    main, db, data = window
    folder_id = db.add_folder("Shiurim")
    db.set_source_folder(data["playlist"], "Shiurim")
    main._refresh_sources(folder_id)

    entries = main.sidebar.model.rows
    folder_rows = [
        entry for entry in entries
        if entry[0] == "source" and entry[1].is_folder
    ]
    assert folder_rows
    # The link assigned to the folder is shown indented beneath it.
    indented = [entry for entry in entries if entry[0] == "source" and entry[3]]
    assert indented
