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
                normalize, cancel_event=None, progress_cb=None):
        if fmt in self.fail_formats:
            raise AppError("simulated conversion failure", kind="test")
        if progress_cb is not None:
            progress_cb(50)
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
                result.done.append(target.name)
            else:
                target.write_bytes(Path(source).read_bytes())
                result.copied += 1
                result.done.append(target.name)
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
    # The cover lookup is a live catalogue request. Every test that wants
    # a found cover patches this itself; the default keeps the rest of the
    # suite offline and fast.
    monkeypatch.setattr(
        pipeline_module, "find_cover", lambda *args, **kwargs: None
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


def test_an_update_run_does_not_download_a_song_it_already_has(env):
    """Update files forces a rewrite, not a second download.

    When one song in the batch needed a fresh download, the force flag
    used to make the download stage fetch every other song again too.
    """
    kept = env["tmp"] / "kept-vid1.webm"
    kept.write_bytes(b"already downloaded")
    env["db"].update_track(
        env["track_ids"][0], raw_path=str(kept), status="downloaded"
    )

    summary = _summary()
    env["pipeline"]._execute(
        env["items"], ["download", "convert"], True,
        env["settings"], summary,
    )

    # Only the song without a download was fetched.
    assert env["ytdlp"].calls == [env["track_ids"][1]]
    assert summary["skipped"] == 1


def test_conversion_keeps_the_cover_it_already_had(env, monkeypatch):
    """A lookup with no answer must not strip the existing picture."""
    used = _covers(monkeypatch)
    monkeypatch.setattr(
        pipeline_module, "find_cover", lambda *args, **kwargs: None
    )
    env["pipeline"].thumbnails = FakeThumbnails()  # no video still either

    track_id = env["track_ids"][0]
    stored = env["tmp"] / "album_youtube_vid1_640.jpg"
    stored.write_bytes(b"the old cover")
    raw = env["tmp"] / "raw-vid1.webm"
    raw.write_bytes(b"raw")
    env["db"].update_track(
        track_id, raw_path=str(raw), cover_path=str(stored),
        status="downloaded",
    )

    env["pipeline"]._execute(
        [env["items"][0]], ["convert"], True, env["settings"], _summary()
    )

    assert used[-1] == stored


def test_a_failed_tag_write_is_reported(env, monkeypatch):
    """A file whose details could not be written is not a success."""
    monkeypatch.setattr(
        pipeline_module, "embed_tags", lambda *args, **kwargs: False
    )
    # The cover lookup is a live network call; this test is about the tag
    # failure, and a slow catalogue must not make the suite hang.
    monkeypatch.setattr(
        pipeline_module, "find_cover", lambda *args, **kwargs: None
    )
    summary = _summary()
    env["pipeline"]._execute(
        env["items"], ["download", "convert"], False,
        env["settings"], summary,
    )

    assert summary["failed"] == 2
    for track_id in env["track_ids"]:
        assert env["db"].get_track(track_id).status == "error"


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


# ------------------------------------------------- retag instead of re-encode


@pytest.fixture()
def no_lookups(monkeypatch):
    """A retag must never reach the network for a song that has a cover."""
    monkeypatch.setattr(pipeline_module, "find_cover", lambda *a, **k: None)


def test_editing_details_rewrites_tags_without_re_encoding(
    env, monkeypatch, no_lookups
):
    """The audio must survive a metadata edit bit-for-bit."""
    calls = []

    def fake_embed(path, **kwargs):
        calls.append((Path(path), kwargs))
        return True

    monkeypatch.setattr(pipeline_module, "embed_tags", fake_embed)

    out = Path(env["settings"].output_dir) / "MP3"
    out.mkdir(parents=True, exist_ok=True)
    song = out / "Song 1.mp3"
    song.write_bytes(b"audio-bytes")
    track_id = env["track_ids"][0]
    env["db"].update_track(
        track_id, status="converted", mp3_path=str(song), raw_path=None,
    )
    env["db"].update_track(track_id, meta_title="Renamed", meta_artist="Artist",
                           needs_update=1)
    item = BatchItem(
        source=env["source"], track=env["db"].get_track(track_id), position=1
    )

    env["pipeline"].start([item], ["convert"], force=True, retag=True)
    env["pipeline"].wait(10)

    renamed = out / "Renamed.mp3"
    assert renamed.read_bytes() == b"audio-bytes"
    assert not song.exists()
    assert env["ffmpeg"].calls == []          # nothing was re-encoded
    assert env["ytdlp"].calls == []           # nothing was downloaded
    track = env["db"].get_track(track_id)
    assert track.mp3_path == str(renamed)
    assert track.status == "converted"
    assert track.needs_update == 0
    assert calls and calls[-1][1]["title"] == "Renamed"


def test_retag_keeps_the_chosen_cover(env, monkeypatch, no_lookups):
    seen = {}

    def fake_embed(path, **kwargs):
        seen.update(kwargs)
        return True

    monkeypatch.setattr(pipeline_module, "embed_tags", fake_embed)

    out = Path(env["settings"].output_dir) / "MP3"
    out.mkdir(parents=True, exist_ok=True)
    song = out / "Song 1.mp3"
    song.write_bytes(b"audio")
    cover = env["tmp"] / "cover.jpg"
    cover.write_bytes(b"picture")
    track_id = env["track_ids"][0]
    env["db"].update_track(
        track_id, status="converted", mp3_path=str(song),
        cover_path=str(cover), cover_source="custom",
    )
    item = BatchItem(
        source=env["source"], track=env["db"].get_track(track_id), position=1
    )

    env["pipeline"].start([item], ["convert"], force=True, retag=True)
    env["pipeline"].wait(10)

    assert Path(seen["cover_path"]) == cover


def test_retag_sends_the_song_to_spotify_again(env, monkeypatch, no_lookups):
    monkeypatch.setattr(
        pipeline_module, "embed_tags", lambda *args, **kwargs: True
    )
    out = Path(env["settings"].output_dir) / "MP3"
    out.mkdir(parents=True, exist_ok=True)
    song = out / "Old.mp3"
    song.write_bytes(b"audio")
    track_id = env["track_ids"][0]
    env["db"].update_track(
        track_id, status="synced", mp3_path=str(song),
        synced_at="2026-01-01T00:00:00", meta_title="New",
    )

    # A stale copy of the old name sits in the Spotify folder.
    spotify_dir = Path(env["settings"].spotify_dir)
    spotify_dir.mkdir(parents=True, exist_ok=True)
    (spotify_dir / "Old.mp3").write_bytes(b"old")

    item = BatchItem(
        source=env["source"], track=env["db"].get_track(track_id), position=1
    )
    env["pipeline"].start(
        [item], ["convert", "sync"], force=True, retag=True
    )
    env["pipeline"].wait(10)

    track = env["db"].get_track(track_id)
    assert Path(track.mp3_path).name == "New.mp3", (
        track.status, track.error, track.sync_error, sorted(x.name for x in out.iterdir())
    )
    assert (spotify_dir / "New.mp3").exists()
    assert not (spotify_dir / "Old.mp3").exists()
    assert track.status == "synced"
    assert track.needs_update is False


def test_a_song_with_no_file_still_converts_normally(
    env, monkeypatch, no_lookups
):
    """Retag is only possible when there is something to retag."""
    monkeypatch.setattr(
        pipeline_module, "embed_tags", lambda *args, **kwargs: True
    )
    track_id = env["track_ids"][0]
    env["db"].update_track(track_id, needs_update=1)
    item = BatchItem(
        source=env["source"], track=env["db"].get_track(track_id), position=1
    )

    env["pipeline"].start(
        [item], ["download", "convert"], force=True, retag=True
    )
    env["pipeline"].wait(10)

    assert env["ytdlp"].calls == [track_id]
    assert env["ffmpeg"].calls == ["mp3"]
    assert env["db"].get_track(track_id).status == "converted"


def test_a_retag_does_not_look_the_cover_up_again(env, monkeypatch):
    """An edit that leaves the artwork alone must not wait on lookups."""
    calls = []
    monkeypatch.setattr(
        pipeline_module, "find_cover",
        lambda *args, **kwargs: calls.append(args) or None,
    )
    monkeypatch.setattr(
        pipeline_module, "embed_tags", lambda *args, **kwargs: True
    )
    out = Path(env["settings"].output_dir) / "MP3"
    out.mkdir(parents=True, exist_ok=True)
    song = out / "Song 1.mp3"
    song.write_bytes(b"audio")
    cover = env["tmp"] / "cover.jpg"
    cover.write_bytes(b"picture")
    track_id = env["track_ids"][0]
    env["db"].update_track(
        track_id, status="converted", mp3_path=str(song),
        cover_path=str(cover),
    )
    item = BatchItem(
        source=env["source"], track=env["db"].get_track(track_id), position=1
    )

    env["pipeline"].start([item], ["convert"], force=True, retag=True)
    env["pipeline"].wait(10)

    assert calls == []


def test_a_retag_rebuilds_a_format_whose_file_is_missing(env, monkeypatch):
    """A configured format that was deleted is encoded again, from the raw
    download — the formats that exist keep their audio untouched."""
    monkeypatch.setattr(
        pipeline_module, "embed_tags", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(pipeline_module, "find_cover", lambda *a, **k: None)
    env["settings"].formats = ["mp3", "m4a"]
    raw = env["tmp"] / "raw.webm"
    raw.write_bytes(b"raw")
    out = Path(env["settings"].output_dir) / "MP3"
    out.mkdir(parents=True, exist_ok=True)
    song = out / "Song 1.mp3"
    song.write_bytes(b"audio")
    track_id = env["track_ids"][0]
    env["db"].update_track(
        track_id, status="converted", mp3_path=str(song), raw_path=str(raw),
    )

    item = BatchItem(
        source=env["source"], track=env["db"].get_track(track_id), position=1
    )
    env["pipeline"].start([item], ["convert"], force=True, retag=True)
    env["pipeline"].wait(20)

    track = env["db"].get_track(track_id)
    assert env["ffmpeg"].calls == ["m4a"]      # only the missing format
    assert Path(track.m4a_path).exists()
    assert Path(track.mp3_path).read_bytes() == b"audio"


def test_a_failed_copy_is_not_reported_as_synced(env):
    """A stale file with the target's name is not proof the copy worked."""
    out = Path(env["settings"].output_dir) / "MP3"
    out.mkdir(parents=True, exist_ok=True)
    song = out / "Song 1.mp3"
    song.write_bytes(b"new-audio")
    spotify_dir = Path(env["settings"].spotify_dir)
    spotify_dir.mkdir(parents=True, exist_ok=True)
    # A different, unplayable copy already has the name the sync wants.
    (spotify_dir / "Song 1.mp3").write_bytes(b"stale")
    track_id = env["track_ids"][0]
    env["db"].update_track(
        track_id, status="converted", mp3_path=str(song)
    )

    class BrokenCopy(FakeSpotify):
        def sync(self, files, destination, mode="copy"):
            result = SyncResult()
            result.failed = len(list(files))
            result.errors = ["permission denied"]
            return result

    env["pipeline"].spotify = BrokenCopy()
    item = BatchItem(
        source=env["source"], track=env["db"].get_track(track_id), position=1
    )
    env["pipeline"].start([item], ["sync"])
    env["pipeline"].wait(10)

    track = env["db"].get_track(track_id)
    assert track.status == "converted"
    assert track.sync_error


def test_a_missing_format_that_cannot_be_rebuilt_stops_claiming_to_exist(
    env, monkeypatch
):
    """With no raw download left, the dead path must be cleared."""
    monkeypatch.setattr(
        pipeline_module, "embed_tags", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(pipeline_module, "find_cover", lambda *a, **k: None)
    env["settings"].formats = ["mp3", "m4a"]
    out = Path(env["settings"].output_dir) / "MP3"
    out.mkdir(parents=True, exist_ok=True)
    song = out / "Song 1.mp3"
    song.write_bytes(b"audio")
    track_id = env["track_ids"][0]
    env["db"].update_track(
        track_id, status="converted", mp3_path=str(song),
        m4a_path=str(out / "gone.m4a"), raw_path=None,
    )
    item = BatchItem(
        source=env["source"], track=env["db"].get_track(track_id), position=1
    )

    env["pipeline"].start([item], ["convert"], force=True, retag=True)
    env["pipeline"].wait(10)

    assert env["db"].get_track(track_id).m4a_path is None


def test_unusable_output_folder_records_a_failure_per_song(env, tmp_path):
    """A File-not-a-folder used to abort the job and strand rows."""
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file", encoding="utf-8")
    env["settings"].output_dir = str(blocker)

    summary = _summary()
    env["pipeline"]._execute(
        env["items"], ["download"], False, env["settings"], summary
    )

    assert summary["failed"] == 2
    assert summary["cancelled"] is False
    for track_id in env["track_ids"]:
        track = env["db"].get_track(track_id)
        assert track.status == "error"
        assert "download folder" in (track.error or "").lower()


def test_unusable_convert_folder_leaves_no_song_converting(env, tmp_path):
    summary = _summary()
    env["pipeline"]._execute(
        env["items"], ["download"], False, env["settings"], summary
    )

    blocker = tmp_path / "also-not-a-folder"
    blocker.write_text("a file", encoding="utf-8")
    env["settings"].output_dir = str(blocker)

    convert = _summary()
    env["pipeline"]._execute(
        env["items"], ["convert"], False, env["settings"], convert
    )

    assert convert["failed"] == 2
    for track_id in env["track_ids"]:
        track = env["db"].get_track(track_id)
        assert track.status == "error"
        assert track.status != "converting"
        assert "folder cannot be created" in (track.error or "").lower()


def test_bad_filename_template_does_not_kill_the_run(env):
    env["settings"].filename_template = "{title.foo}"
    summary = _summary()

    env["pipeline"]._execute(
        env["items"], ["download", "convert"], False,
        env["settings"], summary,
    )

    assert summary["converted"] == 2
    assert not summary.get("fatal")


def test_a_fatal_error_marks_the_stuck_song_as_failed(env):
    summary = _summary()
    track_id = env["track_ids"][0]
    env["db"].update_track(track_id, status="converting", error=None)

    env["pipeline"]._mark_failed(env["items"], summary, "boom")

    track = env["db"].get_track(track_id)
    assert track.status == "error"
    assert track.error == "boom"
    assert summary["failed"] == 1

    # A second call does not count the same song twice.
    env["pipeline"]._mark_failed(env["items"], summary, "boom")
    assert summary["failed"] == 1


def test_convert_reports_item_progress(env):
    seen = []
    env["pipeline"].progress.connect(seen.append)
    summary = _summary()

    env["pipeline"]._execute(
        env["items"], ["download", "convert"], False,
        env["settings"], summary,
    )

    convert = [item for item in seen if item["stage"] == "convert"]
    assert any(item["item_percent"] == 50 for item in convert)


def test_ffmpeg_progress_lines_become_percentages():
    import sys

    from musicdlp.services.ffmpeg import FFmpegService

    values = []
    code, _stderr = FFmpegService("")._run(
        [
            sys.executable, "-c",
            "print('out_time=00:00:01.000000', flush=True)\n"
            "print('progress=end', flush=True)",
        ],
        timeout=20,
        progress_cb=values.append,
        duration=2.0,
    )

    assert code == 0
    assert values[0] == 50.0
    assert values[-1] == 100.0


def test_a_pending_edit_is_written_on_the_next_normal_convert(
    env, monkeypatch, no_lookups
):
    """Saving details without rewriting must not be forgotten by Convert."""
    calls = []

    def fake_embed(path, **kwargs):
        calls.append(kwargs.get("title"))
        return True

    monkeypatch.setattr(pipeline_module, "embed_tags", fake_embed)
    out = Path(env["settings"].output_dir) / "MP3"
    out.mkdir(parents=True, exist_ok=True)
    song = out / "Old Name.mp3"
    song.write_bytes(b"audio-bytes")
    track_id = env["track_ids"][0]
    env["db"].update_track(
        track_id, status="converted", mp3_path=str(song), raw_path=None,
        meta_title="New Name", needs_update=1,
    )
    item = BatchItem(
        source=env["source"], track=env["db"].get_track(track_id), position=1
    )

    summary = _summary()
    env["pipeline"]._execute(
        [item], ["convert"], False, env["settings"], summary
    )

    track = env["db"].get_track(track_id)
    assert track.needs_update == 0
    assert Path(track.mp3_path).name == "New Name.mp3"
    assert calls == ["New Name"]
    assert summary["converted"] == 1
    # The audio was renamed, not re-encoded.
    assert env["ffmpeg"].calls == []


def test_a_cancelled_conversion_leaves_no_temp_file(tmp_path, monkeypatch):
    from musicdlp.core.errors import Cancelled
    from musicdlp.services.ffmpeg import FFmpegService

    service = FFmpegService("")
    service.ffmpeg = tmp_path / "ffmpeg.exe"
    monkeypatch.setattr(
        service, "measure_loudness", lambda *a, **k: None
    )
    monkeypatch.setattr(
        service, "probe", lambda path: {"duration": 10}
    )

    def cancel(*args, **kwargs):
        raise Cancelled()

    monkeypatch.setattr(service, "_run", cancel)
    raw = tmp_path / "input.webm"
    raw.write_bytes(b"audio")
    output = tmp_path / "out" / "Song.mp3"
    output.parent.mkdir()

    with pytest.raises(Cancelled):
        service.convert(raw, output, fmt="mp3", normalize=False)

    assert list(output.parent.iterdir()) == []


def test_a_failed_delivery_keeps_the_old_spotify_copy(env):
    """The old copy is the only thing keeping the song in Spotify."""
    settings = env["settings"]
    db = env["db"]
    track_id = env["track_ids"][0]
    out = Path(settings.output_dir) / "MP3"
    out.mkdir(parents=True, exist_ok=True)
    old = out / "Old Name.mp3"
    old.write_bytes(b"audio-bytes")
    spotify_copy = Path(settings.spotify_dir) / "Old Name.mp3"
    spotify_copy.parent.mkdir(parents=True, exist_ok=True)
    spotify_copy.write_bytes(b"audio-bytes")
    db.update_track(
        track_id, status="converted", mp3_path=str(old), raw_path=None,
        meta_title="New Name", needs_update=1,
    )

    def failing_sync(files, destination, mode="copy"):
        result = SyncResult()
        result.failed = len(list(files))
        result.errors.append("The Spotify folder could not be written.")
        return result

    env["spotify"].sync = failing_sync
    item = BatchItem(
        source=env["source"], track=db.get_track(track_id), position=1
    )
    summary = _summary()
    env["pipeline"]._execute([item], ["convert", "sync"], False, settings, summary)

    assert summary["synced"] == 0
    assert spotify_copy.exists(), "the old copy was deleted although the new one never arrived"
    assert (out / "New Name.mp3").exists()


def test_a_successful_delivery_removes_the_old_spotify_copy(env):
    settings = env["settings"]
    db = env["db"]
    track_id = env["track_ids"][0]
    out = Path(settings.output_dir) / "MP3"
    out.mkdir(parents=True, exist_ok=True)
    old = out / "Old Name.mp3"
    old.write_bytes(b"audio-bytes")
    spotify_copy = Path(settings.spotify_dir) / "Old Name.mp3"
    spotify_copy.parent.mkdir(parents=True, exist_ok=True)
    spotify_copy.write_bytes(b"audio-bytes")
    db.update_track(
        track_id, status="converted", mp3_path=str(old), raw_path=None,
        meta_title="New Name", needs_update=1,
    )

    item = BatchItem(
        source=env["source"], track=db.get_track(track_id), position=1
    )
    summary = _summary()
    env["pipeline"]._execute([item], ["convert", "sync"], False, settings, summary)

    assert summary["synced"] == 1
    assert not spotify_copy.exists()
    assert (Path(settings.spotify_dir) / "New Name.mp3").exists()


def test_the_sync_stage_reports_that_it_is_sending(env):
    """"Sending to Spotify" was defined but never shown."""
    settings = env["settings"]
    db = env["db"]
    track_id = env["track_ids"][0]
    out = Path(settings.output_dir) / "MP3"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "Song.mp3"
    path.write_bytes(b"audio")
    db.update_track(track_id, status="converted", mp3_path=str(path))

    seen = {}
    original = env["spotify"].sync

    def spy(files, destination, mode="copy"):
        seen["status"] = db.get_track(track_id).status
        return original(files, destination, mode)

    env["spotify"].sync = spy
    item = BatchItem(
        source=env["source"], track=db.get_track(track_id), position=1
    )
    env["pipeline"]._execute(
        [item], ["sync"], False, settings, _summary()
    )

    assert seen["status"] == "syncing"
    assert db.get_track(track_id).status == "synced"
