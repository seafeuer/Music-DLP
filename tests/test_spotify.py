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


def test_open_folder_reports_a_path_it_cannot_open(tmp_path):
    # A file where a folder is expected used to raise FileExistsError from
    # mkdir and reach the crash dialog. It must report, not raise.
    not_a_folder = tmp_path / "song.mp3"
    not_a_folder.write_bytes(b"audio")

    assert SpotifyService.open_folder(not_a_folder) is False


def test_a_brief_permission_error_is_retried_not_reported_as_failed(
    tmp_path, monkeypatch
):
    """Spotify or a virus scanner can hold a file for a moment."""
    import shutil as shutil_module

    source = tmp_path / "source"
    source.mkdir()
    file = source / "one.mp3"
    file.write_bytes(b"one")
    destination = tmp_path / "spotify"

    real_copy = shutil_module.copy2
    attempts = {"n": 0}

    def flaky(src, dst, *args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise PermissionError("file is in use")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr("musicdlp.services.spotify.shutil.copy2", flaky)

    result = SpotifyService().sync([file], destination)

    assert result.copied == 1
    assert result.failed == 0
    assert attempts["n"] == 3


def test_a_permanent_copy_error_fails_once_and_keeps_going(tmp_path, monkeypatch):
    import shutil as shutil_module

    source = tmp_path / "source"
    source.mkdir()
    first = source / "one.mp3"
    first.write_bytes(b"one")
    second = source / "two.mp3"
    second.write_bytes(b"two")
    destination = tmp_path / "spotify"

    real_copy = shutil_module.copy2

    def fail_first(src, dst, *args, **kwargs):
        if str(src).endswith("one.mp3"):
            raise PermissionError("always locked")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr("musicdlp.services.spotify.shutil.copy2", fail_first)

    result = SpotifyService().sync([first, second], destination)

    assert result.failed == 1
    assert result.copied == 1
    assert "one.mp3" in result.errors[0]
