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
