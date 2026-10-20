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
