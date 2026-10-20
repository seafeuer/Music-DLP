"""Import the previous app's music_dlp.db into the new library."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from musicdlp.core.db import Database
from musicdlp.core.models import CONVERTED, DOWNLOADED, ERROR, NEW
from musicdlp.core.paths import app_root, data_dir
from musicdlp.services.urls import classify_url

logger = logging.getLogger("musicdlp.legacy")


@dataclass
class ImportSummary:
    sources: int = 0
    tracks: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.tracks or self.sources)


def find_legacy_db() -> Optional[Path]:
    candidates = [
        app_root() / "music_dlp.db",
        data_dir() / "music_dlp.db",
        Path.cwd() / "music_dlp.db",
    ]
    for candidate in candidates:
        if candidate.exists() and _looks_like_legacy(candidate):
            return candidate
    return None


def _looks_like_legacy(path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            return {"playlists", "videos"}.issubset(tables)
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def import_legacy(db: Database, old_path: Path) -> ImportSummary:
    old_path = Path(old_path)
    if old_path.resolve() == db.path.resolve():
        raise ValueError("That is the current library database, not an old one.")
    if not _looks_like_legacy(old_path):
        raise ValueError("That file does not look like an old Music-DLP database.")

    summary = ImportSummary()
    base_dir = old_path.parent
    conn = sqlite3.connect(f"file:{old_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    try:
        approved = {
            row["video_id"]: row
            for row in conn.execute("SELECT * FROM approved_metadata")
        }
        videos = {
            row["video_id"]: row
            for row in conn.execute("SELECT * FROM videos")
        }
        links_by_playlist: dict[str, list[sqlite3.Row]] = {}
        linked_video_ids: set[str] = set()

        for row in conn.execute(
            "SELECT * FROM playlist_videos ORDER BY playlist_id, position"
        ):
            links_by_playlist.setdefault(row["playlist_id"], []).append(row)
            linked_video_ids.add(row["video_id"])

        for playlist in conn.execute("SELECT * FROM playlists"):
            try:
                source_id = _import_playlist(
                    db, playlist, links_by_playlist.get(playlist["playlist_id"], []),
                    videos, approved, base_dir, summary,
                )
                if source_id:
                    summary.sources += 1
            except Exception as exc:
                logger.exception("Could not import playlist %s", playlist["playlist_id"])
                summary.errors.append(f"Playlist {playlist['playlist_id']}: {exc}")

        orphans = [vid for vid in videos if vid not in linked_video_ids]
        if orphans:
            source_id = db.add_source(
                url="legacy://singles",
                kind="playlist",
                platform="other",
                external_id=None,
                title="Imported singles",
                uploader="",
                item_count=len(orphans),
                thumbnail_url=None,
            )
            summary.sources += 1
            links = []
            for position, video_id in enumerate(orphans, start=1):
                track_id = _import_track(
                    db, videos[video_id], approved.get(video_id),
                    base_dir, summary,
                )
                if track_id:
                    links.append((track_id, position))
            db.set_source_tracks(source_id, links)

    finally:
        conn.close()

    logger.info(
        "Legacy import: %d sources, %d tracks, %d errors",
        summary.sources, summary.tracks, len(summary.errors),
    )
    return summary


def _import_playlist(
    db: Database,
    playlist: sqlite3.Row,
    links: list[sqlite3.Row],
    videos: dict[str, sqlite3.Row],
    approved: dict[str, sqlite3.Row],
    base_dir: Path,
    summary: ImportSummary,
) -> Optional[int]:
    old_url = (playlist["url"] or "").strip()
    playlist_id = playlist["playlist_id"] or ""
    if not old_url:
        if playlist_id.startswith(("PL", "UU", "FL", "LL", "RD")):
            old_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        else:
            old_url = f"legacy://playlist/{playlist_id}"

    info = classify_url(old_url)
    platform = info.platform if info else "other"

    source_id = db.add_source(
        url=old_url,
        kind="playlist",
        platform=platform,
        external_id=playlist_id or None,
        title=playlist["title"] or "Imported playlist",
        uploader=playlist["uploader"] or "",
        item_count=len(links),
        thumbnail_url=None,
    )

    source_links = []
    for link in links:
        row = videos.get(link["video_id"])
        if row is None:
            summary.skipped += 1
            continue
        track_id = _import_track(
            db, row, approved.get(row["video_id"]), base_dir, summary
        )
        if track_id:
            source_links.append((track_id, int(link["position"] or len(source_links) + 1)))

    db.set_source_tracks(source_id, source_links)
    return source_id


def _import_track(
    db: Database,
    row: sqlite3.Row,
    approved: Optional[sqlite3.Row],
    base_dir: Path,
    summary: ImportSummary,
) -> Optional[int]:
    video_id = row["video_id"]
    if not video_id:
        summary.skipped += 1
        return None

    old_url = (row["url"] or "").strip()
    if not old_url:
        old_url = f"https://www.youtube.com/watch?v={video_id}"
    info = classify_url(old_url)
    platform = info.platform if info else "other"

    source_index = None
    if "source_index" in row.keys():
        source_index = row["source_index"]

    track_id = db.upsert_track(
        platform=platform,
        media_id=video_id,
        url=old_url,
        title=row["title"] or "Untitled",
        uploader=row["uploader"] or None,
        duration=row["duration"] or 0,
        thumbnail_url=row["thumbnail_url"] or None,
        description=row["description"] or None,
        source_index=source_index,
    )
    summary.tracks += 1

    raw_path = _resolve_path(row["file_path_raw"], base_dir)
    mp3_path = _resolve_path(approved["file_path_mp3"], base_dir) if approved else None
    m4a_path = _resolve_path(approved["file_path_m4a"], base_dir) if approved else None
    cover_path = _resolve_path(approved["thumbnail_path"], base_dir) if approved else None

    conversion_status = approved["conversion_status"] if approved else None
    download_status = row["download_status"] or "pending"

    if conversion_status == "completed" and (mp3_path or m4a_path):
        status = CONVERTED
    elif download_status == "downloaded" and raw_path:
        status = DOWNLOADED
    elif download_status == "error":
        status = ERROR
    else:
        status = NEW

    update: dict = {
        "status": status,
        "raw_path": raw_path,
        "mp3_path": mp3_path,
        "m4a_path": m4a_path,
        "cover_path": cover_path,
        "error": row["download_error"] if status == ERROR else None,
    }

    if approved:
        update["meta_title"] = approved["final_title"] or None
        update["meta_artist"] = approved["final_artist"] or None
        update["meta_album"] = approved["album"] or None
        if approved["approval_date"]:
            update["converted_at"] = approved["approval_date"]
    if row["download_date"]:
        update["downloaded_at"] = row["download_date"]

    db.update_track(track_id, **update)
    return track_id


def _resolve_path(value, base_dir: Path) -> Optional[str]:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return str(path) if path.exists() else None
