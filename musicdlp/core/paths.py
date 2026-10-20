"""Filesystem locations used by the app.

Everything writable lives outside the install directory by default, so the
app works when installed under Program Files. A ``portable.txt`` file next to
the executable switches to an all-in-one portable layout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from musicdlp import APP_NAME


def _home_override() -> Path | None:
    """Isolated home for tests, demos, and parallel runs.

    Setting MUSICDLP_HOME keeps a test run from touching the real user
    configuration, library, or logs.
    """
    value = os.environ.get("MUSICDLP_HOME")
    return Path(value).expanduser() if value else None


def is_frozen() -> bool:
    """True when running from a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Directory containing the executable, or the repository root."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def is_portable() -> bool:
    """True when a portable.txt marker sits next to the executable."""
    return (app_root() / "portable.txt").exists()


def data_dir() -> Path:
    """Persistent data (config, database)."""
    override = _home_override()
    if override is not None:
        return override / "data"
    if is_portable():
        return app_root() / "data"
    base = os.environ.get("APPDATA")
    return (Path(base) / APP_NAME) if base else (Path.home() / ".musicdlp")


def local_dir() -> Path:
    """Machine-local data (logs, caches)."""
    override = _home_override()
    if override is not None:
        return override / "local"
    if is_portable():
        return app_root() / "data"
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / APP_NAME) if base else (Path.home() / ".musicdlp")


def config_path() -> Path:
    return data_dir() / "config.json"


def db_path() -> Path:
    return data_dir() / "library.db"


def log_dir() -> Path:
    return local_dir() / "logs"


def cache_dir() -> Path:
    return local_dir() / "cache"


def art_cache_dir() -> Path:
    return cache_dir() / "art"


def default_output_dir() -> Path:
    """Where converted files go unless the user chooses otherwise.

    Kept outside the user's Music and Downloads folders on purpose:
    Spotify scans both of those by default, and mixing these songs in with
    everything else there makes Local Files confusing.
    """
    return Path.home() / "MusicDLP"


def default_spotify_dir() -> Path:
    """Default folder to add as a Spotify Local Files source."""
    return default_output_dir() / "Spotify"


def ensure_dir(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_app_dirs() -> None:
    for path in (data_dir(), local_dir(), log_dir(), cache_dir(), art_cache_dir()):
        ensure_dir(path)


def is_writable(path: Path) -> bool:
    """Check that a directory exists (or can be created) and is writable."""
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False
