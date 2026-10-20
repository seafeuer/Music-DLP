"""Importing the previous app's database."""

import sqlite3
from pathlib import Path

from musicdlp.core.db import Database
from musicdlp.services.legacy_import import import_legacy


OLD_SCHEMA = """
CREATE TABLE playlists (
    playlist_id TEXT PRIMARY KEY, title TEXT, uploader TEXT, url TEXT,
    video_count INTEGER, added_date TEXT, last_updated TEXT
);
CREATE TABLE videos (
    video_id TEXT PRIMARY KEY, title TEXT, uploader TEXT, duration INTEGER,
    url TEXT, description TEXT, thumbnail_url TEXT, download_status TEXT,
    download_date TEXT, download_error TEXT, file_path_raw TEXT,
    extraction_date TEXT, source_index INTEGER
);
CREATE TABLE playlist_videos (
    playlist_id TEXT, video_id TEXT, position INTEGER, added_date TEXT,
    PRIMARY KEY (playlist_id, video_id)
);
CREATE TABLE approved_metadata (
    video_id TEXT PRIMARY KEY, final_title TEXT, final_artist TEXT,
    cover_artist TEXT, album TEXT, tags TEXT, approval_date TEXT,
    conversion_status TEXT, conversion_error TEXT, file_path_mp3 TEXT,
    file_path_m4a TEXT, thumbnail_path TEXT
);
"""


def _make_old_db(tmp_path: Path) -> Path:
    old = tmp_path / "music_dlp.db"
    conn = sqlite3.connect(old)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO playlists VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("PLold", "Old playlist", "Old channel",
         "https://www.youtube.com/playlist?list=PLold", 2, None, None),
    )
    conn.execute(
        "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("vid1", "Old title", "Old channel", 100,
         "https://www.youtube.com/watch?v=vid1", "", "", "downloaded",
         "2025-01-01", None, "downloads/raw/vid1.webm", None, 1),
    )
    conn.execute(
        "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("vid2", "Second", "Old channel", 120,
         "https://www.youtube.com/watch?v=vid2", "", "", "pending",
         None, None, None, None, 2),
    )
    conn.execute(
        "INSERT INTO videos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("orphan", "Orphan", "Other", 90,
         "https://www.youtube.com/watch?v=orphan", "", "", "downloaded",
         "2025-01-02", None, "downloads/raw/orphan.webm", None, None),
    )
    conn.execute(
        "INSERT INTO playlist_videos VALUES (?, ?, ?, ?)",
        ("PLold", "vid1", 1, None),
    )
    conn.execute(
        "INSERT INTO playlist_videos VALUES (?, ?, ?, ?)",
        ("PLold", "vid2", 2, None),
    )
    conn.execute(
        "INSERT INTO approved_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("vid1", "Final title", "Final artist", None, "Album", "[]",
         "2025-01-03", "completed", None, "downloads/final/mp3/vid1.mp3",
         None, None),
    )
    conn.commit()
    conn.close()

    raw = tmp_path / "downloads" / "raw"
    raw.mkdir(parents=True)
    (raw / "vid1.webm").write_bytes(b"raw")
    (raw / "orphan.webm").write_bytes(b"raw")
    mp3 = tmp_path / "downloads" / "final" / "mp3"
    mp3.mkdir(parents=True)
    (mp3 / "vid1.mp3").write_bytes(b"mp3")
    return old


def test_import_maps_sources_tracks_and_outputs(tmp_path):
    old = _make_old_db(tmp_path)
    db = Database(tmp_path / "new.db")

    summary = import_legacy(db, old)
    assert summary.sources == 2  # the playlist + "Imported singles"
    assert summary.tracks == 3
    assert not summary.errors

    sources = db.get_sources()
    titles = {source.title for source in sources}
    assert titles == {"Old playlist", "Imported singles"}

    playlist = next(s for s in sources if s.title == "Old playlist")
    rows = db.get_tracks_for_source(playlist.id)
    assert [row.track.media_id for row in rows] == ["vid1", "vid2"]

    converted = db.get_track_by_media("youtube", "vid1")
    assert converted.status == "converted"
    assert converted.meta_title == "Final title"
    assert converted.meta_artist == "Final artist"
    assert converted.mp3_path.endswith("vid1.mp3")
    assert converted.raw_path.endswith("vid1.webm")

    pending = db.get_track_by_media("youtube", "vid2")
    assert pending.status == "new"

    singles = next(s for s in sources if s.title == "Imported singles")
    orphan_rows = db.get_tracks_for_source(singles.id)
    assert [row.track.media_id for row in orphan_rows] == ["orphan"]
    assert orphan_rows[0].track.status == "downloaded"
    db.close()


def test_import_is_idempotent(tmp_path):
    old = _make_old_db(tmp_path)
    db = Database(tmp_path / "new.db")

    import_legacy(db, old)
    import_legacy(db, old)

    assert len(db.get_sources()) == 2
    playlist = next(
        s for s in db.get_sources() if s.title == "Old playlist"
    )
    assert len(db.get_tracks_for_source(playlist.id)) == 2
    db.close()


def test_import_rejects_current_database(tmp_path):
    db = Database(tmp_path / "new.db")
    try:
        import_legacy(db, db.path)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    db.close()
