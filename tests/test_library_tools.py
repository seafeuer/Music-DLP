"""Duplicates, catalogue lookup, undo, album numbering, search, templates."""

import logging

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from musicdlp.core.config import Settings
from musicdlp.core.db import Database, _match_key
from musicdlp.core.models import Source
from musicdlp.services import metadata_lookup
from musicdlp.services.metadata_lookup import Lookup, lookup
from musicdlp.ui.dialogs.duplicates import DuplicateDialog
from musicdlp.ui.main_window import MainWindow, build_context


# ------------------------------------------------------------------ fixtures


@pytest.fixture()
def library(tmp_path):
    db = Database(tmp_path / "library.db")
    source_id = db.add_source(
        url="https://www.youtube.com/playlist?list=PLone",
        kind="playlist", platform="youtube", external_id="PLone",
        title="First list", uploader="Channel", item_count=2,
        thumbnail_url=None,
    )
    ids = []
    for index, (media_id, title) in enumerate(
        [("a1", "Same Song"), ("a2", "Other Song")], start=1
    ):
        ids.append(db.upsert_track(
            platform="youtube", media_id=media_id,
            url=f"https://www.youtube.com/watch?v={media_id}",
            title=title, uploader="Channel", duration=180,
        ))
    db.set_source_tracks(source_id, [(tid, i + 1) for i, tid in enumerate(ids)])

    other = db.add_source(
        url="https://www.youtube.com/watch?v=dup1",
        kind="item", platform="youtube", external_id="dup1",
        title="A single", uploader="Uploader", item_count=1,
        thumbnail_url=None,
    )
    duplicate = db.upsert_track(
        platform="youtube", media_id="dup1",
        url="https://www.youtube.com/watch?v=dup1",
        title="Same Song (Official Video)", uploader="Uploader", duration=181,
    )
    db.set_source_tracks(other, [(duplicate, 1)])
    return db, {"source": source_id, "tracks": ids, "duplicate": duplicate}


@pytest.fixture()
def window(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    settings = Settings(
        theme="dark", mode="easy", output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"), first_run_done=True,
        notifications=False,
    )
    db = Database(tmp_path / "library.db")
    playlist = db.add_source(
        url="https://www.youtube.com/playlist?list=PLmix",
        kind="playlist", platform="youtube", external_id="PLmix",
        title="A playlist", uploader="Channel", item_count=2,
        thumbnail_url=None,
    )
    ids = []
    for index, media_id in enumerate(("p1", "p2"), start=1):
        ids.append(db.upsert_track(
            platform="youtube", media_id=media_id,
            url=f"https://www.youtube.com/watch?v={media_id}",
            title=f"Playlist song {index}", uploader="Channel", duration=100,
        ))
    db.set_source_tracks(playlist, [(tid, i + 1) for i, tid in enumerate(ids)])

    ctx = build_context(settings, db, logging.getLogger("test"))
    main = MainWindow(ctx)
    qtbot.addWidget(main)
    main.show()
    qtbot.wait(50)
    yield main, db, {"playlist": playlist, "tracks": ids}
    try:
        db.close()
    except Exception:
        pass


# ---------------------------------------------------------------- duplicates


def test_match_key_ignores_case_and_punctuation():
    assert _match_key("Same Song (Official Video)") == "same song official video"
    assert _match_key("  Same   SONG  ") == "same song"


def test_duplicate_groups_find_the_same_name_twice(library):
    db, data = library
    db.update_track(data["duplicate"], meta_title="Same Song")

    groups = db.find_duplicate_groups()

    assert len(groups) == 1
    ids = {row.track.id for row in groups[0]}
    assert data["tracks"][0] in ids
    assert data["duplicate"] in ids


def test_a_unique_library_has_no_groups(library):
    db, _data = library
    assert db.find_duplicate_groups() == []


def test_same_title_with_a_very_different_length_is_not_a_duplicate(library):
    db, data = library
    db.update_track(data["duplicate"], meta_title="Same Song", duration=400)
    assert db.find_duplicate_groups() == []


def test_same_artist_and_title_groups_even_when_lengths_differ(library):
    db, data = library
    db.update_track(
        data["duplicate"], meta_title="Same Song",
        meta_artist="Channel", duration=400,
    )
    groups = db.find_duplicate_groups()
    assert len(groups) == 1


def test_duplicate_dialog_keeps_the_best_copy(qapp, library):
    db, data = library
    db.update_track(data["duplicate"], meta_title="Same Song")
    db.update_track(data["tracks"][0], status="synced")
    groups = db.find_duplicate_groups()

    dialog = DuplicateDialog(groups)
    ticked = dialog._ticked_ids()

    # The synced copy is kept; the other is ticked for removal.
    assert data["duplicate"] in ticked
    assert data["tracks"][0] not in ticked


def test_duplicate_dialog_reports_the_count(qapp, library):
    db, data = library
    db.update_track(data["duplicate"], meta_title="Same Song")
    dialog = DuplicateDialog(db.find_duplicate_groups())
    assert "1 song will be removed" in dialog.count_label.text()


def test_duplicate_dialog_returns_the_ticked_ids(qapp, library):
    db, data = library
    db.update_track(data["duplicate"], meta_title="Same Song")
    dialog = DuplicateDialog(db.find_duplicate_groups())
    dialog._accept()
    assert dialog.remove_ids == [data["duplicate"]]


# -------------------------------------------------------------------- lookup


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def test_lookup_reads_the_release_and_track_number(monkeypatch):
    payload = {
        "recordings": [{
            "title": "Same Song",
            "score": 100,
            "artist-credit": [{"name": "The Band"}],
            "releases": [{
                "id": "rel-1",
                "title": "Great Album",
                "date": "2004-05-01",
                "media": [{"track-offset": 2, "track-count": 10}],
            }],
        }]
    }
    monkeypatch.setattr(
        metadata_lookup.requests, "get",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    monkeypatch.setattr(metadata_lookup, "_wait_for_turn", lambda: None)

    found = lookup("The Band", "Same Song")

    assert isinstance(found, Lookup)
    assert found.album == "Great Album"
    assert found.track == 3
    assert found.year == "2004"
    assert found.artist == "The Band"


def test_lookup_prefers_the_album_the_user_named(monkeypatch):
    payload = {
        "recordings": [{
            "title": "Same Song",
            "artist-credit": [{"name": "The Band"}],
            "releases": [
                {"title": "Greatest Hits", "date": "2010",
                 "media": [{"track-offset": 0}]},
                {"title": "Great Album", "date": "2004",
                 "media": [{"track-offset": 7}]},
            ],
        }]
    }
    monkeypatch.setattr(
        metadata_lookup.requests, "get",
        lambda *args, **kwargs: FakeResponse(payload),
    )
    monkeypatch.setattr(metadata_lookup, "_wait_for_turn", lambda: None)

    found = lookup("The Band", "Same Song", album="Great Album")
    assert found.album == "Great Album"
    assert found.track == 8


def test_lookup_returns_none_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(
        metadata_lookup.requests, "get",
        lambda *args, **kwargs: FakeResponse({"recordings": []}),
    )
    monkeypatch.setattr(metadata_lookup, "_wait_for_turn", lambda: None)
    assert lookup("Nobody", "Nothing") is None


def test_lookup_survives_a_network_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise metadata_lookup.requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(metadata_lookup.requests, "get", boom)
    monkeypatch.setattr(metadata_lookup, "_wait_for_turn", lambda: None)
    assert lookup("The Band", "Same Song") is None


# ---------------------------------------------------------------------- undo


def test_undo_restores_a_folder_move(window, monkeypatch):
    main, db, data = window
    folder_id = db.add_folder("Shiurim")
    monkeypatch.setattr(main, "_ask_move_or_keep", lambda _title: "move")

    main._tracks_dropped(folder_id, [data["tracks"][0]])
    assert len(db.get_tracks_for_source(folder_id)) == 1

    main._undo_last()

    assert db.get_tracks_for_source(folder_id) == []
    remaining = [row.track.id for row in db.get_tracks_for_source(data["playlist"])]
    assert data["tracks"][0] in remaining


def test_undo_restores_metadata(window):
    main, db, data = window
    track_id = data["tracks"][0]
    main._push_metadata_undo("Renaming songs", [track_id])
    main.ctx.library.set_metadata(track_id, title="A new name")

    assert db.get_track(track_id).meta_title == "A new name"

    main._undo_last()
    assert db.get_track(track_id).meta_title is None


def test_undo_removes_a_new_folder(window):
    main, db, _data = window
    folder_id = db.add_folder("Temporary")
    main._push_undo("Creating a folder", lambda: db.delete_folder(folder_id))

    main._undo_last()

    assert db.get_source(folder_id) is None


def test_undo_brings_back_a_one_song_link(window):
    """Moving a single-song link's song out deletes the link; undo restores it."""
    main, db, _data = window
    single = db.add_source(
        url="https://x.com/user/status/1", kind="item", platform="twitter",
        external_id="1", title="A single post", uploader="Poster",
        item_count=1, thumbnail_url=None,
    )
    track_id = db.upsert_track(
        platform="twitter", media_id="1", url="https://x.com/user/status/1",
        title="Single song", uploader="Poster", duration=60,
    )
    db.set_source_tracks(single, [(track_id, 1)])
    folder_id = db.add_folder("Favourites")

    main._tracks_dropped(folder_id, [track_id])
    assert db.get_source(single) is None

    main._undo_last()

    restored = db.get_source(single)
    assert restored is not None
    assert [row.track.id for row in db.get_tracks_for_source(single)] == [track_id]


def test_undo_with_nothing_to_undo_says_so(window):
    main, _db, _data = window
    main._undo_last()
    assert "Nothing to undo" in main.status.status_label.text()


# ------------------------------------------------------------------- albums


def test_number_as_album_sets_album_and_track_numbers(window, monkeypatch):
    main, db, data = window
    for track_id in data["tracks"]:
        main._checked.add(track_id)

    monkeypatch.setattr(
        QInputDialog, "getText",
        staticmethod(lambda *args, **kwargs: ("Greatest Hits", True)),
    )
    monkeypatch.setattr(
        QInputDialog, "getInt",
        staticmethod(lambda *args, **kwargs: (1, True)),
    )

    main._number_as_album()

    first = db.get_track(data["tracks"][0])
    second = db.get_track(data["tracks"][1])
    assert first.display_album == "Greatest Hits"
    assert second.display_album == "Greatest Hits"
    assert {first.meta_track, second.meta_track} == {1, 2}


def test_number_as_album_does_nothing_when_cancelled(window, monkeypatch):
    main, db, data = window
    for track_id in data["tracks"]:
        main._checked.add(track_id)
    monkeypatch.setattr(
        QInputDialog, "getText",
        staticmethod(lambda *args, **kwargs: ("", False)),
    )

    main._number_as_album()

    assert db.get_track(data["tracks"][0]).meta_album is None


# ------------------------------------------------------------------- search


def test_typing_in_the_search_box_switches_to_all_songs(window, qtbot):
    main, _db, data = window
    main._load_source(data["playlist"])
    assert main._current_source_id == data["playlist"]

    main.search_edit.setText("playlist song 1")
    qtbot.wait(30)

    assert main._current_source_id == 0  # the all-songs view
    assert main.track_table.search.text() == "playlist song 1"


def test_clearing_the_search_returns_to_the_previous_link(window, qtbot):
    main, _db, data = window
    main._load_source(data["playlist"])
    main.search_edit.setText("song")
    qtbot.wait(30)

    main.search_edit.clear()
    qtbot.wait(30)

    assert main._current_source_id == data["playlist"]


def test_opening_a_link_clears_the_library_search(window, qtbot):
    main, _db, data = window
    main.search_edit.setText("song")
    qtbot.wait(30)

    main._load_source(data["playlist"])

    assert main.search_edit.text() == ""


# ---------------------------------------------------------------- templates


def test_folder_template_is_stored_and_read_back(window):
    main, db, data = window
    folder_id = db.add_folder("Shiurim")
    db.update_source(folder_id, filename_template="{track:02d} - {title}")

    folder = db.get_folder_by_name("Shiurim")
    assert folder.filename_template == "{track:02d} - {title}"


def test_pipeline_uses_the_folder_template(window):
    main, db, data = window
    folder_id = db.add_folder("Shiurim")
    db.update_source(folder_id, filename_template="{track:02d} - {title}")
    db.set_source_folder(data["playlist"], "Shiurim")

    source = db.get_source(data["playlist"])
    template = main.ctx.pipeline._filename_template(source, main.settings)
    assert template == "{track:02d} - {title}"


def test_pipeline_falls_back_to_the_global_template(window):
    main, db, data = window
    source = db.get_source(data["playlist"])
    template = main.ctx.pipeline._filename_template(source, main.settings)
    assert template == main.settings.filename_template


# ------------------------------------------------------------------ actions


def test_the_new_actions_exist_and_start_disabled_where_right(window):
    main, _db, _data = window
    assert main.undo_action.isEnabled() is False
    assert main.duplicates_action.isEnabled() is True
    assert main.album_action.isEnabled() is False  # nothing ticked
    assert main.translate_checked_action.isEnabled() is False


def test_ticking_a_song_enables_the_batch_actions(window):
    main, _db, data = window
    main._checked.add(data["tracks"][0])
    main._update_actions()

    assert main.album_action.isEnabled() is True
    assert main.translate_checked_action.isEnabled() is True
    assert main.update_files_action.isEnabled() is True
    assert "1" in main.translate_checked_action.text()


def test_update_files_reports_when_nothing_changed(window):
    main, _db, data = window
    main._checked.add(data["tracks"][0])

    main._update_files()

    assert "Nothing to update" in main.status.status_label.text()


# ------------------------------------------------------- translation wiring


def test_applying_a_translation_without_an_artist_keeps_the_artist(window):
    """Regression: an empty artist cell used to wipe meta_artist."""
    main, db, data = window
    track_id = data["tracks"][0]
    db.update_track(track_id, meta_artist="The Band")

    main._apply_translations([(track_id, "夜に駆ける", "")])

    track = db.get_track(track_id)
    assert track.display_title == "夜に駆ける"
    assert track.meta_artist == "The Band"


def test_applying_a_translation_writes_the_new_artist(window):
    main, db, data = window
    track_id = data["tracks"][0]

    main._apply_translations([(track_id, "夜に駆ける", "ヨアソビ")])

    assert db.get_track(track_id).meta_artist == "ヨアソビ"


def test_translation_dialog_opens_with_the_saved_choices(qapp, window):
    from musicdlp.ui.dialogs.translate import TranslationDialog

    main, db, data = window
    rows = db.get_tracks_for_source(data["playlist"])
    dialog = TranslationDialog(
        rows, language="Hebrew", style="transliterated",
        translate_artist=False,
    )
    assert dialog.language_combo.currentText() == "Hebrew"
    assert dialog.style_combo.currentData() == "transliterated"
    assert dialog.artist_check.isChecked() is False


# --------------------------------------------------------------- settings


def test_anthropic_is_selectable_in_settings(qapp, window):
    from musicdlp.ui.dialogs.settings import SettingsDialog

    main, _db, _data = window
    dialog = SettingsDialog(
        Settings(ai_provider="anthropic", ai_base_url="https://example.test",
                 ai_model="m"),
        main.ctx.ffmpeg, main.ctx.ytdlp, main.ctx.spotify,
    )
    items = [
        dialog.ai_provider_combo.itemData(i)
        for i in range(dialog.ai_provider_combo.count())
    ]
    assert ("anthropic", "") in items
    assert dialog._ai_provider_index() == 3


# ------------------------------------------------------------------- search


def test_a_reload_keeps_the_library_search_applied(window, qtbot):
    main, _db, _data = window
    main.search_edit.setText("song")
    qtbot.wait(30)

    main._load_source(0)  # what a refresh or an undo does

    assert main.track_table.search.text() == "song"


# --------------------------------------------------------------- duplicates


def test_removing_a_duplicate_forgets_the_row(library):
    from musicdlp.services.library import LibraryService

    db, data = library
    service = LibraryService(db, Settings(), ytdlp=None)
    service.delete_track(data["duplicate"], forget=True)

    assert db.get_track(data["duplicate"]) is None


def test_a_removed_duplicate_can_be_put_back(window):
    main, db, data = window
    track_id = data["tracks"][0]
    track = db.get_track(track_id)
    links = db.track_links(track_id)

    main.ctx.library.delete_track(track_id, forget=True)
    assert db.get_track(track_id) is None

    main._restore_tracks([(track, links)])

    assert db.get_track(track_id) is not None
    assert [row.track.id for row in db.get_tracks_for_source(
        data["playlist"]
    )] == data["tracks"]


def test_undoing_a_rename_brings_back_the_suggestion(window):
    main, db, data = window
    track_id = data["tracks"][0]
    db.update_track(
        track_id, smart_title="Suggested", smart_artist="Artist",
        smart_confidence=0.5, smart_reason="rule",
    )
    main._push_metadata_undo("Renaming songs", [track_id])

    main.ctx.library.set_metadata(track_id, title="Applied")
    main.ctx.library.clear_suggestion(track_id)
    assert db.get_track(track_id).smart_title is None

    main._undo_last()

    assert db.get_track(track_id).smart_title == "Suggested"


def test_creating_an_existing_folder_cannot_be_undone(window, monkeypatch):
    main, db, _data = window
    existing = db.add_folder("Shiurim")
    monkeypatch.setattr(
        QInputDialog, "getText",
        staticmethod(lambda *args, **kwargs: ("Shiurim", True)),
    )

    main._new_folder()

    assert main.undo_action.isEnabled() is False
    assert db.get_source(existing) is not None


def test_a_stale_link_undo_does_not_unlink_the_song(window):
    main, db, data = window
    track_id = data["tracks"][0]

    main._restore_links({track_id: [(9999, 1)]})  # source no longer exists

    assert db.track_links(track_id) == [(data["playlist"], 1)]
