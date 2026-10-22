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
from typing import Optional

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.models import DOWNLOADED
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

            replacement = _find_by_name(
                settings.output_path, path.name,
                _search_roots(settings, path),
            )
            if replacement is not None:
                updates[field] = str(replacement)
            else:
                updates[field] = None
                lost = True

        if not updates:
            continue

        if lost:
            missing += 1
            raw = track.raw_path and Path(track.raw_path).exists()
            if raw:
                # The audio is still here, so the file can be rebuilt
                # without downloading anything.
                updates["status"] = DOWNLOADED
                updates["needs_update"] = 1
                updates["synced_at"] = None
                updates["sync_error"] = None
            else:
                # The file is gone and there is nothing to rebuild it from.
                # The song was downloaded once, so saying it never was is
                # wrong and loses that history. Keep any path that was
                # found elsewhere, drop the dead ones, and leave the status
                # alone so the list shows "File missing" and Retry can
                # fetch it again.
                updates = {
                    field: value for field, value in updates.items()
                    if value is not None
                }
                if not updates:
                    continue

        db.update_track(track.id, **updates)

    if missing:
        notes.append(
            f"{missing} song(s) are missing their audio file. They are "
            "marked so you can fetch them again with Retry."
        )
    return notes


def _search_roots(settings: Settings, path: Path) -> list[Path]:
    """Every folder a moved song might now live in.

    A library that was made on another computer, or whose output folder was
    changed, keeps absolute paths that no longer resolve. Looking only
    under the current output folder then declares every song lost, which
    is what made an entire older library show as "not downloaded".
    """
    roots: list[Path] = []
    for candidate in (
        path.parent,                      # cheapest: where it used to be
        settings.output_path,
        settings.spotify_path,
        default_output_dir(),
        default_spotify_dir(),
    ):
        try:
            if candidate and candidate.exists() and candidate not in roots:
                roots.append(candidate)
        except OSError:
            continue
    return roots


def _find_by_name(
    root: Path, name: str, extra_roots: Optional[list[Path]] = None
) -> Path | None:
    roots = [root, *(extra_roots or [])]
    for candidate_root in roots:
        if not candidate_root.exists():
            continue
        direct = candidate_root / name
        if direct.is_file():
            return direct
    for candidate_root in roots:
        if not candidate_root.exists():
            continue
        try:
            for candidate in candidate_root.rglob(name):
                if candidate.is_file():
                    return candidate
        except OSError:
            continue
    return None


def rewrite_tags(db: Database, settings: Settings) -> list[str]:
    """Re-write tags and artwork into existing output files.

    Used after the tag format changes, or when a player shows songs without
    their picture. No audio is converted; only the tag block is replaced.
    """
    from musicdlp.services.tagging import embed_tags

    notes: list[str] = []
    rewritten = 0
    failed = 0

    for track in db.get_all_tracks():
        paths = [Path(p) for p in (track.mp3_path, track.m4a_path) if p]
        paths = [p for p in paths if p.exists()]

        # The Spotify folder holds copies. They need the same tags, or
        # Spotify keeps showing whatever it read the first time.
        copies = [
            settings.spotify_path / path.name for path in paths
        ]
        paths.extend(copy for copy in copies if copy.exists())

        if not paths:
            continue

        cover = (
            Path(track.cover_path)
            if track.cover_path and Path(track.cover_path).exists()
            else None
        )

        album = track.display_album or "Singles"
        track_no = track.display_track
        ok = False
        for path in paths:
            ok = embed_tags(
                path,
                title=track.display_title,
                artist=track.display_artist or "Unknown",
                album=album,
                track=track_no,
                cover_path=cover,
            ) or ok
        if ok:
            rewritten += 1
        else:
            failed += 1

    if rewritten:
        notes.append(f"Updated the details and artwork in {rewritten} song(s).")
    if failed:
        notes.append(f"{failed} song(s) could not be updated.")
    return notes


def refresh_spotify_index(spotify_running_check=None) -> tuple[bool, str]:
    """Make Spotify rebuild its list of local files.

    Spotify caches which files it has seen, including artwork. Removing the
    index while Spotify is closed forces a full re-read on the next start.
    """
    import os

    appdata = os.environ.get("APPDATA")
    if not appdata:
        return False, "Spotify's data folder could not be found."

    removed = 0
    users = Path(appdata) / "Spotify" / "Users"
    if users.exists():
        for index_file in users.glob("*/local-files.bnk"):
            try:
                index_file.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Could not remove %s: %s", index_file, exc)

    if not removed:
        return False, (
            "No Spotify index was found to reset. Add the folder in Spotify "
            "under Settings, Local files."
        )
    return True, (
        "Spotify's list of local files was reset. It will re-read the "
        "folder, including artwork, the next time it starts."
    )
