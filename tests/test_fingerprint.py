"""A light per-file fingerprint that catches shared and replaced files."""

from musicdlp.core.db import Database
from musicdlp.services import fingerprint


def _db(tmp_path):
    return Database(tmp_path / "library.db")


def _add_track(db, media_id, title, path):
    track_id = db.upsert_track(
        platform="youtube",
        media_id=media_id,
        url=f"https://youtu.be/{media_id}",
        title=title,
        uploader="Channel",
        duration=200,
    )
    db.update_track(track_id, status="converted", mp3_path=str(path))
    return track_id


def _audio(path, payload=b"A" * 400_000):
    path.write_bytes(payload)
    return path


def test_two_identical_files_have_one_fingerprint(tmp_path):
    first = _audio(tmp_path / "one.mp3")
    second = _audio(tmp_path / "two.mp3")

    left = fingerprint.fingerprint_file(first)
    right = fingerprint.fingerprint_file(second)

    assert left is not None and right is not None
    assert left.signature() == right.signature()


def test_a_file_that_changed_has_a_different_fingerprint(tmp_path):
    path = _audio(tmp_path / "song.mp3")
    before = fingerprint.fingerprint_file(path)

    path.write_bytes(b"B" * 400_000)
    after = fingerprint.fingerprint_file(path)

    assert before is not None and after is not None
    assert before.signature() != after.signature()


def test_a_missing_file_has_no_fingerprint(tmp_path):
    assert fingerprint.fingerprint_file(tmp_path / "gone.mp3") is None
    assert fingerprint.fingerprint_file(tmp_path / "empty.mp3") is None


def test_two_songs_sharing_one_file_are_reported(tmp_path):
    db = _db(tmp_path)
    try:
        content = b"C" * 300_000
        first_path = _audio(tmp_path / "first.mp3", content)
        second_path = _audio(tmp_path / "copy.mp3", content)
        first = _add_track(db, "one", "First", first_path)
        second = _add_track(db, "two", "Second", second_path)
        fingerprint.record_fingerprints(db, first)
        fingerprint.record_fingerprints(db, second)

        report = fingerprint.check_library(db)
    finally:
        db.close()

    assert report["checked"] == 2
    assert len(report["shared"]) == 1
    assert {track["id"] for track in report["shared"][0]["tracks"]} == {
        first, second
    }
    assert report["changed"] == []


def test_a_file_replaced_by_another_song_is_reported(tmp_path):
    db = _db(tmp_path)
    try:
        path = _audio(tmp_path / "song.mp3")
        track_id = _add_track(db, "one", "First", path)
        fingerprint.record_fingerprints(db, track_id)

        path.write_bytes(b"D" * 300_000)

        report = fingerprint.check_library(db)
    finally:
        db.close()

    assert report["shared"] == []
    assert [item["track_id"] for item in report["changed"]] == [track_id]
    assert report["changed"][0]["title"] == "First"


def test_a_note_that_was_re_recorded_after_our_own_write_is_clean(tmp_path):
    """Music DLP refreshes the record after it writes, so its own edit is
    not reported as an outside change."""
    db = _db(tmp_path)
    try:
        path = _audio(tmp_path / "song.mp3")
        track_id = _add_track(db, "one", "First", path)
        fingerprint.record_fingerprints(db, track_id)

        # Our own rewrite (retag, cover) changes the bytes, then we record.
        path.write_bytes(b"E" * 300_000)
        fingerprint.record_fingerprints(db, track_id)

        report = fingerprint.check_library(db)
    finally:
        db.close()

    assert report["changed"] == []
    assert report["shared"] == []


def test_a_deleted_file_is_left_to_the_missing_status(tmp_path):
    db = _db(tmp_path)
    try:
        path = _audio(tmp_path / "song.mp3")
        track_id = _add_track(db, "one", "First", path)
        fingerprint.record_fingerprints(db, track_id)
        path.unlink()

        report = fingerprint.check_library(db)
    finally:
        db.close()

    assert report == {"checked": 0, "shared": [], "changed": []}