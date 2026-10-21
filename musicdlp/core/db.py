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
import uuid
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
    folder        TEXT NOT NULL DEFAULT '',
    filename_template TEXT,
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
    "smart_reason", "duration",
}

SOURCE_UPDATABLE = {
    "title", "uploader", "item_count", "thumbnail_url", "refreshed_at",
    "url", "folder", "filename_template",
}


def _match_key(text: Optional[str]) -> str:
    """A comparison form for a song name: letters and digits only."""
    cleaned = []
    for char in (text or "").lower():
        if char.isalnum():
            cleaned.append(char)
        elif cleaned and cleaned[-1] != " ":
            cleaned.append(" ")
    return "".join(cleaned).strip()


# Two copies of one upload rarely differ by more than a few seconds.
_LENGTH_TOLERANCE = 5


def _cluster_copies(rows: list[TrackRow]) -> list[list[TrackRow]]:
    """Split same-title songs into clusters that look like copies."""
    clusters: list[list[TrackRow]] = []
    for row in rows:
        placed = False
        for cluster in clusters:
            if any(_same_recording(row, other) for other in cluster):
                cluster.append(row)
                placed = True
                break
        if not placed:
            clusters.append([row])
    return clusters


def _same_recording(left: TrackRow, right: TrackRow) -> bool:
    left_artist = _match_key(left.track.display_artist)
    right_artist = _match_key(right.track.display_artist)
    if left_artist and left_artist == right_artist:
        return True

    left_length = left.track.duration or 0
    right_length = right.track.duration or 0
    if left_length > 0 and right_length > 0:
        return abs(int(left_length) - int(right_length)) <= _LENGTH_TOLERANCE
    # Without a length to compare, an artist match was the only evidence,
    # and there was none.
    return False


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

        source_columns = [
            row[1] for row in conn.execute("PRAGMA table_info(sources)")
        ]
        if "folder" not in source_columns:
            conn.execute(
                "ALTER TABLE sources ADD COLUMN folder TEXT NOT NULL DEFAULT ''"
            )
            logger.info("Migration: added sources.folder")
        if "filename_template" not in source_columns:
            conn.execute(
                "ALTER TABLE sources ADD COLUMN filename_template TEXT"
            )
            logger.info("Migration: added sources.filename_template")

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

        # Reuse an existing entry for the same playlist or video, whatever
        # URL form it was pasted in, so re-adding or refreshing a link never
        # creates a second section in the library.
        existing = self.get_source_by_identity(platform, external_id)
        if existing is not None:
            with conn:
                conn.execute(
                    """
                    UPDATE sources SET
                        url = ?, kind = ?, platform = ?, external_id = ?,
                        title = ?, uploader = ?, item_count = ?,
                        thumbnail_url = ?, refreshed_at = datetime('now')
                    WHERE id = ?
                    """,
                    (url, kind, platform, external_id, title, uploader,
                     item_count, thumbnail_url, existing.id),
                )
            return existing.id

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

    def get_source_by_identity(
        self, platform: str, external_id: Optional[str]
    ) -> Optional[Source]:
        """Find a source by the item it points at, not by its URL.

        The same playlist can be pasted with different tracking parameters,
        and re-adding it must update the existing entry rather than create a
        second section in the library.
        """
        if not external_id:
            return None
        row = self._conn().execute(
            "SELECT * FROM sources WHERE platform = ? AND external_id = ? "
            "ORDER BY id LIMIT 1",
            (platform, external_id),
        ).fetchone()
        return Source.from_row(row) if row else None

    def set_source_folder(self, source_id: int, folder: str) -> None:
        self.update_source(source_id, folder=(folder or "").strip())

    # --------------------------------------------------------------- folders
    #
    # A folder is a source row with kind 'folder'. That keeps the whole
    # pipeline (tracks, statuses, selection, shipping) working unchanged,
    # and lets a song be dragged into it like any other collection.

    def add_folder(self, name: str) -> int:
        name = (name or "").strip()
        if not name:
            raise ValueError("A folder needs a name")

        conn = self._conn()
        row = conn.execute(
            "SELECT id FROM sources WHERE kind = 'folder' "
            "AND title = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if row:
            return int(row["id"])

        url = f"folder://{uuid.uuid4().hex[:12]}"
        with conn:
            conn.execute(
                """
                INSERT INTO sources
                    (url, kind, platform, external_id, title, uploader,
                     item_count, thumbnail_url)
                VALUES (?, 'folder', 'folder', NULL, ?, '', 0, NULL)
                """,
                (url, name),
            )
            row = conn.execute(
                "SELECT id FROM sources WHERE url = ?", (url,)
            ).fetchone()
        logger.info("Created folder %r", name)
        return int(row["id"])

    def get_folder_sources(self) -> list[Source]:
        rows = self._conn().execute(
            "SELECT * FROM sources WHERE kind = 'folder' "
            "ORDER BY title COLLATE NOCASE"
        ).fetchall()
        return [Source.from_row(row) for row in rows]

    def ensure_folders(self) -> None:
        """Create a folder row for any folder name used by a link.

        Links store their folder by name; this keeps the two views in step.
        """
        for name in self.get_folders():
            self.add_folder(name)

    def move_tracks_to_folder(
        self,
        track_ids: Iterable[int],
        folder_id: int,
        *,
        keep_in_playlists: bool = False,
    ) -> int:
        """Move songs into a folder.

        With ``keep_in_playlists`` the song is added to the folder but left
        in the link it came from, so a folder can act as a view rather than
        a move. Single-video entries that become empty are deleted either
        way, so an individually added song stops having its own entry.
        """
        ids = [int(value) for value in track_ids]
        if not ids:
            return 0

        conn = self._conn()
        moved = 0
        with conn:
            affected: set[int] = set()
            for track_id in ids:
                rows = conn.execute(
                    "SELECT source_id FROM source_tracks WHERE track_id = ?",
                    (track_id,),
                ).fetchall()
                affected.update(int(row["source_id"]) for row in rows)

                if not keep_in_playlists:
                    conn.execute(
                        "DELETE FROM source_tracks "
                        "WHERE track_id = ? AND source_id <> ?",
                        (track_id, folder_id),
                    )
                present = conn.execute(
                    "SELECT 1 FROM source_tracks "
                    "WHERE source_id = ? AND track_id = ?",
                    (folder_id, track_id),
                ).fetchone()
                if present is None:
                    position = conn.execute(
                        "SELECT COALESCE(MAX(position), 0) + 1 AS n "
                        "FROM source_tracks WHERE source_id = ?",
                        (folder_id,),
                    ).fetchone()["n"]
                    conn.execute(
                        "INSERT INTO source_tracks "
                        "(source_id, track_id, position) VALUES (?, ?, ?)",
                        (folder_id, track_id, position),
                    )
                moved += 1

            affected.add(folder_id)
            self._prune_and_recount(conn, affected)

        logger.info("Moved %d song(s) into folder %s", moved, folder_id)
        return moved

    def _prune_and_recount(
        self, conn: sqlite3.Connection, source_ids: Iterable[int]
    ) -> None:
        """Drop empty one-song entries, then refresh every item count."""
        for source_id in source_ids:
            row = conn.execute(
                "SELECT kind FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
            if row is None:
                continue
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM source_tracks WHERE source_id = ?",
                (source_id,),
            ).fetchone()["n"]
            if count == 0 and row["kind"] == "item":
                conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
                continue
            conn.execute(
                "UPDATE sources SET item_count = ? WHERE id = ?",
                (count, source_id),
            )

    def delete_folder(self, folder_id: int) -> None:
        """Remove a folder. Its songs stay in the library.

        Links that pointed at the folder have their folder name cleared, so
        the folder is not immediately recreated from them.
        """
        folder = self.get_source(folder_id)
        if folder is None:
            return
        conn = self._conn()
        with conn:
            conn.execute(
                "UPDATE sources SET folder = '' "
                "WHERE folder = ? COLLATE NOCASE",
                (folder.title,),
            )
            conn.execute("DELETE FROM sources WHERE id = ?", (folder_id,))

    def get_folders(self) -> list[str]:
        rows = self._conn().execute(
            "SELECT DISTINCT folder FROM sources "
            "WHERE TRIM(folder) <> '' ORDER BY folder COLLATE NOCASE"
        ).fetchall()
        return [row["folder"] for row in rows]

    def get_folder_by_name(self, name: str) -> Optional[Source]:
        """The folder source with this title, if it exists."""
        row = self._conn().execute(
            "SELECT * FROM sources WHERE kind = 'folder' "
            "AND title = ? COLLATE NOCASE LIMIT 1",
            ((name or "").strip(),),
        ).fetchone()
        return Source.from_row(row) if row else None

    def get_sources(self) -> list[Source]:
        rows = self._conn().execute(
            "SELECT * FROM sources "
            "ORDER BY folder COLLATE NOCASE, added_at DESC, id DESC"
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

    # One row per track, joined to a single "home" source: the first one it
    # was linked to. A track can belong to several sources; for naming and
    # album purposes one is enough.
    _HOME_SOURCE_JOIN = """
        LEFT JOIN source_tracks st
               ON st.track_id = t.id
              AND NOT EXISTS (
                    SELECT 1 FROM source_tracks st2
                    WHERE st2.track_id = st.track_id
                      AND (st2.source_id < st.source_id
                           OR (st2.source_id = st.source_id
                               AND st2.position < st.position))
              )
    """

    def _rows_with_sources(self, rows: list[sqlite3.Row]) -> list[TrackRow]:
        sources = {source.id: source for source in self.get_sources()}
        result: list[TrackRow] = []
        for row in rows:
            source_id = row["source_id"]
            source = sources.get(int(source_id)) if source_id is not None else None
            if source is None:
                source = Source(
                    id=-1, url="", kind="item", platform="other",
                    external_id=None, title="Unlinked songs", uploader="",
                    item_count=0, thumbnail_url=None,
                    added_at=None, refreshed_at=None,
                )
            result.append(
                TrackRow(
                    source=source,
                    track=Track.from_row(row),
                    position=int(row["position"] or 1),
                )
            )
        return result

    def get_all_track_rows(self) -> list[TrackRow]:
        """Every song in the library, with the source it belongs to."""
        rows = self._conn().execute(
            f"""
            SELECT t.*, st.position AS position, st.source_id AS source_id
            FROM tracks t
            {self._HOME_SOURCE_JOIN}
            ORDER BY t.id
            """
        ).fetchall()
        return self._rows_with_sources(rows)

    def get_tracks_for_folder(self, folder_id: int) -> list[TrackRow]:
        """Songs filed in a folder, including those in links filed there."""
        folder = self.get_source(folder_id)
        if folder is None:
            return []

        rows = self._conn().execute(
            """
            SELECT t.*, st.position AS position, st.source_id AS source_id
            FROM source_tracks st
            JOIN tracks t ON t.id = st.track_id
            JOIN sources s ON s.id = st.source_id
            WHERE st.source_id = ?
               OR (s.kind <> 'folder'
                   AND TRIM(s.folder) <> ''
                   AND s.folder = ? COLLATE NOCASE)
            GROUP BY t.id
            ORDER BY t.id
            """,
            (folder_id, folder.title),
        ).fetchall()
        return self._rows_with_sources(rows)

    def get_track_rows_by_ids(self, track_ids: Iterable[int]) -> list[TrackRow]:
        ids = [int(value) for value in track_ids]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self._conn().execute(
            f"""
            SELECT t.*, st.position AS position, st.source_id AS source_id
            FROM tracks t
            {self._HOME_SOURCE_JOIN}
            WHERE t.id IN ({placeholders})
            ORDER BY t.id
            """,
            ids,
        ).fetchall()
        return self._rows_with_sources(rows)

    def all_status_counts(self) -> dict[str, int]:
        rows = self._conn().execute(
            "SELECT status, COUNT(*) AS n FROM tracks GROUP BY status"
        ).fetchall()
        return {row["status"]: int(row["n"]) for row in rows}

    def count_tracks(self) -> int:
        row = self._conn().execute("SELECT COUNT(*) AS n FROM tracks").fetchone()
        return int(row["n"]) if row else 0

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

    def track_links(self, track_id: int) -> list[tuple[int, int]]:
        """The sources a track belongs to, with its position in each.

        Used to put a song back where it was after a move or a removal.
        """
        rows = self._conn().execute(
            "SELECT source_id, position FROM source_tracks WHERE track_id = ? "
            "ORDER BY source_id",
            (int(track_id),),
        ).fetchall()
        return [(int(row["source_id"]), int(row["position"])) for row in rows]

    def restore_track_links(
        self, track_id: int, links: Iterable[tuple[int, int]]
    ) -> None:
        """Put a track back into exactly the sources and positions given.

        If none of those sources still exists there is nothing to restore,
        and the song is left where it is rather than unlinked from
        everything.
        """
        track_id = int(track_id)
        wanted = [(int(source_id), int(position)) for source_id, position in links]
        conn = self._conn()
        existing = {
            int(row["id"])
            for row in conn.execute("SELECT id FROM sources").fetchall()
        }
        usable = [
            (source_id, position)
            for source_id, position in wanted
            if source_id in existing
        ]
        if not usable:
            logger.info(
                "Nothing to restore for track %s: its sources are gone", track_id
            )
            return

        with conn:
            conn.execute(
                "DELETE FROM source_tracks WHERE track_id = ?", (track_id,)
            )
            for source_id, position in usable:
                conn.execute(
                    "INSERT INTO source_tracks (source_id, track_id, position) "
                    "VALUES (?, ?, ?)",
                    (source_id, track_id, position),
                )
            self._prune_and_recount(
                conn, {source_id for source_id, _p in usable}
            )

    def restore_track(self, track: Track) -> None:
        """Put a deleted track row back, keeping its id.

        Used by undo after a duplicate is removed from the library.
        """
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT INTO tracks
                    (id, platform, media_id, url, source_index, title,
                     uploader, duration, thumbnail_url, description, status,
                     error, meta_title, meta_artist, meta_album, meta_track,
                     raw_path, mp3_path, m4a_path, cover_path, downloaded_at,
                     converted_at, synced_at, sync_error, needs_update,
                     smart_title, smart_artist, smart_confidence, smart_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    track.id, track.platform, track.media_id, track.url,
                    track.source_index, track.title, track.uploader,
                    track.duration, track.thumbnail_url, track.description,
                    track.status, track.error, track.meta_title,
                    track.meta_artist, track.meta_album, track.meta_track,
                    track.raw_path, track.mp3_path, track.m4a_path,
                    track.cover_path, track.downloaded_at, track.converted_at,
                    track.synced_at, track.sync_error,
                    1 if track.needs_update else 0, track.smart_title,
                    track.smart_artist, track.smart_confidence,
                    track.smart_reason,
                ),
            )

    def restore_source(self, source: Source) -> None:
        """Put a deleted source back, keeping its id.

        Moving the only song out of a one-song link deletes that link. An
        undo has to recreate it before the song can be linked back.
        """
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT INTO sources
                    (id, url, kind, platform, external_id, title, uploader,
                     item_count, thumbnail_url, folder, filename_template,
                     added_at, refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    source.id, source.url, source.kind, source.platform,
                    source.external_id, source.title, source.uploader,
                    source.item_count, source.thumbnail_url,
                    source.folder or "", source.filename_template,
                    source.added_at, source.refreshed_at,
                ),
            )

    def find_duplicate_groups(self) -> list[list[TrackRow]]:
        """Songs that look like the same recording, grouped together.

        Two different uploads of one song have different media ids, so this
        compares the names people see: title first, ignoring case and
        punctuation. Within one title, two songs are treated as copies when
        the artist matches, or when their lengths are within a few seconds —
        the same upload through two channels often has a different channel
        name but the same runtime.
        """
        by_title: dict[str, list[TrackRow]] = {}
        for row in self.get_all_track_rows():
            title = _match_key(row.track.display_title)
            if not title:
                continue
            by_title.setdefault(title, []).append(row)

        groups: list[list[TrackRow]] = []
        for rows in by_title.values():
            if len(rows) < 2:
                continue
            for cluster in _cluster_copies(rows):
                if len(cluster) > 1:
                    groups.append(cluster)
        return groups

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
