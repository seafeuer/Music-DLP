"""Google Drive folder support."""

from pathlib import Path

import pytest

from musicdlp.services import gdrive


@pytest.fixture()
def drive(tmp_path, monkeypatch):
    root = tmp_path / "My Drive"
    root.mkdir()
    monkeypatch.setattr(gdrive, "candidates", lambda: [root])
    return root


def test_the_synced_folder_is_found(drive):
    assert gdrive.find_drive() == drive


def test_nothing_found_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive, "candidates", lambda: [tmp_path / "nope"])
    assert gdrive.find_drive() is None


def test_songs_go_into_a_musidlp_subfolder(drive):
    assert gdrive.destination("") == drive / gdrive.SUBFOLDER


def test_a_configured_folder_is_used_as_given(tmp_path):
    chosen = tmp_path / "Elsewhere"
    assert gdrive.destination(str(chosen)) == chosen


def test_no_drive_means_no_destination(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive, "candidates", lambda: [tmp_path / "nope"])
    assert gdrive.destination("") is None
    assert gdrive.is_ready("") is False


def test_files_are_copied(drive, tmp_path):
    song = tmp_path / "Song.mp3"
    song.write_bytes(b"audio")
    sent, errors = gdrive.send([song], "")
    assert sent == 1
    assert errors == []
    assert (drive / gdrive.SUBFOLDER / "Song.mp3").read_bytes() == b"audio"
    # A copy, not a move: the original stays where it was.
    assert song.exists()


def test_sending_twice_is_harmless(drive, tmp_path):
    song = tmp_path / "Song.mp3"
    song.write_bytes(b"audio")
    gdrive.send([song], "")
    sent, errors = gdrive.send([song], "")
    assert (sent, errors) == (0, [])


def test_a_missing_file_is_reported(drive, tmp_path):
    sent, errors = gdrive.send([tmp_path / "gone.mp3"], "")
    assert sent == 0
    assert "Missing file" in errors[0]


def test_no_drive_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(gdrive, "candidates", lambda: [tmp_path / "nope"])
    sent, errors = gdrive.send([], "")
    assert sent == 0
    assert "No Google Drive folder" in errors[0]


def test_move_mode_removes_the_original(drive, tmp_path):
    song = tmp_path / "Song.mp3"
    song.write_bytes(b"audio")
    gdrive.send([song], "", mode="move")
    assert not song.exists()
    assert (drive / gdrive.SUBFOLDER / "Song.mp3").exists()


def test_a_changed_song_is_copied_over_the_old_one(drive, tmp_path):
    """Same size, newer content: the phone must get the new version."""
    import os
    import time

    song = tmp_path / "Song.mp3"
    song.write_bytes(b"aaaa")
    gdrive.send([song], "")

    time.sleep(0.05)
    song.write_bytes(b"bbbb")  # same length, different tags
    os.utime(song, (time.time() + 10, time.time() + 10))
    sent, errors = gdrive.send([song], "")

    assert (sent, errors) == (1, [])
    assert (drive / gdrive.SUBFOLDER / "Song.mp3").read_bytes() == b"bbbb"


def test_two_different_songs_with_one_name_are_kept_apart(drive, tmp_path):
    first = tmp_path / "a" / "01 - Intro.mp3"
    second = tmp_path / "b" / "01 - Intro.mp3"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first song")
    second.write_bytes(b"second song")

    gdrive.send([first], "")
    sent, errors = gdrive.send([second], "")

    folder = drive / gdrive.SUBFOLDER
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["01 - Intro (2).mp3", "01 - Intro.mp3"]
    assert (folder / "01 - Intro.mp3").read_bytes() == b"first song"
    assert (folder / "01 - Intro (2).mp3").read_bytes() == b"second song"
    assert (sent, errors) == (1, [])


def test_a_configured_folder_that_vanished_falls_back_to_the_drive(
    tmp_path, monkeypatch
):
    root = tmp_path / "My Drive"
    root.mkdir()
    monkeypatch.setattr(gdrive, "candidates", lambda: [root])
    gone = tmp_path / "Q" / "OldDrive" / "MusicDLP"
    assert gdrive.destination(str(gone)) == root / gdrive.SUBFOLDER


def test_the_backup_and_sync_container_is_not_mistaken_for_the_drive(
    tmp_path, monkeypatch
):
    """%USERPROFILE%\Google Drive only wraps My Drive; writing there syncs
    nothing."""
    container = tmp_path / "Google Drive"
    real = container / "My Drive"
    real.mkdir(parents=True)
    monkeypatch.setattr(
        gdrive, "candidates", lambda: [container, real]
    )
    assert gdrive.find_drive() == real
    assert gdrive.destination("") == real / gdrive.SUBFOLDER
