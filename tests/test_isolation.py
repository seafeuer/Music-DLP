"""Guard: tests must never write to the real user configuration."""

import os

from musicdlp.core import paths


def test_tests_are_isolated_from_the_real_config():
    override = os.environ.get("MUSICDLP_HOME")
    assert override, "MUSICDLP_HOME must be set for the test run"
    assert str(paths.config_path()).startswith(override)
    assert str(paths.db_path()).startswith(override)
    assert str(paths.log_dir()).startswith(override)

    appdata = os.environ.get("APPDATA")
    if appdata:
        assert appdata.lower() not in str(paths.config_path()).lower()


def test_start_directory_falls_back_when_missing(tmp_path):
    missing = tmp_path / "gone" / "deeper"
    start = paths.start_directory(missing)
    assert start.is_dir()
    assert start == tmp_path


def test_start_directory_uses_existing(tmp_path):
    assert paths.start_directory(tmp_path) == tmp_path
    assert paths.start_directory(None) == __import__("pathlib").Path.home()


def test_default_folders_stay_inside_an_isolated_home():
    """Regression: defaults used to point at the real ~/MusicDLP."""
    override = os.environ["MUSICDLP_HOME"]
    for path in (
        paths.default_output_dir(),
        paths.default_spotify_dir(),
    ):
        assert str(path).startswith(override), path
    assert paths.is_isolated() is True


def test_settings_defaults_are_isolated():
    from musicdlp.core.config import Settings

    override = os.environ["MUSICDLP_HOME"]
    settings = Settings()
    assert settings.output_dir.startswith(override)
    assert settings.spotify_dir.startswith(override)


def test_repair_never_moves_a_folder_in_an_isolated_run(tmp_path, monkeypatch):
    """A scripted run must not relocate its own folders, let alone move
    files towards the real library."""
    import shutil

    from musicdlp.core.config import Settings
    from musicdlp.core.db import Database
    from musicdlp.core.maintenance import repair_library

    source = tmp_path / "tempout"
    source.mkdir()
    (source / "Song.mp3").write_bytes(b"x")
    settings = Settings(
        output_dir=str(source),
        spotify_dir=str(tmp_path / "temposp"),
        first_run_done=True,
    )
    db = Database(tmp_path / "lib.db")
    try:
        notes = repair_library(db, settings)
    finally:
        db.close()

    assert source.exists()
    assert (source / "Song.mp3").exists()
    assert settings.output_dir == str(source)
    assert notes and "temporary folder" in notes[0]
