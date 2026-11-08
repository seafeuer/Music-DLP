"""Damaged settings and a damaged library must not stop the app."""

import json
import logging

import pytest
from PySide6.QtWidgets import QMessageBox

from musicdlp.core.config import Settings
from musicdlp.core.db import Database, quarantine_database


@pytest.mark.parametrize(
    "raw",
    [
        {"first_run_done": True, "filename_template": None},
        {"ai_base_url": 123},
        {"bridge_token": 5},
        {"settings_version": "x"},
        {"output_dir": 123},
        {"formats": {"mp3": True}},
        {"notifications": "false"},
        {"theme": ["dark"]},
        {"sleep_min": "x", "sleep_max": None},
    ],
)
def test_wrong_typed_values_are_coerced(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    settings = Settings.load(path)

    assert isinstance(settings.output_dir, str) and settings.output_dir
    assert isinstance(settings.spotify_dir, str) and settings.spotify_dir
    assert isinstance(settings.filename_template, str)
    assert settings.filename_template
    assert isinstance(settings.settings_version, int)
    assert isinstance(settings.notifications, bool)
    assert settings.theme in ("dark", "light", "system")
    assert settings.formats
    assert settings.sleep_min <= settings.sleep_max


def test_numeric_output_dir_falls_back_to_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSICDLP_HOME", str(tmp_path))
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"output_dir": 123}), encoding="utf-8")

    settings = Settings.load(path)

    assert settings.output_dir != "123"
    assert settings.output_dir.startswith(str(tmp_path))


def test_quarantine_moves_a_damaged_database(tmp_path):
    path = tmp_path / "library.db"
    path.write_bytes(b"this is not a database")

    backup = quarantine_database(path)

    assert backup is not None
    assert backup.exists()
    assert backup.read_bytes() == b"this is not a database"
    assert not path.exists()

    db = Database(path)
    assert db.get_sources() == []
    db.close()


def test_startup_moves_a_damaged_database_aside(tmp_path, monkeypatch):
    from musicdlp import app as app_module

    path = tmp_path / "library.db"
    path.write_bytes(b"not a database either")
    warned = []
    monkeypatch.setattr(
        app_module, "db_path", lambda: path
    )
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: warned.append(args[2])),
    )

    db = app_module._open_database(logging.getLogger("test"))

    assert db.get_sources() == []
    assert warned
    assert "damaged" in warned[0].lower()
    assert list(tmp_path.glob("library.db.damaged-*"))
    db.close()

def test_an_unreadable_config_is_kept_beside_the_new_one(tmp_path):
    """A damaged config used to be replaced on the next save, with no copy."""
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")

    settings = Settings.load(path)

    assert settings.output_dir  # defaults are usable
    assert not path.exists()
    backups = list(tmp_path.glob("config.json.bad-*"))
    assert len(backups) == 1
    assert "not json" in backups[0].read_text(encoding="utf-8")


def test_resetting_settings_keeps_the_panel_token():
    """A reset must not make the installed Spotify panel answer 403."""
    settings = Settings()
    settings.bridge_token = "panel-token"
    settings.theme = "light"
    settings.filename_template = "{artist} - {title}"
    settings.first_run_done = True

    fresh = settings.reset_keeping_token()

    assert fresh.bridge_token == "panel-token"
    assert fresh.theme == "dark"
    assert fresh.filename_template == "{title}"
    assert fresh.first_run_done is True


def test_saved_filters_from_a_damaged_config_are_cleaned_not_dropped_whole():
    settings = Settings(saved_filters=[
        {"name": "  Recent  ", "status": "new", "search": 5},
        {"name": "", "status": "all"},
        "not-an-entry",
        {"status": "all", "search": "x"},
    ])

    assert settings.saved_filters == [
        {"name": "Recent", "status": "new", "search": "5"},
    ]
