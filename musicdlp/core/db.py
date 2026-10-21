"""SQLite storage with per-thread connections.

A single connection shared between the UI and worker threads is unsafe:
transactions are connection-wide, so one thread's rollback can discard
another thread's write. Every thread therefore gets its own connection, and
the database runs in WAL mode so readers and the writer do not block each
other.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

from musicdlp.core.models import Source, Track, TrackRow

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL,
    platform      TEXT NOT NULL,
    external_id   TEXT,
    title         TEXT NOT NULL,
    uploader      TEXT,
    item_count    INTEGER NOT NULL DEFAULT 0,
    thumbnail_url TEXT,
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    refreshed_at  TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,
    media_id      TEXT NOT NULL,
    url           TEXT NOT NULL,
    source_index  INTEGER,
    title         TEXT NOT NULL,
    uploader      TEXT,
    duration      INTEGER,
    thumbnail_url TEXT,
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'new',
    error         TEXT,
    meta_title    TEXT,
    meta_artist   TEXT,
    meta_album    TEXT,
    meta_track    INTEGER,
    raw_path      TEXT,
    mp3_path      TEXT,
    m4a_path      TEXT,
    cover_path    TEXT,
    downloaded_at TEXT,
    converted_at  TEXT,
    synced_at     TEXT,
    sync_error    TEXT,
    needs_update  INTEGER NOT NULL DEFAULT 0,
    smart_title   TEXT,
    smart_artist  TEXT,
    smart_confidence REAL,
    smart_reason  TEXT,
    UNIQUE(platform, media_id)
);

CREATE TABLE IF NOT EXISTS source_tracks (
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    track_id  INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    PRIMARY KEY (source_id, track_id)
);

CREATE INDEX IF NOT EXISTS idx_source_tracks_source
    ON source_tracks(source_id, position);
CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);
"""

TRACK_UPDATABLE = {
    "status", "error", "meta_title", "meta_artist", "meta_album", "meta_track",
    "raw_path", "mp3_path", "m4a_path", "cover_path",
    "downloaded_at", "converted_at", "synced_at", "sync_error",
    "needs_update", "smart_title", "smart_artist", "smart_confidence",
    "smart_reason",
}

SOURCE_UPDATABLE = {
    "title", "uploader", "item_count", "thumbnail_url", "refreshed_at",
}


class Database:
    """Small repository layer over SQLite."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._closed = False
        self._init_schema()

    # ------------------------------------------------------------ connection

    def _conn(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("Database is closed")
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.path),
                timeout=5.0,
                check_same_thread=True,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def close_current_thread(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    def close(self) -> None:
        """Close the calling thread's connection and stop new work.

        Connections owned by worker threads are closed when those threads
        end; SQLite tolerates the process exiting with them open.
        """
        self.close_current_thread()
        self._closed = True

    def _init_schema(self) -> None:
        conn = self._conn()
        conn.executescript(SCHEMA_SQL)
        self._migrate(conn)
        row = conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,)
            )
        conn.commit()
        logger.debug("Database ready at %s", self.path)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the first schema revision."""
        columns = [row[1] for row in conn.execute("PRAGMA table_info(tracks)")]
        if "sync_error" not in columns:
            conn.execute("ALTER TABLE tracks ADD COLUMN sync_error TEXT")
            logger.info("Migration: added tracks.sync_error")
        if "needs_update" not in columns:
            conn.execute(
                "ALTER TABLE tracks ADD COLUMN needs_update INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Migration: added tracks.needs_update")
        for column, kind in (
            ("smart_title", "TEXT"),
            ("smart_artist", "TEXT"),
            ("smart_confidence", "REAL"),
            ("smart_reason", "TEXT"),
        ):
            if column not in columns:
                conn.execute(f"ALTER TABLE tracks ADD COLUMN {column} {kind}")
                logger.info("Migration: added tracks.%s", column)

    # --------------------------------------------------------------- sources

    def add_source(
        self,
        url: str,
        kind: str,
        platform: str,
        external_id: Optional[str],
        title: str,
        uploader: Optional[str],
        item_count: int,
        thumbnail_url: Optional[str],
    ) -> int:
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT INTO sources
                    (url, kind, platform, external_id, title, uploader,
                     item_count, thumbnail_url, refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(url) DO UPDATE SET
                    kind = excluded.kind,
                    platform = excluded.platform,
                    external_id = excluded.external_id,
                    title = excluded.title,
                    uploader = excluded.uploader,
                    item_count = excluded.item_count,
                    thumbnail_url = excluded.thumbnail_url,
                    refreshed_at = datetime('now')
                """,
                (url, kind, platform, external_id, title, uploader,
                 item_count, thumbnail_url),
            )
            row = conn.execute(
                "SELECT id FROM sources WHERE url = ?", (url,)
            ).fetchone()
        return int(row["id"])

    def get_source(self, source_id: int) -> Optional[Source]:
        row = self._conn().execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        return Source.from_row(row) if row else None

    def get_source_by_url(self, url: str) -> Optional[Source]:
        row = self._conn().execute(
            "SELECT * FROM sources WHERE url = ?", (url,)
        ).fetchone()
        return Source.from_row(row) if row else None

    def get_sources(self) -> list[Source]:
        rows = self._conn().execute(
            "SELECT * FROM sources ORDER BY added_at DESC, id DESC"
        ).fetchall()
        return [Source.from_row(row) for row in rows]

    def update_source(self, source_id: int, **fields: Any) -> None:
        values = {k: v for k, v in fields.items() if k in SOURCE_UPDATABLE}
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        conn = self._conn()
        with conn:
            conn.execute(
                f"UPDATE sources SET {assignments} WHERE id = ?",
                (*values.values(), source_id),
            )

    def delete_source(self, source_id: int) -> None:
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))

    # ---------------------------------------------------------------- tracks

    def upsert_track(
        self,
        platform: str,
        media_id: str,
        url: str,
        title: str,
        uploader: Optional[str] = None,
        duration: Optional[int] = None,
        thumbnail_url: Optional[str] = None,
        description: Optional[str] = None,
        source_index: Optional[int] = None,
    ) -> int:
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT INTO tracks
                    (platform, media_id, url, source_index, title, uploader,
                     duration, thumbnail_url, description, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
                ON CONFLICT(platform, media_id) DO UPDATE SET
                    url = excluded.url,
                    source_index = COALESCE(
                        excluded.source_index, tracks.source_index
                    ),
                    title = excluded.title,
                    uploader = excluded.uploader,
                    duration = excluded.duration,
                    thumbnail_url = excluded.thumbnail_url,
                    description = excluded.description
                """,
                (platform, media_id, url, source_index, title, uploader,
                 duration, thumbnail_url, description),
            )
            row = conn.execute(
                "SELECT id FROM tracks WHERE platform = ? AND media_id = ?",
                (platform, media_id),
            ).fetchone()
        return int(row["id"])

    def get_track(self, track_id: int) -> Optional[Track]:
        row = self._conn().execute(
            "SELECT * FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        return Track.from_row(row) if row else None

    def get_track_by_media(self, platform: str, media_id: str) -> Optional[Track]:
        row = self._conn().execute(
            "SELECT * FROM tracks WHERE platform = ? AND media_id = ?",
            (platform, media_id),
        ).fetchone()
        return Track.from_row(row) if row else None

    def get_all_tracks(self) -> list[Track]:
        rows = self._conn().execute("SELECT * FROM tracks ORDER BY id").fetchall()
        return [Track.from_row(row) for row in rows]

    def update_track(self, track_id: int, **fields: Any) -> None:
        values = {k: v for k, v in fields.items() if k in TRACK_UPDATABLE}
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        conn = self._conn()
        with conn:
            cur = conn.execute(
                f"UPDATE tracks SET {assignments} WHERE id = ?",
                (*values.values(), track_id),
            )
            if cur.rowcount == 0:
                logger.warning("Track %s disappeared during update", track_id)

    def delete_track(self, track_id: int) -> None:
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))

    # -------------------------------------------------------- source <-> track

    def set_source_tracks(
        self, source_id: int, items: Iterable[tuple[int, int]]
    ) -> None:
        """Replace the source's track list atomically.

        Tracks themselves are never deleted here: a track may belong to other
        sources, and its files must survive a playlist refresh.
        """
        items = list(items)
        conn = self._conn()
        with conn:
            conn.execute(
                "DELETE FROM source_tracks WHERE source_id = ?", (source_id,)
            )
            conn.executemany(
                "INSERT INTO source_tracks (source_id, track_id, position) "
                "VALUES (?, ?, ?)",
                [(source_id, track_id, position)
                 for track_id, position in items],
            )
            conn.execute(
                "UPDATE sources SET item_count = ? WHERE id = ?",
                (len(items), source_id),
            )

    def get_tracks_for_source(self, source_id: int) -> list[TrackRow]:
        source = self.get_source(source_id)
        if source is None:
            return []
        rows = self._conn().execute(
            """
            SELECT t.*, st.position AS position
            FROM source_tracks st
            JOIN tracks t ON t.id = st.track_id
            WHERE st.source_id = ?
            ORDER BY st.position, t.id
            """,
            (source_id,),
        ).fetchall()
        return [
            TrackRow(source=source, track=Track.from_row(row),
                     position=int(row["position"]))
            for row in rows
        ]

    def status_counts(self, source_id: int) -> dict[str, int]:
        rows = self._conn().execute(
            """
            SELECT t.status AS status, COUNT(*) AS count
            FROM source_tracks st
            JOIN tracks t ON t.id = st.track_id
            WHERE st.source_id = ?
            GROUP BY t.status
            """,
            (source_id,),
        ).fetchall()
        return {row["status"]: int(row["count"]) for row in rows}

    def count_source_tracks(self, source_id: int) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) AS n FROM source_tracks WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def count_track_links(self, track_id: int) -> int:
        row = self._conn().execute(
            "SELECT COUNT(*) AS n FROM source_tracks WHERE track_id = ?",
            (track_id,),
        ).fetchone()
        return int(row["n"]) if row else 0

    def known_artists(self) -> set[str]:
        """Artist names the user has confirmed.

        Smart titles use these to split titles that have no separator, so
        the parser gets better as the library grows.
        """
        rows = self._conn().execute(
            "SELECT DISTINCT meta_artist FROM tracks "
            "WHERE meta_artist IS NOT NULL AND TRIM(meta_artist) <> ''"
        ).fetchall()
        names: set[str] = set()
        for row in rows:
            for part in str(row["meta_artist"]).split(","):
                part = part.strip()
                if len(part) >= 3:
                    names.add(part)
        return names

    def unlink_track_everywhere(self, track_id: int) -> None:
        """Remove a track from every source and refresh those counts."""
        conn = self._conn()
        with conn:
            rows = conn.execute(
                "SELECT source_id FROM source_tracks WHERE track_id = ?",
                (track_id,),
            ).fetchall()
            source_ids = [int(row["source_id"]) for row in rows]
            conn.execute(
                "DELETE FROM source_tracks WHERE track_id = ?", (track_id,)
            )
            for source_id in source_ids:
                conn.execute(
                    "UPDATE sources SET item_count = "
                    "(SELECT COUNT(*) FROM source_tracks WHERE source_id = ?) "
                    "WHERE id = ?",
                    (source_id, source_id),
                )

    # -------------------------------------------------------------- settings

    def set_setting(self, key: str, value: str) -> None:
        conn = self._conn()
        with conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._conn().execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default
