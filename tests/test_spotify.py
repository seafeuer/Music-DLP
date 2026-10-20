"""Spotify folder delivery."""

from pathlib import Path

from musicdlp.services.spotify import SpotifyService


def test_sync_copies_and_skips_identical(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    first = source / "one.mp3"
    first.write_bytes(b"one")
    second = source / "two.mp3"
    second.write_bytes(b"two")
    destination = tmp_path / "spotify"

    service = SpotifyService()
    result = service.sync([first, second], destination)
    assert result.copied == 2
    assert result.failed == 0
    assert (destination / "one.mp3").read_bytes() == b"one"

    again = service.sync([first, second], destination)
    assert again.skipped == 2
    assert again.copied == 0


def test_sync_move_removes_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    file = source / "one.mp3"
    file.write_bytes(b"one")
    destination = tmp_path / "spotify"

    service = SpotifyService()
    result = service.sync([file], destination, mode="move")
    assert result.copied == 1
    assert not file.exists()
    assert (destination / "one.mp3").exists()


def test_sync_missing_file_is_reported(tmp_path):
    service = SpotifyService()
    result = service.sync([tmp_path / "missing.mp3"], tmp_path / "spotify")
    assert result.failed == 1
    assert result.errors


def test_setup_steps_mentions_folder(tmp_path):
    text = SpotifyService.setup_steps(tmp_path / "spotify")
    assert str(tmp_path / "spotify") in text
    assert "Local Files" in text
    assert "does not allow" in text


def test_find_exe_returns_path_or_none():
    service = SpotifyService()
    exe = service.find_exe()
    assert exe is None or Path(exe).exists()
