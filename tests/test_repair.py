"""Repairing a library whose files moved or went missing."""

from pathlib import Path

import pytest

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.maintenance import repair_library


@pytest.fixture(autouse=True)
def _not_a_temp_folder(monkeypatch):
    """pytest runs in %TEMP%, which the app deliberately relocates."""
    monkeypatch.setattr(
        "musicdlp.core.maintenance.is_temporary", lambda path: False
    )


@pytest.fixture()
def library(tmp_path):
    db = Database(tmp_path / "lib.db")
    source = db.add_source(url="u", kind="item", platform="youtube",
                           external_id="a", title="t", uploader="u",
                           item_count=3, thumbnail_url=None)
    return db, source


def _track(db, source, media_id, **fields):
    tid = db.upsert_track(platform="youtube", media_id=media_id, url="u",
                          title=media_id, uploader="C", duration=100)
    db.update_track(tid, **fields)
    db.set_source_tracks(source, [(tid, 1)])
    return tid


def test_a_moved_file_is_found_and_its_path_repaired(tmp_path, library):
    """A library made elsewhere, or with a new output folder."""
    db, source = library
    old = tmp_path / "old" / "MP3"
    new = tmp_path / "new" / "MP3"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    song = old / "Song.mp3"
    song.write_bytes(b"x" * 100)
    tid = _track(db, source, "a", mp3_path=str(song), status="synced",
                 synced_at="2026-01-01")

    song.rename(new / "Song.mp3")
    repair_library(db, Settings(output_dir=str(tmp_path / "new"),
                                spotify_dir=str(tmp_path / "sp")))

    track = db.get_track(tid)
    assert track.status == "synced"
    assert Path(track.mp3_path).exists()
    assert Path(track.mp3_path).parent == new


def test_a_lost_file_does_not_claim_the_song_was_never_downloaded(
    tmp_path, library
):
    """It was downloaded once; "Not downloaded" is a lie and loses that."""
    db, source = library
    tid = _track(db, source, "b",
                 mp3_path=str(tmp_path / "gone" / "Gone.mp3"),
                 status="synced", synced_at="2026-01-01")

    repair_library(db, Settings(output_dir=str(tmp_path / "out"),
                                spotify_dir=str(tmp_path / "sp")))

    track = db.get_track(tid)
    assert track.status == "synced"      # shown as "File missing", not "new"
    assert track.mp3_path is not None    # the old path is kept for Retry


def test_a_lost_file_with_the_download_kept_is_ready_to_convert(
    tmp_path, library
):
    db, source = library
    raw = tmp_path / "raw.webm"
    raw.write_bytes(b"r" * 50)
    tid = _track(db, source, "c",
                 mp3_path=str(tmp_path / "gone" / "Gone.mp3"),
                 raw_path=str(raw), status="converted")

    repair_library(db, Settings(output_dir=str(tmp_path / "out"),
                                spotify_dir=str(tmp_path / "sp")))

    track = db.get_track(tid)
    assert track.status == "downloaded"
    assert track.needs_update is True
    assert track.mp3_path is None


def test_an_intact_library_is_left_alone(tmp_path, library):
    db, source = library
    out = tmp_path / "out" / "MP3"
    out.mkdir(parents=True)
    song = out / "Song.mp3"
    song.write_bytes(b"x" * 100)
    tid = _track(db, source, "d", mp3_path=str(song), status="synced")

    repair_library(db, Settings(output_dir=str(tmp_path / "out"),
                                spotify_dir=str(tmp_path / "sp")))

    track = db.get_track(tid)
    assert track.status == "synced"
    assert track.mp3_path == str(song)


# ------------------------------------------------ relinking from a folder

def test_songs_are_relinked_to_files_in_another_folder(tmp_path, library):
    from musicdlp.services.library import LibraryService

    db, source = library
    elsewhere = tmp_path / "somewhere"
    elsewhere.mkdir()
    (elsewhere / "Song.mp3").write_bytes(b"x" * 100)

    tid = _track(db, source, "e",
                 mp3_path=str(tmp_path / "gone" / "Song.mp3"),
                 status="synced")

    library_service = LibraryService(db, Settings(), None)
    counts = library_service.relink_from_folder(elsewhere)

    track = db.get_track(tid)
    assert counts["relinked"] == 1
    assert Path(track.mp3_path) == elsewhere / "Song.mp3"
    assert track.status == "synced"


def test_relinking_ignores_a_different_file_name(tmp_path, library):
    from musicdlp.services.library import LibraryService

    db, source = library
    elsewhere = tmp_path / "somewhere"
    elsewhere.mkdir()
    (elsewhere / "Another Song.mp3").write_bytes(b"x" * 100)

    tid = _track(db, source, "f",
                 mp3_path=str(tmp_path / "gone" / "Song.mp3"),
                 status="synced")

    counts = LibraryService(db, Settings(), None).relink_from_folder(elsewhere)

    assert counts["relinked"] == 0
    assert db.get_track(tid).mp3_path.endswith("Song.mp3")


def test_relinking_leaves_a_present_file_alone(tmp_path, library):
    from musicdlp.services.library import LibraryService

    db, source = library
    out = tmp_path / "out"
    out.mkdir()
    song = out / "Song.mp3"
    song.write_bytes(b"x" * 100)
    tid = _track(db, source, "g", mp3_path=str(song), status="synced")

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "Song.mp3").write_bytes(b"y" * 50)

    LibraryService(db, Settings(), None).relink_from_folder(elsewhere)
    assert db.get_track(tid).mp3_path == str(song)


def test_relinking_restores_every_missing_format(tmp_path, library):
    """One missing format used to hide the other forever."""
    from musicdlp.services.library import LibraryService

    db, source = library
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "Song.mp3").write_bytes(b"x" * 100)
    (elsewhere / "Song.m4a").write_bytes(b"x" * 100)

    tid = _track(
        db, source, "h",
        mp3_path=str(tmp_path / "gone" / "Song.mp3"),
        status="synced",
    )
    db.update_track(tid, m4a_path=str(tmp_path / "gone" / "Song.m4a"))

    service = LibraryService(db, Settings(), None)
    first = service.relink_from_folder(elsewhere)
    second = service.relink_from_folder(elsewhere)

    track = db.get_track(tid)
    assert first["relinked"] == 1 and second["relinked"] == 0
    assert Path(track.mp3_path) == elsewhere / "Song.mp3"
    assert Path(track.m4a_path) == elsewhere / "Song.m4a"


def test_relinking_keeps_a_pending_rewrite_pending(tmp_path, library):
    """Finding the file again does not make its old tags current."""
    from musicdlp.services.library import LibraryService

    db, source = library
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "Song.mp3").write_bytes(b"x" * 100)

    tid = _track(
        db, source, "i",
        mp3_path=str(tmp_path / "gone" / "Song.mp3"),
        status="converted",
    )
    db.update_track(tid, needs_update=1)

    LibraryService(db, Settings(), None).relink_from_folder(elsewhere)

    assert db.get_track(tid).needs_update is True


def test_a_colliding_file_is_kept_beside_the_moved_one(tmp_path, library):
    """The merge skipped collisions and still switched folders."""
    from musicdlp.core.maintenance import _move_tree

    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "Song.mp3").write_bytes(b"new")
    (target / "Song.mp3").write_bytes(b"old")

    _move_tree(source, target)

    assert (target / "Song.mp3").read_bytes() == b"old"
    assert (target / "Song (2).mp3").read_bytes() == b"new"


def test_a_kept_download_is_remapped_with_the_output_folder(
    tmp_path, library, monkeypatch
):
    """The kept download stayed in the old tree and was fetched again."""
    from musicdlp.core import maintenance

    db, source = library
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    raw = old / "Song.webm"
    raw.write_bytes(b"x" * 100)
    tid = _track(db, source, "raw-1", raw_path=str(raw), status="converted")

    settings = Settings(output_dir=str(old), spotify_dir=str(tmp_path / "sp"))
    monkeypatch.setattr(maintenance, "is_temporary", lambda path: Path(path) == old)
    monkeypatch.setattr(maintenance, "default_output_dir", lambda: new)

    maintenance._repair_folders(db, settings, move=True)

    assert settings.output_dir == str(new)
    assert Path(db.get_track(tid).raw_path) == new / "Song.webm"
