"""Pipeline orchestration with fake services (no network, no FFmpeg)."""

from pathlib import Path

import pytest

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.errors import AppError, Cancelled
from musicdlp.core.models import BatchItem
from musicdlp.services import pipeline as pipeline_module
from musicdlp.services.pipeline import Pipeline
from musicdlp.services.spotify import SyncResult


class FakeYtDlp:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.fail_ids: set[int] = set()

    def download(self, track, raw_dir, progress_cb=None, cancel_event=None):
        self.calls.append(track.id)
        if track.id in self.fail_ids:
            raise AppError("simulated download failure", kind="test")
        if progress_cb:
            progress_cb({"percent": 40, "speed": 1000, "eta": 2})
            progress_cb({"percent": 100, "speed": 0, "eta": 0})
        path = Path(raw_dir) / f"[{track.id}].webm"
        path.write_bytes(b"raw-audio")
        return path


class FakeFFmpeg:
    available = True

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_formats: set[str] = set()

    def convert(self, input_path, output_path, *, fmt, bitrate,
                normalize, cancel_event=None):
        if fmt in self.fail_formats:
            raise AppError("simulated conversion failure", kind="test")
        self.calls.append(fmt)
        output_path.write_bytes(b"fake-" + fmt.encode())
        return output_path


class FakeThumbnails:
    def fetch(self, *args, **kwargs):
        return None


class FakeSpotify:
    def __init__(self) -> None:
        self.files: list[Path] = []
        self.restarted = False

    def sync(self, files, destination, mode="copy"):
        result = SyncResult()
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        for source in files:
            target = destination / Path(source).name
            if target.exists():
                result.skipped += 1
            else:
                target.write_bytes(Path(source).read_bytes())
                result.copied += 1
                self.files.append(target)
        return result

    def restart(self):
        self.restarted = True
        return True, "ok"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pipeline_module, "embed_tags", lambda *args, **kwargs: True
    )
    settings = Settings(
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
        formats=["mp3"],
        normalize=False,
        keep_raw=True,
        spotify_auto_restart=False,
        first_run_done=True,
    )
    db = Database(tmp_path / "library.db")
    source_id = db.add_source(
        url="https://www.youtube.com/playlist?list=PLtest",
        kind="playlist",
        platform="youtube",
        external_id="PLtest",
        title="Test playlist",
        uploader="Channel",
        item_count=2,
        thumbnail_url=None,
    )
    track_ids = []
    for index, media_id in enumerate(("vid1", "vid2"), start=1):
        track_ids.append(
            db.upsert_track(
                platform="youtube",
                media_id=media_id,
                url=f"https://www.youtube.com/watch?v={media_id}",
                title=f"Song {index}",
                uploader="Channel",
                duration=100,
            )
        )
    db.set_source_tracks(
        source_id, [(track_ids[0], 1), (track_ids[1], 2)]
    )
    source = db.get_source(source_id)
    rows = db.get_tracks_for_source(source_id)
    items = [
        BatchItem(source=source, track=row.track, position=row.position)
        for row in rows
    ]

    ytdlp = FakeYtDlp()
    ffmpeg = FakeFFmpeg()
    spotify = FakeSpotify()
    pipeline = Pipeline(
        db, settings, ytdlp, ffmpeg, FakeThumbnails(), spotify
    )
    yield {
        "db": db,
        "settings": settings,
        "source": source,
        "items": items,
        "track_ids": track_ids,
        "ytdlp": ytdlp,
        "ffmpeg": ffmpeg,
        "spotify": spotify,
        "pipeline": pipeline,
        "tmp": tmp_path,
    }
    db.close()


def _summary():
    return {
        "downloaded": 0,
        "converted": 0,
        "synced": 0,
        "failed": 0,
        "skipped": 0,
        "errors": [],
        "cancelled": False,
        "stages": [],
    }


def test_full_pipeline(env):
    summary = _summary()
    env["pipeline"]._execute(
        env["items"], ["download", "convert", "sync"], False,
        env["settings"], summary,
    )

    assert summary["downloaded"] == 2
    assert summary["converted"] == 2
    assert summary["synced"] == 2
    assert summary["failed"] == 0
    assert env["ytdlp"].calls == env["track_ids"]

    for track_id in env["track_ids"]:
        track = env["db"].get_track(track_id)
        assert track.status == "synced"
        assert Path(track.mp3_path).exists()
        assert track.error is None

    assert len(list((env["tmp"] / "spotify").glob("*.mp3"))) == 2
    assert env["spotify"].restarted is False


def test_second_run_does_not_reconvert(env):
    env["pipeline"]._execute(
        env["items"], ["download", "convert", "sync"], False,
        env["settings"], _summary(),
    )
    conversions = len(env["ffmpeg"].calls)

    summary = _summary()
    env["pipeline"]._execute(
        env["items"], ["download", "convert", "sync"], False,
        env["settings"], summary,
    )
    assert len(env["ffmpeg"].calls) == conversions
    assert summary["synced"] == 2


def test_download_failure_does_not_stop_the_batch(env):
    env["ytdlp"].fail_ids = {env["track_ids"][0]}
    summary = _summary()
    env["pipeline"]._execute(
        env["items"], ["download", "convert", "sync"], False,
        env["settings"], summary,
    )

    assert summary["failed"] == 1
    first = env["db"].get_track(env["track_ids"][0])
    second = env["db"].get_track(env["track_ids"][1])
    assert first.status == "error"
    assert "simulated download failure" in first.error
    assert second.status == "synced"


def test_conversion_failure_is_recorded(env):
    env["ffmpeg"].fail_formats = {"mp3"}
    summary = _summary()
    env["pipeline"]._execute(
        env["items"], ["download", "convert"], False,
        env["settings"], summary,
    )
    assert summary["failed"] == 2
    for track_id in env["track_ids"]:
        track = env["db"].get_track(track_id)
        assert track.status == "error"
        assert "simulated conversion failure" in track.error


def test_sync_failure_keeps_converted_status(env):
    class FailingSpotify(FakeSpotify):
        def sync(self, files, destination, mode="copy"):
            result = SyncResult()
            result.failed = len(list(files))
            result.errors = ["disk full"]
            return result

    env["pipeline"].spotify = FailingSpotify()
    summary = _summary()
    env["pipeline"]._execute(
        env["items"], ["download", "convert", "sync"], False,
        env["settings"], summary,
    )
    assert summary["failed"] == 2
    for track_id in env["track_ids"]:
        track = env["db"].get_track(track_id)
        assert track.status == "converted"
        assert track.sync_error


def test_cancellation(env):
    env["pipeline"]._cancel.set()
    with pytest.raises(Cancelled):
        env["pipeline"]._execute(
            env["items"], ["download"], False,
            env["settings"], _summary(),
        )


def test_an_update_run_only_refreshes_songs_already_in_spotify(env):
    """Updating files must not add new songs to the Spotify folder."""
    db = env["db"]
    pipeline = env["pipeline"]
    pipeline._execute(
        env["items"], ["download", "convert"], False,
        env["settings"], _summary(),
    )
    db.update_track(
        env["track_ids"][0], status="synced", synced_at="2024-01-01 00:00:00"
    )

    assert pipeline.start(env["items"], ["sync"], only_synced=True)
    pipeline.wait(20)

    copies = list((env["tmp"] / "spotify").glob("*.mp3"))
    assert len(copies) == 1


# ------------------------------------- the picture the user chose is final

class FakeThumbnailsWithStill:
    """Serves the video's own picture, as the real service would."""

    def __init__(self, still: Path) -> None:
        self.still = still
        self.calls = 0

    def fetch(self, *args, **kwargs):
        self.calls += 1
        return self.still


def _covers(monkeypatch):
    """Capture the picture handed to the tag writer."""
    used: list = []
    monkeypatch.setattr(
        pipeline_module,
        "embed_tags",
        lambda path, **kwargs: used.append(kwargs.get("cover_path")) or True,
    )
    return used


def test_use_video_thumbnail_replaces_the_cover_the_user_rejected(
    env, monkeypatch
):
    """The reported bug: Save kept putting the bad artwork back.

    The automatic lookup had cached a wrong cover. Choosing "Use video
    thumbnail" recorded no choice, so the convert stage simply looked the
    same artwork up again and re-embedded it.
    """
    db, tmp = env["db"], env["tmp"]
    track_id = env["track_ids"][0]

    bad = tmp / "album_youtube_vid1_1400_v3.jpg"
    bad.write_bytes(b"the cover the user rejected")
    still = tmp / "youtube_vid1_v2.jpg"
    still.write_bytes(b"the video's own picture")
    raw = tmp / "raw.webm"
    raw.write_bytes(b"raw")
    song = tmp / "out" / "MP3" / "Song 1.mp3"
    song.parent.mkdir(parents=True, exist_ok=True)
    song.write_bytes(b"old audio")

    db.update_track(
        track_id, cover_path=str(bad), raw_path=str(raw), mp3_path=str(song),
        status="converted", meta_title="Song 1", meta_artist="Channel",
    )

    # What Save does when "Use video thumbnail" was pressed.
    from musicdlp.services.library import LibraryService

    LibraryService(db, env["settings"], None).restore_video_cover(track_id)
    assert db.get_track(track_id).cover_source == "video"

    looked_up: list = []
    monkeypatch.setattr(
        pipeline_module, "find_cover",
        lambda *a, **k: looked_up.append(a) or bad,
    )
    used = _covers(monkeypatch)
    env["pipeline"].thumbnails = FakeThumbnailsWithStill(still)

    summary = _summary()
    env["pipeline"]._execute(
        env["items"][:1], ["convert"], True, env["settings"], summary
    )

    assert summary["failed"] == 0
    assert looked_up == [], "the rejected artwork was looked up again"
    assert used == [still], "the file kept the cover the user rejected"
    assert db.get_track(track_id).cover_path == str(still)


def test_the_catalogue_lookup_still_runs_when_nothing_was_chosen(
    env, monkeypatch
):
    """The fix must not stop the automatic artwork from working."""
    db, tmp = env["db"], env["tmp"]
    track_id = env["track_ids"][0]

    found = tmp / "album_youtube_vid1_1400_v3.jpg"
    found.write_bytes(b"a real cover")
    raw = tmp / "raw.webm"
    raw.write_bytes(b"raw")
    song = tmp / "out" / "MP3" / "Song 1.mp3"
    song.parent.mkdir(parents=True, exist_ok=True)
    song.write_bytes(b"old audio")

    db.update_track(
        track_id, cover_path=None, raw_path=str(raw), mp3_path=str(song),
        status="converted", meta_title="Song 1", meta_artist="Channel",
    )

    monkeypatch.setattr(pipeline_module, "find_cover", lambda *a, **k: found)
    used = _covers(monkeypatch)

    summary = _summary()
    env["pipeline"]._execute(
        env["items"][:1], ["convert"], True, env["settings"], summary
    )

    assert used == [found]


def test_a_chosen_picture_survives_a_fresh_download(env, monkeypatch):
    """Re-downloading must not swap a chosen picture for the video still."""
    db, tmp = env["db"], env["tmp"]
    track_id = env["track_ids"][0]

    chosen = tmp / "custom_youtube_vid1.jpg"
    chosen.write_bytes(b"the picture the user picked")
    still = tmp / "youtube_vid1_v2.jpg"
    still.write_bytes(b"the video still")
    song = tmp / "out" / "MP3" / "Song 1.mp3"
    song.parent.mkdir(parents=True, exist_ok=True)
    song.write_bytes(b"old audio")

    db.update_track(
        track_id, cover_path=str(chosen), cover_source="custom",
        raw_path=None, mp3_path=str(song), status="converted",
        meta_title="Song 1", meta_artist="Channel",
    )

    looked_up: list = []
    monkeypatch.setattr(
        pipeline_module, "find_cover",
        lambda *a, **k: looked_up.append(a) or still,
    )
    used = _covers(monkeypatch)
    env["pipeline"].thumbnails = FakeThumbnailsWithStill(still)

    summary = _summary()
    env["pipeline"]._execute(
        env["items"][:1], ["download", "convert"], True,
        env["settings"], summary,
    )

    assert db.get_track(track_id).cover_path == str(chosen)
    assert looked_up == []
    assert used == [chosen]
