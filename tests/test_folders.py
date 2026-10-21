"""Library folders and no duplicate sections when a link is re-added."""

import pytest

from musicdlp.core.config import Settings
from musicdlp.core.db import Database


def _add(db, url, external_id, title, kind="playlist"):
    return db.add_source(
        url=url,
        kind=kind,
        platform="youtube",
        external_id=external_id,
        title=title,
        uploader="Channel",
        item_count=1,
        thumbnail_url=None,
    )


def test_re_adding_a_playlist_with_a_tracking_parameter_reuses_it(tmp_path):
    db = Database(tmp_path / "library.db")
    first = _add(
        db, "https://www.youtube.com/playlist?list=PLabc", "PLabc", "List"
    )
    second = _add(
        db,
        "https://www.youtube.com/playlist?list=PLabc&si=tracking",
        "PLabc",
        "List renamed",
    )
    assert first == second
    assert len(db.get_sources()) == 1
    assert db.get_source(first).title == "List renamed"
    db.close()


def test_different_playlists_stay_separate(tmp_path):
    db = Database(tmp_path / "library.db")
    one = _add(db, "https://www.youtube.com/playlist?list=PL1", "PL1", "One")
    two = _add(db, "https://www.youtube.com/playlist?list=PL2", "PL2", "Two")
    assert one != two
    assert len(db.get_sources()) == 2
    db.close()


def test_folder_assignment_and_listing(tmp_path):
    db = Database(tmp_path / "library.db")
    source_id = _add(db, "https://x.com/a/status/1", "1", "A")
    assert db.get_folders() == []

    db.set_source_folder(source_id, "Shiurim")
    assert db.get_folders() == ["Shiurim"]
    assert db.get_source(source_id).folder == "Shiurim"

    db.set_source_folder(source_id, "")
    assert db.get_folders() == []
    db.close()


def test_sources_are_listed_grouped_by_folder(tmp_path):
    db = Database(tmp_path / "library.db")
    a = _add(db, "https://x.com/a/status/1", "1", "A")
    b = _add(db, "https://x.com/b/status/2", "2", "B")
    c = _add(db, "https://x.com/c/status/3", "3", "C")
    db.set_source_folder(a, "Shiurim")
    db.set_source_folder(b, "Shiurim")

    sources = db.get_sources()
    # Folder members sort together; the ungrouped link is ordered first
    # because an empty folder name sorts before a real one.
    folders = [source.folder or "" for source in sources]
    assert folders == ["", "Shiurim", "Shiurim"]
    assert {source.id for source in sources} == {a, b, c}
    db.close()


def test_folder_survives_a_refresh(tmp_path):
    db = Database(tmp_path / "library.db")
    source_id = _add(
        db, "https://www.youtube.com/playlist?list=PLabc", "PLabc", "List"
    )
    db.set_source_folder(source_id, "Shiurim")

    # Re-adding the same playlist updates it without touching the folder.
    again = _add(
        db,
        "https://www.youtube.com/playlist?list=PLabc&si=x",
        "PLabc",
        "List",
    )
    assert again == source_id
    assert db.get_source(source_id).folder == "Shiurim"
    db.close()
