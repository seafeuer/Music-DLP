"""Library repair: moving files out of temp folders and fixing paths."""

import tempfile
from pathlib import Path

import pytest

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.maintenance import is_temporary, repair_library


@pytest.fixture()
def not_temp(monkeypatch):
    """pytest's tmp_path lives under the OS temp folder.

    Stub the temp check so the path-repair tests exercise their own logic
    instead of the temp-folder migration.
    """
    monkeypatch.setattr(
        "musicdlp.core.maintenance.is_temporary", lambda path: False
    )


def _setup(tmp_path, output_dir, spotify_dir):
    settings = Settings(
        output_dir=str(output_dir),
        spotify_dir=str(spotify_dir),
        first_run_done=True,
    )
    db = Database(tmp_path / "library.db")
    source_id = db.add_source(
        url="https://www.youtube.com/watch?v=abc",
        kind="item",
        platform="youtube",
        external_id="abc",
        title="Song",
        uploader="Artist",
        item_count=1,
        thumbnail_url=None,
    )
    track_id = db.upsert_track(
        platform="youtube",
        media_id="abc",
        url="https://www.youtube.com/watch?v=abc",
        title="Song",
        uploader="Artist",
    )
    db.set_source_tracks(source_id, [(track_id, 1)])
    return settings, db, track_id


def test_is_temporary_detects_temp_dir():
    assert is_temporary(Path(tempfile.gettempdir()) / "x" / "y")
    assert not is_temporary(Path.home() / "MusicDLP")


def test_moves_output_out_of_temp(tmp_path, monkeypatch):
    temp_root = tmp_path / "Temp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_root))

    old_out = temp_root / "build" / "out"
    (old_out / "MP3").mkdir(parents=True)
    audio = old_out / "MP3" / "Song.mp3"
    audio.write_bytes(b"audio")

    settings, db, track_id = _setup(
        tmp_path, old_out, temp_root / "build" / "spotify"
    )
    db.update_track(track_id, status="converted", mp3_path=str(audio))

    monkeypatch.setattr(
        "musicdlp.core.maintenance.default_output_dir",
        lambda: tmp_path / "MusicDLP",
    )
    monkeypatch.setattr(
        "musicdlp.core.maintenance.default_spotify_dir",
        lambda: tmp_path / "MusicDLP" / "Spotify",
    )

    notes = repair_library(db, settings)
    assert notes

    assert settings.output_dir == str(tmp_path / "MusicDLP")
    assert settings.spotify_dir == str(tmp_path / "MusicDLP" / "Spotify")

    track = db.get_track(track_id)
    assert track.mp3_path == str(tmp_path / "MusicDLP" / "MP3" / "Song.mp3")
    assert Path(track.mp3_path).exists()
    db.close()


def test_missing_output_marks_track_for_redownload(tmp_path, not_temp):
    output = tmp_path / "out"
    output.mkdir()
    settings, db, track_id = _setup(tmp_path, output, tmp_path / "spotify")
    db.update_track(
        track_id,
        status="synced",
        mp3_path=str(output / "MP3" / "Gone.mp3"),
        synced_at="2026-01-01T00:00:00",
    )

    repair_library(db, settings)
    track = db.get_track(track_id)
    assert track.status == "new"
    assert track.mp3_path is None
    assert track.synced_at is None
    db.close()


def test_finds_output_in_a_subfolder(tmp_path, not_temp):
    output = tmp_path / "out"
    nested = output / "MP3" / "moved"
    nested.mkdir(parents=True)
    audio = nested / "Song.mp3"
    audio.write_bytes(b"audio")

    settings, db, track_id = _setup(tmp_path, output, tmp_path / "spotify")
    db.update_track(
        track_id, status="converted", mp3_path=str(output / "Song.mp3")
    )

    repair_library(db, settings)
    track = db.get_track(track_id)
    assert track.mp3_path == str(audio)
    assert track.status == "converted"
    db.close()


def test_healthy_library_is_untouched(tmp_path, not_temp):
    output = tmp_path / "out"
    (output / "MP3").mkdir(parents=True)
    audio = output / "MP3" / "Song.mp3"
    audio.write_bytes(b"audio")

    settings, db, track_id = _setup(tmp_path, output, tmp_path / "spotify")
    db.update_track(track_id, status="converted", mp3_path=str(audio))

    notes = repair_library(db, settings)
    assert notes == []
    assert db.get_track(track_id).mp3_path == str(audio)
    db.close()
