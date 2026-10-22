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
from musicdlp.ui.dialogs.settings import SettingsDialog
from musicdlp.ui.main_window import ALL_SONGS_ID, MainWindow, build_context


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
    assert ("auto", "") in items
    assert dialog._ai_provider_index() == 4
    assert items[0] == ("auto", "")


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


def _run_lookup(monkeypatch, recordings, *, official=None):
    """Feed a canned recording list to lookup(), by query type."""
    def fake_get(*args, **kwargs):
        query = (kwargs.get("params") or {}).get("query", "")
        if "status:official" in query:
            return FakeResponse({"recordings": official or []})
        return FakeResponse({"recordings": recordings})

    monkeypatch.setattr(metadata_lookup.requests, "get", fake_get)
    monkeypatch.setattr(metadata_lookup, "_wait_for_turn", lambda: None)


def test_lookup_prefers_a_studio_album_over_a_bootleg(monkeypatch):
    bootleg = {
        "title": "Same Song",
        "score": 100,
        "artist-credit": [{"name": "The Band"}],
        "releases": [{
            "title": "Live in 1995", "date": "1995", "status": "Bootleg",
            "release-group": {"primary-type": "Album",
                              "secondary-types": ["Live"]},
            "media": [{"track-offset": 0}],
        }],
    }
    studio = {
        "title": "Same Song",
        "score": 100,
        "artist-credit": [{"name": "The Band"}],
        "releases": [{
            "title": "Great Album", "date": "1994", "status": "Official",
            "release-group": {"primary-type": "Album"},
            "media": [{"track-offset": 4}],
        }],
    }
    _run_lookup(monkeypatch, [bootleg, studio])
    found = lookup("The Band", "Same Song")
    assert found.album == "Great Album"
    assert found.track == 5


def test_lookup_asks_again_for_official_releases(monkeypatch):
    """When every first-pass hit is a bootleg, a second query finds the album."""
    bootleg = {
        "title": "Same Song",
        "score": 100,
        "artist-credit": [{"name": "The Band"}],
        "releases": [{
            "title": "Live in 1995", "date": "1995", "status": "Bootleg",
            "release-group": {"primary-type": "Album",
                              "secondary-types": ["Live"]},
            "media": [{"track-offset": 0}],
        }],
    }
    official = {
        "title": "Same Song",
        "score": 100,
        "artist-credit": [{"name": "The Band"}],
        "releases": [{
            "title": "Great Album", "date": "1994", "status": "Official",
            "release-group": {"primary-type": "Album"},
            "media": [{"track-offset": 1}],
        }],
    }
    _run_lookup(monkeypatch, [bootleg], official=[official])
    found = lookup("The Band", "Same Song")
    assert found.album == "Great Album"
    assert found.track == 2


def test_lookup_ignores_a_different_artist_with_the_same_title(monkeypatch):
    """A film composer called Christopher Drake must not beat Drake."""
    wrong = {
        "title": "At the Gates",
        "score": 98,
        "artist-credit": [{"name": "Christopher Drake"}],
        "releases": [{
            "title": "Wonder Woman", "date": "2010", "status": "Official",
            "release-group": {"primary-type": "Album"},
            "media": [{"track-offset": 13}],
        }],
    }
    right = {
        "title": "At the Gates",
        "score": 100,
        "artist-credit": [{"name": "Drake, Lil Uzi Vert"}],
        "releases": [{
            "title": "Care Package 2", "date": "2023", "status": "Official",
            "release-group": {"primary-type": "Album"},
            "media": [{"track-offset": 3}],
        }],
    }
    _run_lookup(monkeypatch, [wrong, right])
    found = lookup("Drake", "At the Gates")
    assert found.album == "Care Package 2"
    assert found.artist == "Drake, Lil Uzi Vert"


def test_lookup_prefers_an_album_over_a_compilation(monkeypatch):
    compilation = {
        "title": "Same Song",
        "score": 100,
        "artist-credit": [{"name": "The Band"}],
        "releases": [{
            "title": "Now That's Music 52", "date": "2019",
            "status": "Official",
            "release-group": {"primary-type": "Album",
                              "secondary-types": ["Compilation"]},
            "media": [{"track-offset": 8}],
        }],
    }
    album = {
        "title": "Same Song",
        "score": 100,
        "artist-credit": [{"name": "The Band"}],
        "releases": [{
            "title": "Great Album", "date": "2019-03-29",
            "status": "Official",
            "release-group": {"primary-type": "Album"},
            "media": [{"track-offset": 1}],
        }],
    }
    _run_lookup(monkeypatch, [compilation, album])
    found = lookup("The Band", "Same Song")
    assert found.album == "Great Album"


def test_saving_settings_keeps_the_translation_choices(qapp, window, monkeypatch):
    """The Settings dialog does not show these, so it must not reset them."""
    # Saving with AI left unconfigured asks a question; it is not what this
    # test is about.
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    main, db, _data = window
    main.settings.translate_language = "Hebrew"
    main.settings.translate_style = "transliterated"
    main.settings.translate_artist = False

    dialog = SettingsDialog(
        main.settings, main.ctx.ffmpeg, main.ctx.ytdlp, main.ctx.spotify, main
    )
    dialog._save()

    saved = dialog.result_settings
    assert saved is not None
    assert saved.translate_language == "Hebrew"
    assert saved.translate_style == "transliterated"
    assert saved.translate_artist is False


def test_the_ytdlp_updater_explains_itself_in_a_packaged_build(
    qapp, window, monkeypatch
):
    """A frozen build has no pip; it must say so instead of failing."""
    import sys

    main, _db, _data = window
    dialog = SettingsDialog(
        main.settings, main.ctx.ffmpeg, main.ctx.ytdlp, main.ctx.spotify, main,
        start_tab="Advanced",
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    dialog._update_ytdlp()
    assert "packaged app" in dialog.ytdlp_status.text()


# ------------------------------------------------- the new 2.7.1 fixes

def test_the_menu_entry_for_suggested_titles_opens_the_dialog(window, monkeypatch):
    """It used to raise 'native Qt signal instance is not callable'."""
    import musicdlp.ui.main_window as main_window

    main, db, data = window
    db.update_track(data["tracks"][0], smart_title="Some Song",
                    smart_confidence=0.4)
    main._reload_current_source()

    opened = []
    monkeypatch.setattr(
        main_window, "SmartTitlesDialog",
        lambda rows, *a, **k: opened.append(rows) or _StubDialog(),
    )
    main.track_table._suggestions_action.trigger()
    assert opened, "the menu entry did not open Suggested titles"


class _StubDialog:
    accepted_values: list = []

    def exec(self):
        return 0


def test_songs_sort_by_number_not_by_letter(qapp, window):
    """A playlist must read 1, 2, 10 rather than 1, 10, 2."""
    main, db, data = window
    for index in range(3, 12):
        tid = db.upsert_track(
            platform="youtube", media_id=f"n{index}",
            url=f"https://www.youtube.com/watch?v=n{index}",
            title=f"Song {index}", uploader="Channel", duration=100,
        )
        db.set_source_tracks(data["playlist"], [(tid, index)])
    main._reload_current_source()

    table = main.track_table.table
    table.sortByColumn(1, Qt.AscendingOrder)
    positions = [
        table.model().index(row, 1).data()
        for row in range(table.model().rowCount())
    ]
    assert positions[:3] == ["1", "2", "3"]
    assert positions[-1] == "11"


def test_length_sorts_by_seconds_not_by_letter(qapp, window):
    main, db, data = window
    db.update_track(data["tracks"][0], duration=100)
    db.update_track(data["tracks"][1], duration=1000)
    main._reload_current_source()
    table = main.track_table.table
    table.sortByColumn(4, Qt.AscendingOrder)
    lengths = [
        table.model().index(row, 4).data()
        for row in range(table.model().rowCount())
    ]
    assert lengths == ["1:40", "16:40"]


def test_raw_downloads_are_kept_by_default():
    assert Settings().keep_raw is True


def test_the_delete_raw_box_asks_the_opposite_question(qapp, window):
    """The stored setting is 'keep', the checkbox says 'delete'."""
    main, _db, _data = window
    main.settings.keep_raw = True
    dialog = SettingsDialog(
        main.settings, main.ctx.ffmpeg, main.ctx.ytdlp, main.ctx.spotify, main
    )
    assert dialog.keep_raw_check.isChecked() is False
    assert dialog.keep_raw_check.text().startswith("Delete")


def test_sending_to_google_drive_copies_the_ticked_songs(
    qapp, window, tmp_path, monkeypatch
):
    from musicdlp.services import gdrive

    main, db, data = window
    drive = tmp_path / "My Drive"
    drive.mkdir()
    monkeypatch.setattr(gdrive, "candidates", lambda: [drive])

    song = tmp_path / "Song.mp3"
    song.write_bytes(b"audio")
    db.update_track(data["tracks"][0], mp3_path=str(song), status="converted")
    main.settings.gdrive_dir = ""
    main._reload_current_source()
    main._set_track_included(data["tracks"][0], True)

    main._send_to_gdrive()

    sent = drive / gdrive.SUBFOLDER / "Song.mp3"
    assert sent.exists()
    assert "Sent 1 file" in main.status.status_label.text()


def test_sending_without_a_drive_explains_itself(qapp, window, tmp_path, monkeypatch):
    from musicdlp.services import gdrive

    main, db, data = window
    monkeypatch.setattr(gdrive, "candidates", lambda: [tmp_path / "nope"])
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    song = tmp_path / "Song.mp3"
    song.write_bytes(b"audio")
    db.update_track(data["tracks"][0], mp3_path=str(song), status="converted")
    main._reload_current_source()
    main._set_track_included(data["tracks"][0], True)

    main._send_to_gdrive()  # must not raise


def test_saving_settings_keeps_the_custom_prompt_and_drive_folder(
    qapp, window, monkeypatch
):
    """Both are new fields; a save must not silently reset them."""
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    main, _db, _data = window
    main.settings.ai_custom_prompt = "Always keep the vocaloid singer."
    main.settings.gdrive_dir = r"D:\Drive\MusicDLP"

    dialog = SettingsDialog(
        main.settings, main.ctx.ffmpeg, main.ctx.ytdlp, main.ctx.spotify, main
    )
    dialog._save()

    saved = dialog.result_settings
    assert saved is not None
    assert saved.ai_custom_prompt == "Always keep the vocaloid singer."
    assert saved.gdrive_dir == r"D:\Drive\MusicDLP"


def test_the_settings_dialog_shows_the_saved_custom_prompt(qapp, window):
    main, _db, _data = window
    main.settings.ai_custom_prompt = "Prefer official English titles."
    dialog = SettingsDialog(
        main.settings, main.ctx.ffmpeg, main.ctx.ytdlp, main.ctx.spotify, main
    )
    assert dialog.ai_prompt_edit.toPlainText() == "Prefer official English titles."


def test_the_all_songs_view_numbers_stay_in_order(qapp, window):
    """Sorting the All Songs view must not shuffle its own row numbers."""
    main, db, data = window
    other = db.add_source(
        url="https://www.youtube.com/playlist?list=PLtwo",
        kind="playlist", platform="youtube", external_id="PLtwo",
        title="Second list", uploader="Channel", item_count=2,
        thumbnail_url=None,
    )
    ids = []
    for index in (1, 2):
        tid = db.upsert_track(
            platform="youtube", media_id=f"z{index}",
            url=f"https://www.youtube.com/watch?v=z{index}",
            title=f"Other {index}", uploader="Channel", duration=100,
        )
        ids.append(tid)
    db.set_source_tracks(other, [(tid, i + 1) for i, tid in enumerate(ids)])

    main._load_source(ALL_SONGS_ID)
    table = main.track_table.table
    numbers = [
        table.model().index(row, 1).data()
        for row in range(table.model().rowCount())
    ]
    assert numbers == [str(index) for index in range(1, len(numbers) + 1)]


def test_an_old_config_keeps_raw_files_after_upgrading(tmp_path):
    """2.7.0 wrote keep_raw false by default; upgrading must not keep deleting."""
    path = tmp_path / "config.json"
    path.write_text('{"theme": "dark", "keep_raw": false}', encoding="utf-8")
    assert Settings.load(path).keep_raw is True


def test_a_deliberate_keep_setting_is_left_alone(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        '{"theme": "dark", "keep_raw": true, "settings_version": 2}',
        encoding="utf-8",
    )
    assert Settings.load(path).keep_raw is True


def test_find_it_leaves_auto_detection_on(qapp, window, tmp_path, monkeypatch):
    """Pressing Find it must not pin a path that breaks if Drive moves."""
    from musicdlp.services import gdrive

    main, _db, _data = window
    root = tmp_path / "My Drive"
    root.mkdir()
    monkeypatch.setattr(gdrive, "candidates", lambda: [root])

    dialog = SettingsDialog(
        main.settings, main.ctx.ffmpeg, main.ctx.ytdlp, main.ctx.spotify, main
    )
    dialog._find_gdrive()
    assert dialog.gdrive_edit.text() == ""
    assert str(root) in dialog.gdrive_status.text()


# ------------------------------------------------- a settings save can fail safely

def test_saving_settings_survives_an_unsaveable_value(
    qapp, window, monkeypatch
):
    """A pasted key can hold a character UTF-8 cannot write.

    Only OSError used to be caught, so that exception escaped into Qt and
    the window closed with the settings unsaved.
    """
    from musicdlp.ui.dialogs import settings as settings_module

    shown = []
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *args, **kwargs: shown.append(args[1:3]),
    )
    # Patch only the work the button does, not Settings.save itself, which
    # the window also calls on exit.
    monkeypatch.setattr(
        settings_module.SettingsDialog, "_save_now",
        lambda self: (_ for _ in ()).throw(ValueError("bad value")),
    )

    main, _db, _data = window
    dialog = settings_module.SettingsDialog(
        main.settings, main.ctx.ffmpeg, main.ctx.ytdlp, main.ctx.spotify, main
    )
    dialog._save()          # must not raise
    assert shown, "the user was not told"
    assert dialog.result_settings is None
    dialog.close()


def test_an_invisible_character_is_removed_from_the_key():
    from musicdlp.ui.dialogs.settings import _clean_key

    assert _clean_key("﻿sk-abc​") == "sk-abc"
    assert _clean_key("  sk-abc\n") == "sk-abc"


def test_a_key_with_an_unpaired_surrogate_is_saved_cleanly(tmp_path):
    """It used to raise UnicodeEncodeError and lose every setting."""
    path = tmp_path / "config.json"
    settings = Settings(ai_api_key="sk-test-\ud800abc")
    settings.save(path)                     # must not raise
    loaded = Settings.load(path)
    assert loaded.ai_api_key.startswith("sk-test-")
