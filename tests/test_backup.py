"""Dated library/settings snapshots, and the restore applied at start-up."""

import json
import sqlite3

import pytest

from musicdlp.core import backup
from musicdlp.core.db import Database


def _library(path, media_id, title):
    db = Database(path)
    db.add_source(
        url=f"https://youtu.be/{media_id}",
        kind="video",
        platform="youtube",
        external_id=media_id,
        title=title,
        uploader="Channel",
        item_count=1,
        thumbnail_url=None,
    )
    db.upsert_track(
        platform="youtube",
        media_id=media_id,
        url=f"https://youtu.be/{media_id}",
        title=title,
        uploader="Channel",
        duration=200,
    )
    db.close()
    return path


def _settings_file(path, value="before"):
    path.write_text(json.dumps({"output_dir": value}), encoding="utf-8")
    return path


@pytest.fixture()
def env(tmp_path):
    source = _library(tmp_path / "live" / "library.db", "aaa", "First song")
    config = _settings_file(tmp_path / "live" / "config.json")
    root = tmp_path / "backups"
    return source, config, root


def test_a_backup_holds_the_library_and_the_settings(env):
    source, config, root = env

    folder = backup.create_backup(
        "manual", database=source, config=config, root=root
    )

    assert folder is not None
    assert (folder / "library.db").is_file()
    assert (folder / "config.json").is_file()
    metadata = json.loads((folder / "backup.json").read_text(encoding="utf-8"))
    assert metadata["reason"] == "manual"
    assert metadata["tracks"] == 1
    assert metadata["settings"] is True

    # The copy is a real library, not just bytes.
    connection = sqlite3.connect(folder / "library.db")
    try:
        titles = [
            row[0] for row in connection.execute("SELECT title FROM tracks")
        ]
    finally:
        connection.close()
    assert titles == ["First song"]


def test_the_newest_backup_is_listed_first(env):
    source, config, root = env
    first = backup.create_backup(
        "daily", database=source, config=config, root=root
    )
    second = backup.create_backup(
        "manual", database=source, config=config, root=root
    )

    entries = backup.list_backups(root)

    assert {entry["path"] for entry in entries} == {str(first), str(second)}
    assert entries[0]["name"] >= entries[-1]["name"]
    assert entries[0]["tracks"] == 1


def test_old_backups_are_pruned(env):
    source, config, root = env
    for _ in range(3):
        backup.create_backup(
            "manual", database=source, config=config, root=root, keep=2
        )

    assert len(backup.list_backups(root)) == 2


def test_a_folder_outside_the_backup_directory_is_refused(env, tmp_path):
    _source, _config, root = env
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "library.db").write_bytes(b"not really a database")

    with pytest.raises(backup.BackupError, match="not a Music DLP backup"):
        backup.validate_backup(elsewhere, root=root)


def test_a_damaged_backup_is_refused(env):
    _source, _config, root = env
    folder = root / "broken"
    folder.mkdir(parents=True)
    (folder / "library.db").write_bytes(b"not really a database")

    with pytest.raises(backup.BackupError):
        backup.validate_backup(folder, root=root)


def test_a_restore_request_is_applied_before_the_library_is_opened(
    env, tmp_path
):
    source, config, root = env
    # A backup that holds a different library from the live one.
    old_library = _library(tmp_path / "old" / "library.db", "bbb", "Older song")
    old_config = _settings_file(tmp_path / "old" / "config.json", "restored")
    folder = backup.create_backup(
        "manual", database=old_library, config=old_config, root=root
    )

    backup.request_restore(folder, root=root)
    message = backup.apply_pending_restore(
        database=source, config=config, root=root
    )

    assert message and "Restored" in message
    db = Database(source)
    try:
        titles = [track.title for track in db.get_all_tracks()]
    finally:
        db.close()
    assert titles == ["Older song"]
    assert json.loads(config.read_text(encoding="utf-8"))["output_dir"] == "restored"
    kept = list(source.parent.glob("library.db.before-restore-*"))
    assert kept, "the previous library must be kept"
    assert backup.pending_restore(root=root) is None


def test_an_invalid_restore_request_is_dropped(env, tmp_path):
    _source, _config, root = env
    path = backup.restore_request_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"source": str(tmp_path / "not-a-backup")}),
        encoding="utf-8",
    )

    assert backup.pending_restore(root=root) is None
    assert not path.exists()


def test_a_daily_backup_runs_at_most_once_a_day(env):
    source, config, root = env

    first = backup.maybe_daily_backup(
        database=source, config=config, root=root
    )
    second = backup.maybe_daily_backup(
        database=source, config=config, root=root
    )

    assert first is not None
    assert second is None
    assert len(backup.list_backups(root)) == 1


def test_a_missing_config_does_not_stop_the_library_backup(env, tmp_path):
    source, _config, root = env
    missing = tmp_path / "no-config.json"

    folder = backup.create_backup(
        "manual", database=source, config=missing, root=root
    )

    assert folder is not None
    assert (folder / "library.db").is_file()
    assert not (folder / "config.json").exists()
    assert backup.list_backups(root)[0]["settings"] is False