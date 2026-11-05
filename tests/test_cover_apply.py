"""Applying a song's own upload artwork as its cover."""

from pathlib import Path

import pytest

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.services import library as library_module
from musicdlp.services.library import LibraryService


class FakeThumbnails:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def fetch(self, platform, media_id, url, fallbacks=None, *, refresh=False):
        self.calls.append(
            {
                "platform": platform,
                "media_id": media_id,
                "url": url,
                "fallbacks": fallbacks,
                "refresh": refresh,
            }
        )
        return self.result


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = Settings(
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
    )
    db = Database(tmp_path / "library.db")
    track_id = db.upsert_track(
        platform="soundcloud",
        media_id="921863392",
        url="https://soundcloud.com/x/y",
        title="Clap (God Above)",
        uploader="Travis Scott",
        thumbnail_url="https://i1.sndcdn.com/artworks-abc-original.jpg",
    )

    out_file = tmp_path / "out" / "Clap (God Above).mp3"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(b"audio")
    spotify_copy = tmp_path / "spotify" / out_file.name
    spotify_copy.parent.mkdir(parents=True, exist_ok=True)
    spotify_copy.write_bytes(b"audio")

    db.update_track(
        track_id,
        mp3_path=str(out_file),
        status="synced",
        needs_update=1,
    )

    replaced = []

    def fake_replace(path, cover):
        replaced.append((Path(path), Path(cover)))
        return True

    monkeypatch.setattr(library_module, "replace_cover", fake_replace)

    library = LibraryService(db, settings, None)
    yield library, db, track_id, FakeThumbnails(tmp_path / "art.jpg"), replaced
    db.close()


def test_the_upload_picture_replaces_the_cover_in_every_copy(env):
    library, db, track_id, thumbnails, replaced = env

    assert library.apply_video_cover(track_id, thumbnails) == "updated"

    written = {path.name for path, _cover in replaced}
    assert written == {"Clap (God Above).mp3"}
    # Both the working file and the Spotify copy were rewritten.
    assert len(replaced) == 2

    track = db.get_track(track_id)
    assert track.cover_source == "video"
    # Changing the picture says nothing about the song's details. Clearing
    # this flag would hide a pending rename/retag from the next run.
    assert track.needs_update == 1
    assert track.cover_path.endswith("art.jpg")


def test_it_asks_for_the_uploads_own_picture(env):
    library, _db, track_id, thumbnails, _replaced = env

    library.apply_video_cover(track_id, thumbnails)

    call = thumbnails.calls[0]
    assert call["platform"] == "soundcloud"
    assert call["url"] == "https://i1.sndcdn.com/artworks-abc-original.jpg"
    # A stale cached picture must not be reused.
    assert call["refresh"] is True
    # YouTube has size-specific fallbacks; other platforms do not.
    assert call["fallbacks"] is None


def test_a_song_with_no_file_is_skipped(env):
    library, db, track_id, thumbnails, _replaced = env
    db.update_track(track_id, mp3_path="")

    assert library.apply_video_cover(track_id, thumbnails) == "skipped"
    assert thumbnails.calls == []


def test_no_picture_available_changes_nothing(env):
    library, db, track_id, _thumbnails, replaced = env

    assert library.apply_video_cover(track_id, FakeThumbnails(None)) == "unchanged"
    assert replaced == []
    assert db.get_track(track_id).needs_update == 1


def test_an_unknown_song_is_an_error(env):
    library, _db, _track_id, thumbnails, _replaced = env

    assert library.apply_video_cover(9999, thumbnails) == "error"


def test_without_a_thumbnail_service_nothing_happens(env):
    library, _db, track_id, _thumbnails, replaced = env

    assert library.apply_video_cover(track_id, None) == "error"
    assert replaced == []


def test_choosing_the_video_keeps_a_still_that_is_already_on_disk(tmp_path):
    """The site may stop serving the picture; the copy on disk still works."""
    settings = Settings(
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
    )
    db = Database(tmp_path / "library.db")
    still = tmp_path / "youtube_abc_v2.jpg"
    still.write_bytes(b"the video still")
    track_id = db.upsert_track(
        platform="youtube", media_id="abc",
        url="https://www.youtube.com/watch?v=abc", title="A song",
        uploader="Channel", duration=200,
    )
    db.update_track(
        track_id, status="converted", cover_path=str(still),
        mp3_path=str(tmp_path / "song.mp3"),
    )
    library = LibraryService(db, settings, None)

    library.restore_video_cover(track_id)

    track = db.get_track(track_id)
    assert track.cover_source == "video"
    assert track.cover_path == str(still)
    db.close()


def test_choosing_the_video_drops_a_catalogue_picture(tmp_path):
    settings = Settings(
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
    )
    db = Database(tmp_path / "library.db")
    catalogue = tmp_path / "album_youtube_abc_1400_v3.jpg"
    catalogue.write_bytes(b"the wrong cover")
    track_id = db.upsert_track(
        platform="youtube", media_id="abc",
        url="https://www.youtube.com/watch?v=abc", title="A song",
        uploader="Channel", duration=200,
    )
    db.update_track(
        track_id, status="converted", cover_path=str(catalogue),
        mp3_path=str(tmp_path / "song.mp3"),
    )
    library = LibraryService(db, settings, None)

    library.restore_video_cover(track_id)

    track = db.get_track(track_id)
    assert track.cover_source == "video"
    assert track.cover_path is None
    db.close()