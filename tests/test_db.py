"""Database schema and repository behavior."""

import sqlite3

from musicdlp.core.db import Database


def _db(tmp_path) -> Database:
    return Database(tmp_path / "library.db")


def _source(db, url="https://www.youtube.com/playlist?list=PLx", title="List",
            external_id="PLx"):
    return db.add_source(
        url=url,
        kind="playlist",
        platform="youtube",
        external_id=external_id,
        title=title,
        uploader="Channel",
        item_count=0,
        thumbnail_url=None,
    )


def _track(db, media_id="vid1", title="Song"):
    return db.upsert_track(
        platform="youtube",
        media_id=media_id,
        url=f"https://www.youtube.com/watch?v={media_id}",
        title=title,
        uploader="Channel",
        duration=200,
    )


def test_source_upsert_is_idempotent(tmp_path):
    db = _db(tmp_path)
    first = _source(db, title="First")
    second = _source(db, title="Second")
    assert first == second
    sources = db.get_sources()
    assert len(sources) == 1
    assert sources[0].title == "Second"
    db.close()


def test_track_upsert_dedupes_and_updates_description(tmp_path):
    db = _db(tmp_path)
    first = _track(db, title="Old")
    second = _track(db, title="New")
    assert first == second
    track = db.get_track(first)
    assert track.title == "New"
    db.close()


def test_source_tracks_order_and_counts(tmp_path):
    db = _db(tmp_path)
    source_id = _source(db)
    a = _track(db, "a", "A")
    b = _track(db, "b", "B")
    db.set_source_tracks(source_id, [(b, 2), (a, 1)])

    rows = db.get_tracks_for_source(source_id)
    assert [row.track.media_id for row in rows] == ["a", "b"]
    assert [row.position for row in rows] == [1, 2]
    assert db.count_source_tracks(source_id) == 2
    assert db.get_source(source_id).item_count == 2
    db.close()


def test_refresh_replaces_links_but_keeps_tracks(tmp_path):
    db = _db(tmp_path)
    source_id = _source(db)
    a = _track(db, "a", "A")
    b = _track(db, "b", "B")
    db.set_source_tracks(source_id, [(a, 1), (b, 2)])

    db.set_source_tracks(source_id, [(b, 1)])
    assert db.count_source_tracks(source_id) == 1
    assert db.get_track(a) is not None
    db.close()


def test_same_track_can_belong_to_two_sources(tmp_path):
    db = _db(tmp_path)
    one = _source(db, url="https://www.youtube.com/playlist?list=PL1",
                  title="One", external_id="PL1")
    two = _source(db, url="https://www.youtube.com/playlist?list=PL2",
                  title="Two", external_id="PL2")
    track_id = _track(db, "shared", "Shared")
    db.set_source_tracks(one, [(track_id, 1)])
    db.set_source_tracks(two, [(track_id, 1)])

    assert db.count_track_links(track_id) == 2
    db.unlink_track_everywhere(track_id)
    assert db.count_track_links(track_id) == 0
    assert db.get_track(track_id) is not None
    assert db.get_source(one).item_count == 0
    assert db.get_source(two).item_count == 0
    db.close()


def test_delete_source_keeps_track_rows(tmp_path):
    db = _db(tmp_path)
    source_id = _source(db)
    track_id = _track(db)
    db.set_source_tracks(source_id, [(track_id, 1)])

    db.delete_source(source_id)
    assert db.get_source(source_id) is None
    assert db.get_track(track_id) is not None
    db.close()


def test_status_counts_and_sync_error(tmp_path):
    db = _db(tmp_path)
    source_id = _source(db)
    a = _track(db, "a", "A")
    b = _track(db, "b", "B")
    db.set_source_tracks(source_id, [(a, 1), (b, 2)])

    db.update_track(a, status="converted", sync_error="copy failed")
    counts = db.status_counts(source_id)
    assert counts["new"] == 1
    assert counts["converted"] == 1
    assert db.get_track(a).sync_error == "copy failed"
    db.close()


def test_foreign_keys_cascade_source_links(tmp_path):
    db = _db(tmp_path)
    source_id = _source(db)
    track_id = _track(db)
    db.set_source_tracks(source_id, [(track_id, 1)])
    db.delete_source(source_id)
    assert db.count_track_links(track_id) == 0
    db.close()


def test_database_file_is_created_with_wal(tmp_path):
    db = _db(tmp_path)
    assert db.path.exists()
    mode = db._conn().execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    db.close()


def test_closed_database_refuses_work(tmp_path):
    db = _db(tmp_path)
    db.close()
    try:
        db.get_sources()
    except RuntimeError:
        pass
    else:
        raise AssertionError("closed database should refuse work")

def test_an_old_library_gains_the_cover_source_column(tmp_path):
    """A library made before covers recorded their source still opens."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL, media_id TEXT NOT NULL, url TEXT NOT NULL,
            source_index INTEGER, title TEXT NOT NULL, uploader TEXT,
            duration INTEGER, thumbnail_url TEXT, description TEXT,
            status TEXT NOT NULL DEFAULT 'new', error TEXT,
            meta_title TEXT, meta_artist TEXT, meta_album TEXT,
            meta_track INTEGER, raw_path TEXT, mp3_path TEXT, m4a_path TEXT,
            cover_path TEXT, downloaded_at TEXT, converted_at TEXT,
            synced_at TEXT, sync_error TEXT,
            needs_update INTEGER NOT NULL DEFAULT 0,
            smart_title TEXT, smart_artist TEXT, smart_confidence REAL,
            smart_reason TEXT,
            UNIQUE(platform, media_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO tracks (platform, media_id, url, title) "
        "VALUES ('youtube', 'abc', 'u', 'Old song')"
    )
    conn.commit()
    conn.close()

    db = Database(path)
    track = db.get_all_tracks()[0]
    assert track.cover_source is None
    db.update_track(track.id, cover_source="video")
    assert db.get_track(track.id).cover_source == "video"
    db.close()
