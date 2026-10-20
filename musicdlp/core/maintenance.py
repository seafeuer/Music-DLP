"""Self-repair for a library whose files or folders moved.

Two things can leave a library pointing at files that no longer exist:

* the output or Spotify folder was changed, and
* a build wrote into a temporary folder that was later cleaned up.

Both are recoverable. ``repair_library`` moves anything it can find to the
current folders, updates the stored paths, and marks tracks whose audio is
genuinely gone so they can be fetched again.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.models import CONVERTED, DOWNLOADED, NEW, SYNCED
from musicdlp.core.paths import default_output_dir, default_spotify_dir

logger = logging.getLogger("musicdlp.maintenance")


def is_temporary(path: Path) -> bool:
    """True when the folder lives in the OS temp directory."""
    try:
        temp = Path(tempfile.gettempdir()).resolve()
        return temp in Path(path).resolve().parents
    except OSError:
        return False


def repair_library(db: Database, settings: Settings) -> list[str]:
    """Fix stale folders and missing outputs. Returns human-readable notes."""
    notes: list[str] = []
    notes.extend(_repair_folders(db, settings))
    notes.extend(_repair_track_files(db, settings))
    return notes


def _repair_folders(db: Database, settings: Settings) -> list[str]:
    notes: list[str] = []
    changed = False

    output = settings.output_path
    if is_temporary(output):
        target = default_output_dir()
        _move_tree(output, target)
        settings.output_dir = str(target)
        changed = True
        notes.append(f"Moved your songs out of a temporary folder into {target}")

    spotify = settings.spotify_path
    if is_temporary(spotify):
        target = default_spotify_dir()
        _move_tree(spotify, target)
        settings.spotify_dir = str(target)
        changed = True
        notes.append(f"Moved the Spotify folder to {target}")

    if changed:
        try:
            settings.save()
        except OSError as exc:
            logger.warning("Could not save repaired settings: %s", exc)

    # Remap stored paths that pointed into the old output folder.
    for track in db.get_all_tracks():
        updates = {}
        for field in ("mp3_path", "m4a_path"):
            value = getattr(track, field)
            if not value:
                continue
            path = Path(value)
            if output in path.parents and output != settings.output_path:
                try:
                    relative = path.relative_to(output)
                except ValueError:
                    continue
                updates[field] = str(settings.output_path / relative)
        if updates:
            db.update_track(track.id, **updates)

    return notes


def _move_tree(source: Path, target: Path) -> None:
    """Move a folder's contents into the target, merging where possible."""
    if not source.exists() or source == target:
        return
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        try:
            if item.is_dir():
                if destination.exists():
                    _move_tree(item, destination)
                else:
                    shutil.move(str(item), str(destination))
            elif not destination.exists():
                shutil.move(str(item), str(destination))
        except OSError as exc:
            logger.warning("Could not move %s: %s", item, exc)


def _repair_track_files(db: Database, settings: Settings) -> list[str]:
    """Find or re-flag tracks whose converted files are missing."""
    notes: list[str] = []
    missing = 0
    for track in db.get_all_tracks():
        outputs = {
            "mp3_path": track.mp3_path,
            "m4a_path": track.m4a_path,
        }
        updates: dict = {}
        lost = False

        for field, value in outputs.items():
            if not value:
                continue
            path = Path(value)
            if path.exists():
                continue

            replacement = _find_by_name(settings.output_path, path.name)
            if replacement is not None:
                updates[field] = str(replacement)
            else:
                updates[field] = None
                lost = True

        if not updates:
            continue

        if lost:
            missing += 1
            updates["synced_at"] = None
            updates["sync_error"] = None
            raw = track.raw_path and Path(track.raw_path).exists()
            if raw:
                updates["status"] = DOWNLOADED
                updates["needs_update"] = 1
            elif track.status in (CONVERTED, SYNCED):
                updates["status"] = NEW
                updates["needs_update"] = 0

        db.update_track(track.id, **updates)

    if missing:
        notes.append(
            f"{missing} song(s) were missing their audio file and will be "
            "downloaded again."
        )
    return notes


def _find_by_name(root: Path, name: str) -> Path | None:
    if not root.exists():
        return None
    for candidate in root.rglob(name):
        if candidate.is_file():
            return candidate
    return None
