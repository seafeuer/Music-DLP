"""Copy finished songs into a Google Drive folder, for phones.

Google Drive's desktop app syncs a local folder, and the Drive app on iOS or
Android shows whatever is in it. Writing files there is enough to make them
reachable from a phone, with no account setup, API key, or OAuth flow.

Nothing here talks to Google. It only finds the synced folder and copies
files into it.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import string
import time
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("musicdlp.gdrive")

# The subfolder Music DLP writes into, inside the user's Drive.
SUBFOLDER = "MusicDLP"

# Names Google Drive for desktop and older "Backup and Sync" use.
_FOLDER_NAMES = ("My Drive", "Google Drive", "GoogleDrive")


def _user_home() -> Optional[Path]:
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) if home else None


def _drive_letters() -> list[str]:
    """Local drive letters that currently exist.

    Network drives are skipped on purpose: probing one that is mapped but
    disconnected blocks for the SMB timeout, which would freeze the window
    while it looks for Drive. Google Drive mounts as a local drive.
    """
    if os.name != "nt":
        return []
    try:
        import ctypes

        DRIVE_REMOTE = 4
        DRIVE_NO_ROOT_DIR = 1
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:  # noqa: BLE001 - fall back to probing every letter
        return [f"{letter}:\\" for letter in string.ascii_uppercase]

    letters = []
    for index, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << index):
            continue
        root = f"{letter}:\\"
        try:
            kind = ctypes.windll.kernel32.GetDriveTypeW(root)
        except Exception:  # noqa: BLE001
            kind = 0
        if kind in (DRIVE_REMOTE, DRIVE_NO_ROOT_DIR):
            continue
        letters.append(root)
    return letters


def candidates() -> list[Path]:
    """Every folder that could be the synced Google Drive, best first."""
    found: list[Path] = []

    # Drive for desktop mounts the account as a drive: G:\My Drive.
    for root in _drive_letters():
        for name in _FOLDER_NAMES:
            found.append(Path(root) / name)

    home = _user_home()
    if home is not None:
        # Backup and Sync put the synced files one level down, inside a
        # "My Drive" folder. The parent is only a container and is never
        # uploaded, so it is listed after the real thing.
        for name in _FOLDER_NAMES:
            found.append(home / name / "My Drive")
        for name in _FOLDER_NAMES:
            found.append(home / name)

    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


def find_drive() -> Optional[Path]:
    """The synced Google Drive folder, if one is present."""
    for path in candidates():
        try:
            if not path.is_dir():
                continue
            # "Google Drive" with a "My Drive" inside is only the container
            # that Backup and Sync wraps the real folder in. Writing there
            # never reaches Google, so skip it and use the nested folder.
            if (path / "My Drive").is_dir():
                continue
            return path
        except OSError:
            continue
    return None


def destination(configured: str = "") -> Optional[Path]:
    """Where songs should go.

    A folder set in Settings wins, unless it has gone away — a Drive that
    moved to another drive letter is common, and silently copying to a
    folder that no longer syncs would look like success. Otherwise the
    synced Drive is found automatically, and songs go into a ``MusicDLP``
    subfolder so the rest of the Drive is left alone.
    """
    chosen = (configured or "").strip()
    if chosen:
        # A folder chosen in Settings is used exactly as given.
        path = Path(chosen).expanduser()
        if _has_parent(path):
            return path
        logger.info(
            "The configured Drive folder %s is gone; looking for one", path
        )
    root = find_drive()
    if root is None:
        return None
    return root / SUBFOLDER


def _has_parent(path: Path) -> bool:
    """Whether this path can still be written to.

    The folder itself may not exist yet — it is created on the first send —
    so its parent is what has to be there. A drive letter that no longer
    exists fails this, which is the case worth catching.
    """
    try:
        return path.exists() or path.parent.exists()
    except OSError:
        return False


def is_ready(configured: str = "") -> bool:
    """Whether there is somewhere to copy to, and it can be written."""
    path = destination(configured)
    if path is None:
        return False
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return path.is_dir() and os.access(path, os.W_OK)


def _manifest_path() -> Path:
    from musicdlp.core.paths import data_dir

    return data_dir() / "gdrive_sent.json"


def _load_manifest() -> dict:
    try:
        data = json.loads(_manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_manifest(manifest: dict) -> None:
    try:
        path = _manifest_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.debug("Could not record what was sent: %s", exc)


def _free_name(directory: Path, name: str) -> Path:
    """A name in this folder that is not already taken."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = Path(name).stem, Path(name).suffix
    for index in range(2, 1000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem} ({time.time_ns()}){suffix}"


def send(
    files: Iterable[Path],
    configured: str = "",
    *,
    mode: str = "copy",
) -> tuple[int, list[str]]:
    """Copy or move files into the Drive folder.

    Returns how many were sent, and a list of problems. Sending the same
    file twice is harmless: an unchanged copy is skipped. A song whose
    details changed is copied over the old one, and two different songs
    that happen to share a name get separate files instead of one
    overwriting the other.
    """
    target_dir = destination(configured)
    if target_dir is None:
        return 0, [
            "No Google Drive folder was found. Set one in Settings, Spotify."
        ]
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return 0, [f"Cannot create {target_dir}: {exc}"]

    manifest = _load_manifest()
    changed = False
    sent = 0
    errors: list[str] = []

    for source in files:
        source = Path(source)
        if not source.exists():
            errors.append(f"Missing file: {source.name}")
            continue

        try:
            size = source.stat().st_size
            modified = source.stat().st_mtime
        except OSError as exc:
            errors.append(f"{source.name}: {exc}")
            continue

        source_key = str(source.resolve())
        record = manifest.get(source.name)
        same_song = isinstance(record, dict) and \
            record.get("source") == source_key

        target = target_dir / source.name
        if not same_song and target.exists():
            # A different song already has this name. Keep both.
            target = _free_name(target_dir, source.name)
        elif same_song and target.exists():
            # Compare against what was sent last time, not against the
            # copy's own timestamp: a file edited in place can keep its
            # size, and the phone would never see the new version.
            unchanged = (
                record.get("size") == size
                and abs(float(record.get("mtime") or 0) - modified) < 1e-6
            )
            try:
                unchanged = unchanged and target.stat().st_size == size
            except OSError:
                unchanged = False
            if unchanged and mode != "move":
                continue

        try:
            if mode == "move":
                shutil.move(str(source), str(target))
            else:
                shutil.copy2(source, target)
        except OSError as exc:
            errors.append(f"{source.name}: {exc}")
            continue

        sent += 1
        manifest[target.name] = {
            "source": source_key,
            "size": size,
            "mtime": modified,
        }
        changed = True

    if changed:
        _save_manifest(manifest)
    if sent:
        logger.info("Sent %s file(s) to %s", sent, target_dir)
    return sent, errors
