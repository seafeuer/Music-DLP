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
