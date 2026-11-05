"""Dated snapshots of the library and the settings.

The library is one SQLite file. A crash, a damaged database or a mistaken
delete can therefore lose a lot of work at once. These snapshots are plain
folders under ``<data>/backups``: a consistent copy of ``library.db``, a
copy of ``config.json`` and a small ``backup.json`` that says what it is.

A restore is deliberately two-step. Picking a backup writes a request; the
next start applies it before the database is opened, so the running app
never has to juggle a file it is already using.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from musicdlp import __version__
from musicdlp.core.paths import config_path, data_dir, db_path

logger = logging.getLogger("musicdlp.backup")

KEEP_BACKUPS = 10
_METADATA = "backup.json"


class BackupError(Exception):
    """A backup that cannot be used, with the reason to show."""


def backups_dir(root: Optional[Path] = None) -> Path:
    return Path(root) if root is not None else data_dir() / "backups"


def restore_request_path(root: Optional[Path] = None) -> Path:
    base = Path(root) if root is not None else data_dir()
    return base / "restore-request.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp() -> str:
    # Local time is what a person reads in the folder name.
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _unique_folder(root: Path) -> Path:
    stamp = _timestamp()
    folder = root / stamp
    counter = 2
    while folder.exists():
        folder = root / f"{stamp}-{counter}"
        counter += 1
    return folder


def _count_tracks(path: Path) -> int:
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            return int(connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0])
        finally:
            connection.close()
    except (sqlite3.Error, OSError, TypeError):
        return 0


def create_backup(
    reason: str = "manual",
    *,
    database: Optional[Path] = None,
    config: Optional[Path] = None,
    root: Optional[Path] = None,
    keep: int = KEEP_BACKUPS,
) -> Optional[Path]:
    """Copy the library and settings into a dated folder.

    Returns the folder, or None when there is no library yet or the copy
    failed. A backup must never be able to stop the app.
    """
    source = Path(database) if database is not None else db_path()
    settings_file = Path(config) if config is not None else config_path()
    if not source.is_file():
        return None

    parent = backups_dir(root)
    folder = _unique_folder(parent)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        # sqlite3's own backup API reads a consistent database even while
        # it is open in WAL mode; copying the file alone would not.
        destination = sqlite3.connect(str(folder / "library.db"))
        origin = sqlite3.connect(str(source))
        try:
            origin.backup(destination)
        finally:
            origin.close()
            destination.close()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Could not back up the library: %s", exc)
        shutil.rmtree(folder, ignore_errors=True)
        return None

    if settings_file.is_file():
        try:
            shutil.copy2(settings_file, folder / "config.json")
        except OSError as exc:
            logger.warning("Could not back up the settings: %s", exc)

    metadata = {
        "created_at": _now().isoformat(timespec="seconds"),
        "reason": reason,
        "version": __version__,
        "tracks": _count_tracks(folder / "library.db"),
        "settings": (folder / "config.json").is_file(),
    }
    try:
        (folder / _METADATA).write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Could not describe the backup: %s", exc)

    prune_backups(keep, root=root)
    return folder


def _entry(folder: Path) -> dict:
    metadata = {}
    try:
        metadata = json.loads((folder / _METADATA).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "path": str(folder),
        "name": folder.name,
        "created_at": metadata.get("created_at") or folder.name,
        "reason": metadata.get("reason") or "",
        "version": metadata.get("version") or "",
        "tracks": metadata.get("tracks", 0),
        "settings": bool(metadata.get("settings", (folder / "config.json").is_file())),
    }


def list_backups(root: Optional[Path] = None) -> list[dict]:
    """Newest first. Unreadable folders are skipped, not raised."""
    parent = backups_dir(root)
    if not parent.is_dir():
        return []
    entries = []
    for folder in parent.iterdir():
        if folder.is_dir() and (folder / "library.db").is_file():
            entries.append(_entry(folder))
    entries.sort(key=lambda entry: (entry["created_at"], entry["name"]),
                 reverse=True)
    return entries


def prune_backups(keep: int = KEEP_BACKUPS, *, root: Optional[Path] = None) -> None:
    entries = list_backups(root)
    for entry in entries[max(0, keep):]:
        shutil.rmtree(entry["path"], ignore_errors=True)


def validate_backup(folder: Path, *, root: Optional[Path] = None) -> None:
    """Raise BackupError unless this is a usable library snapshot.

    Only folders inside the backup directory are accepted, so a damaged
    restore request can never copy an arbitrary file over the library.
    """
    path = Path(folder).expanduser().resolve()
    parent = backups_dir(root).resolve()
    if path != parent and parent not in path.parents:
        raise BackupError("That folder is not a Music DLP backup.")
    database = path / "library.db"
    if not database.is_file():
        raise BackupError("That backup has no library file.")
    try:
        connection = sqlite3.connect(
            f"file:{database.as_posix()}?mode=ro", uri=True
        )
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if not result or str(result[0]).lower() != "ok":
                raise BackupError("That backup's library file is damaged.")
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "tracks" not in tables or "schema_meta" not in tables:
                raise BackupError(
                    "That folder does not contain a Music DLP library."
                )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise BackupError(f"That backup could not be read: {exc}") from exc


def request_restore(folder: Path, *, root: Optional[Path] = None) -> Path:
    """Ask the next start to restore this backup."""
    validate_backup(folder, root=root)
    path = restore_request_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "source": str(Path(folder).expanduser().resolve()),
                "requested_at": _now().isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def pending_restore(*, root: Optional[Path] = None) -> Optional[Path]:
    path = restore_request_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        _clear_request(root)
        return None
    try:
        folder = Path(str(data.get("source") or ""))
    except (TypeError, ValueError):
        _clear_request(root)
        return None
    try:
        validate_backup(folder, root=root)
    except BackupError as exc:
        logger.warning("Ignoring an invalid restore request: %s", exc)
        _clear_request(root)
        return None
    return folder


def clear_restore_request(root: Optional[Path] = None) -> None:
    _clear_request(root)


def _clear_request(root: Optional[Path]) -> None:
    try:
        restore_request_path(root).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not clear the restore request: %s", exc)


def apply_pending_restore(
    *,
    database: Optional[Path] = None,
    config: Optional[Path] = None,
    root: Optional[Path] = None,
) -> Optional[str]:
    """Apply a requested restore before the library is opened.

    Returns a message for the start-up log, or None when there was
    nothing to do. The current library is kept beside the new one, never
    deleted, so a wrong choice can still be undone.
    """
    folder = pending_restore(root=root)
    if folder is None:
        return None

    target = Path(database) if database is not None else db_path()
    settings_file = Path(config) if config is not None else config_path()
    stamp = _timestamp()

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            kept = target.with_name(f"{target.name}.before-restore-{stamp}")
            shutil.copy2(target, kept)
        # A stale WAL beside the replaced file would be applied to the
        # restored database and undo the restore.
        for suffix in ("-wal", "-shm"):
            sidecar = target.with_name(target.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        shutil.copy2(folder / "library.db", target)

        if (folder / "config.json").is_file():
            if settings_file.exists():
                kept_config = settings_file.with_name(
                    f"{settings_file.name}.before-restore-{stamp}"
                )
                shutil.copy2(settings_file, kept_config)
            shutil.copy2(folder / "config.json", settings_file)
    except OSError as exc:
        logger.warning("Could not restore the backup: %s", exc)
        _clear_request(root)
        return f"The backup could not be restored: {exc}"

    _clear_request(root)
    entry = _entry(folder)
    return (
        "Restored the library from the backup made "
        f"{entry['created_at']} ({entry['tracks']} songs). "
        "The previous library was kept beside it."
    )


def maybe_daily_backup(
    *,
    database: Optional[Path] = None,
    config: Optional[Path] = None,
    root: Optional[Path] = None,
    hours: int = 24,
) -> Optional[Path]:
    """Take one automatic snapshot a day, at most.

    This is the copy a mistaken delete or a damaged database can fall
    back to when the user never pressed "Back up now".
    """
    entries = list_backups(root)
    if entries:
        newest = entries[0].get("created_at") or ""
        try:
            made = datetime.fromisoformat(newest)
            if made.tzinfo is None:
                made = made.replace(tzinfo=timezone.utc)
            if (_now() - made).total_seconds() < hours * 3600:
                return None
        except ValueError:
            pass
    return create_backup("daily", database=database, config=config, root=root)