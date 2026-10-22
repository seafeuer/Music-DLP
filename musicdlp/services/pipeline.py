"""Batch job orchestration: download, convert, and Spotify sync.

The pipeline runs on a plain Python thread and communicates with the UI only
through Qt signals, which Qt queues onto the GUI thread. Every worker gets its
own SQLite connection from ``Database``.
"""

from __future__ import annotations

import copy
import logging
import random
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import QObject, Signal

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.errors import AppError, Cancelled
from musicdlp.core.models import (
    CANCELLED,
    CONVERTED,
    DOWNLOADED,
    DOWNLOADING,
    CONVERTING,
    ERROR,
    QUEUED,
    SYNCED,
    SYNCING,
    BatchItem,
    Track,
)
from musicdlp.core.paths import art_cache_dir
from musicdlp.services.artwork import cover_cache_path, find_cover
from musicdlp.services.filenames import render_template, unique_path
from musicdlp.services.ffmpeg import FFmpegService
from musicdlp.services.spotify import SpotifyService
from musicdlp.services.tagging import embed_tags
from musicdlp.services.thumbnails import ThumbnailService, youtube_fallbacks
from musicdlp.services.ytdlp import YtDlpService

logger = logging.getLogger("musicdlp.pipeline")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _record_failure(summary: dict, track_id: int, message: str) -> None:
    """Count each track once even if it fails in several stages."""
    failed_ids = summary.setdefault("_failed_ids", set())
    if track_id not in failed_ids:
        failed_ids.add(track_id)
        summary["failed"] += 1
    summary["errors"].append(message)


class _Tracker:
    """Turns per-item progress into a single overall percentage."""

    def __init__(self, pipeline: "Pipeline", item_count: int, stage_count: int) -> None:
        self.pipeline = pipeline
        self.total_units = max(1, item_count * stage_count)
        self.done_units = 0

    def item_progress(
        self,
        stage: str,
        title: str,
        percent: float,
        speed: float = 0,
        eta: float = 0,
        track_id: Optional[int] = None,
    ) -> None:
        percent = max(0.0, min(100.0, float(percent or 0)))
        overall = (self.done_units + percent / 100.0) / self.total_units * 100
        self.pipeline.progress.emit({
            "stage": stage,
            "percent": overall,
            "item_percent": percent,
            "title": title,
            "speed": speed or 0,
            "eta": eta or 0,
            "done": self.done_units,
            "total": self.total_units,
            "track_id": track_id,
        })

    def complete_unit(
        self, stage: str, title: str, track_id: Optional[int] = None
    ) -> None:
        self.done_units += 1
        overall = self.done_units / self.total_units * 100
        self.pipeline.progress.emit({
            "stage": stage,
            "percent": overall,
            "item_percent": 100.0,
            "title": title,
            "speed": 0,
            "eta": 0,
            "done": self.done_units,
            "total": self.total_units,
            "track_id": track_id,
        })


class Pipeline(QObject):
    """Runs a batch in the background."""

    track_changed = Signal(int)
    progress = Signal(dict)
    message = Signal(str, str)
    finished = Signal(dict)
    failed = Signal(str, str)

    STAGES = ("download", "convert", "sync")

    def __init__(
        self,
        db: Database,
        settings: Settings,
        ytdlp: YtDlpService,
        ffmpeg: FFmpegService,
        thumbnails: ThumbnailService,
        spotify: SpotifyService,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.settings = settings
        self.ytdlp = ytdlp
        self.ffmpeg = ffmpeg
        self.thumbnails = thumbnails
        self.spotify = spotify

        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._only_synced = False
        self._lock = threading.Lock()

    # --------------------------------------------------------------- control

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(
        self,
        items: Iterable[BatchItem],
        stages: Iterable[str],
        *,
        force: bool = False,
        only_synced: bool = False,
    ) -> bool:
        items = list(items)
        stages = [stage for stage in self.STAGES if stage in set(stages)]
        if not items or not stages:
            return False

        with self._lock:
            if self.is_running:
                return False
            self._cancel = threading.Event()
            self._only_synced = only_synced
            self._thread = threading.Thread(
                target=self._run,
                args=(items, stages, force),
                daemon=True,
                name="MusicDLP-Pipeline",
            )
            self._thread.start()
            return True

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # ------------------------------------------------------------------ run

    def _run(self, items: list[BatchItem], stages: list[str], force: bool) -> None:
        settings = copy.deepcopy(self.settings)
        summary = {
            "downloaded": 0,
            "converted": 0,
            "synced": 0,
            "failed": 0,
            "skipped": 0,
            "errors": [],
            "cancelled": False,
            "stages": stages,
        }
        try:
            self._execute(items, stages, force, settings, summary)
        except Cancelled:
            summary["cancelled"] = True
            self._mark_cancelled(items)
            self.message.emit("INFO", "Job cancelled.")
        except Exception as exc:
            logger.exception("Pipeline failed")
            summary["fatal"] = str(exc)
            self.failed.emit("Job failed", str(exc))
        finally:
            with self._lock:
                self._thread = None
            self.message.emit("INFO", self._summary_text(summary))
            self.finished.emit(summary)

    def _mark_cancelled(self, items: list[BatchItem]) -> None:
        """Leave nothing stuck on an in-progress status after a cancel."""
        for item in items:
            track = self.db.get_track(item.track.id)
            if track is None:
                continue
            if track.status in (QUEUED, DOWNLOADING, CONVERTING, SYNCING):
                self.db.update_track(track.id, status=CANCELLED)
                self.track_changed.emit(track.id)

    def _execute(
        self,
        items: list[BatchItem],
        stages: list[str],
        force: bool,
        settings: Settings,
        summary: dict,
    ) -> None:
        tracker = _Tracker(self, len(items), len(stages))
        for stage in stages:
            if self._cancel.is_set():
                raise Cancelled()
            logger.info("Stage: %s (%d items)", stage, len(items))
            if stage == "download":
                self._stage_download(items, settings, tracker, summary, force)
            elif stage == "convert":
                self._stage_convert(items, settings, tracker, summary, force)
            elif stage == "sync":
                self._stage_sync(items, settings, tracker, summary)
        if self._cancel.is_set():
            raise Cancelled()

    @staticmethod
    def _summary_text(summary: dict) -> str:
        if summary.get("cancelled"):
            return "Job cancelled."
        parts = []
        if summary["downloaded"]:
            parts.append(f"downloaded {summary['downloaded']}")
        if summary["converted"]:
            parts.append(f"converted {summary['converted']}")
        if summary["synced"]:
            parts.append(f"sent {summary['synced']} to Spotify")
        if summary["failed"]:
            parts.append(f"{summary['failed']} failed")
        if summary["skipped"]:
            parts.append(f"{summary['skipped']} skipped")
        return "Finished: " + ", ".join(parts) if parts else "Nothing to do."

    # --------------------------------------------------------------- stages

    def _stage_download(
        self,
        items: list[BatchItem],
        settings: Settings,
        tracker: _Tracker,
        summary: dict,
        force: bool,
    ) -> None:
        raw_dir = settings.raw_dir
        raw_dir.mkdir(parents=True, exist_ok=True)

        for index, item in enumerate(items):
            if self._cancel.is_set():
                raise Cancelled()

            track = self.db.get_track(item.track.id)
            if track is None:
                continue
            title = track.display_title

            if (
                not force
                and track.raw_path
                and Path(track.raw_path).exists()
            ):
                summary["skipped"] += 1
                tracker.complete_unit("download", title, track.id)
                continue

            self.db.update_track(track.id, status=QUEUED, error=None)
            self.track_changed.emit(track.id)
            self.db.update_track(track.id, status=DOWNLOADING)
            self.track_changed.emit(track.id)
            tracker.item_progress("download", title, 0, track_id=track.id)

            try:
                def on_progress(data: dict, title: str = title,
                                track_id: int = track.id) -> None:
                    tracker.item_progress(
                        "download",
                        title,
                        data.get("percent", 0),
                        data.get("speed", 0),
                        data.get("eta", 0),
                        track_id=track_id,
                    )

                path = self.ytdlp.download(
                    track,
                    raw_dir,
                    progress_cb=on_progress,
                    cancel_event=self._cancel,
                )

                fallbacks = (
                    youtube_fallbacks(track.media_id)
                    if track.platform == "youtube" else None
                )
                cover = self.thumbnails.fetch(
                    track.platform, track.media_id, track.thumbnail_url,
                    fallbacks,
                )

                self.db.update_track(
                    track.id,
                    raw_path=str(path),
                    status=DOWNLOADED,
                    downloaded_at=_now(),
                    error=None,
                    cover_path=str(cover) if cover else track.cover_path,
                )
                summary["downloaded"] += 1

            except Cancelled:
                raise
            except AppError as exc:
                self.db.update_track(track.id, status=ERROR, error=exc.user_text())
                _record_failure(summary, track.id, f"{title}: {exc.message}")
            except Exception as exc:
                logger.exception("Download failed for %s", title)
                self.db.update_track(track.id, status=ERROR, error=str(exc))
                _record_failure(summary, track.id, f"{title}: {exc}")
            finally:
                self.track_changed.emit(track.id)
                tracker.complete_unit("download", title, track.id)

            # Space out requests so the sites do not treat us as a bot.
            if index < len(items) - 1:
                low = max(0.0, settings.sleep_min)
                high = max(low, settings.sleep_max)
                delay = random.uniform(low, high) if high > 0 else 0.0
                if delay > 0:
                    deadline = time.monotonic() + delay
                    while time.monotonic() < deadline:
                        if self._cancel.is_set():
                            raise Cancelled()
                        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def _filename_template(self, source, settings: Settings) -> str:
        """The naming pattern for this song.

        A link can carry its own pattern, and a folder's pattern applies to
        every link filed inside it. The global setting is the fallback.
        """
        if source is None:
            return settings.filename_template
        own = getattr(source, "filename_template", None)
        if own:
            return own
        folder_name = (getattr(source, "folder", "") or "").strip()
        if folder_name:
            try:
                folder = self.db.get_folder_by_name(folder_name)
            except Exception:  # noqa: BLE001 - naming must never break a run
                folder = None
            if folder is not None and folder.filename_template:
                return folder.filename_template
        return settings.filename_template

    def _stage_convert(
        self,
        items: list[BatchItem],
        settings: Settings,
        tracker: _Tracker,
        summary: dict,
        force: bool,
    ) -> None:
        for item in items:
            if self._cancel.is_set():
                raise Cancelled()

            track = self.db.get_track(item.track.id)
            if track is None:
                continue
            title = track.display_title

            # Do not let a later stage overwrite the error that caused the
            # failure, or count the same track twice.
            if track.id in summary.get("_failed_ids", set()):
                tracker.complete_unit("convert", title, track.id)
                continue

            raw = Path(track.raw_path) if track.raw_path else None

            if raw is None or not raw.exists():
                if not force and self._outputs_exist(track, settings):
                    summary["skipped"] += 1
                    tracker.complete_unit("convert", title, track.id)
                    continue
                self.db.update_track(
                    track.id, status=ERROR,
                    error="Download the track before converting it.",
                )
                _record_failure(summary, track.id, f"{title}: not downloaded")
                self.track_changed.emit(track.id)
                tracker.complete_unit("convert", title, track.id)
                continue

            self.db.update_track(track.id, status=CONVERTING, error=None)
            self.track_changed.emit(track.id)
            tracker.item_progress("convert", title, 0, track_id=track.id)

            errors: list[str] = []
            album = track.display_album or (
                item.source.title if item.source.kind == "playlist" else "Singles"
            )
            track_no = track.display_track or item.position

            for fmt in settings.formats:
                if self._cancel.is_set():
                    raise Cancelled()

                existing = track.mp3_path if fmt == "mp3" else track.m4a_path
                if existing and Path(existing).exists() and not force:
                    continue

                out_dir = settings.output_path / fmt.upper()
                out_dir.mkdir(parents=True, exist_ok=True)

                stem = render_template(
                    self._filename_template(item.source, settings),
                    title=track.display_title,
                    artist=track.display_artist,
                    album=album,
                    track=track_no,
                    media_id=track.media_id,
                    source=item.source.title,
                )

                # Reuse the current file when the name still matches, so a
                # plain re-convert overwrites in place. When the user changed
                # the metadata, the new name differs and a fresh file is
                # written; the old one is removed after a successful write.
                old_output = Path(existing) if existing else None
                if (
                    old_output is not None
                    and old_output.exists()
                    and old_output.stem == stem
                ):
                    output = old_output
                else:
                    output = unique_path(
                        out_dir, stem, f".{fmt}", fallback_id=track.media_id
                    )

                try:
                    self.ffmpeg.convert(
                        raw,
                        output,
                        fmt=fmt,
                        bitrate=(
                            settings.mp3_bitrate if fmt == "mp3"
                            else settings.m4a_bitrate
                        ),
                        normalize=settings.normalize,
                        cancel_event=self._cancel,
                    )

                    cover = (
                        Path(track.cover_path)
                        if track.cover_path and Path(track.cover_path).exists()
                        else None
                    )

                    # Prefer real album art over the video still, when the
                    # user has that turned on.
                    if settings.ai_find_artwork:
                        album_art = find_cover(
                            track.display_artist,
                            track.display_title,
                            cover_cache_path(track.platform, track.media_id),
                            album=track.display_album,
                        )
                        if album_art is not None:
                            cover = album_art
                            self.db.update_track(
                                track.id, cover_path=str(album_art)
                            )

                    if cover is None:
                        fallbacks = (
                            youtube_fallbacks(track.media_id)
                            if track.platform == "youtube" else None
                        )
                        cover = self.thumbnails.fetch(
                            track.platform, track.media_id,
                            track.thumbnail_url, fallbacks,
                        )
                        if cover:
                            self.db.update_track(track.id, cover_path=str(cover))

                    embed_tags(
                        output,
                        title=track.display_title,
                        artist=track.display_artist or "Unknown",
                        album=album,
                        track=track_no,
                        cover_path=cover,
                    )

                    if fmt == "mp3":
                        self.db.update_track(track.id, mp3_path=str(output))
                    else:
                        self.db.update_track(track.id, m4a_path=str(output))

                    # The metadata change renamed the file: drop the stale
                    # one so the folder does not accumulate duplicates.
                    if (
                        old_output is not None
                        and old_output.exists()
                        and old_output != output
                    ):
                        try:
                            old_output.unlink()
                        except OSError as exc:
                            logger.debug("Could not remove old file: %s", exc)

                        # The Spotify copy is only removed once the new one
                        # has been copied, in the sync stage. Deleting it here
                        # made songs disappear from Spotify when the run did
                        # not include a sync.
                        stale_copies = summary.setdefault(
                            "_stale_spotify", []
                        )
                        stale_copies.append(old_output.name)

                except Cancelled:
                    raise
                except AppError as exc:
                    errors.append(exc.user_text())
                except Exception as exc:
                    logger.exception("Conversion failed for %s (%s)", title, fmt)
                    errors.append(str(exc))

            if errors:
                self.db.update_track(
                    track.id, status=ERROR, error="\n".join(errors)
                )
                _record_failure(
                    summary, track.id, f"{title}: " + "; ".join(errors)
                )
            else:
                self.db.update_track(
                    track.id,
                    status=CONVERTED,
                    converted_at=_now(),
                    error=None,
                    needs_update=0,
                )
                summary["converted"] += 1
                if not settings.keep_raw and raw.exists():
                    try:
                        raw.unlink()
                    except OSError as exc:
                        logger.debug("Could not remove raw file: %s", exc)
                    else:
                        self.db.update_track(track.id, raw_path=None)

            self.track_changed.emit(track.id)
            tracker.complete_unit("convert", title, track.id)

    def _stage_sync(
        self,
        items: list[BatchItem],
        settings: Settings,
        tracker: _Tracker,
        summary: dict,
    ) -> None:
        files: list[Path] = []
        pending: list[tuple[BatchItem, Track, Path]] = []

        for item in items:
            if self._cancel.is_set():
                raise Cancelled()

            track = self.db.get_track(item.track.id)
            if track is None:
                continue

            if track.id in summary.get("_failed_ids", set()):
                tracker.complete_unit("sync", track.display_title, track.id)
                continue

            # An "update files" run only refreshes copies that already exist
            # in Spotify; it must not add new songs to the Spotify folder.
            if getattr(self, "_only_synced", False) and not track.synced_at:
                tracker.complete_unit("sync", track.display_title, track.id)
                continue

            path = (
                track.mp3_path if settings.spotify_format == "mp3"
                else track.m4a_path
            )
            if path and Path(path).exists():
                files.append(Path(path))
                pending.append((item, track, Path(path)))
            else:
                self.db.update_track(
                    track.id,
                    sync_error="No converted file to send to Spotify.",
                )
                _record_failure(
                    summary, track.id,
                    f"{track.display_title}: no converted file for Spotify",
                )
                tracker.complete_unit("sync", track.display_title, track.id)
                self.track_changed.emit(track.id)

        if files:
            result = self.spotify.sync(
                files, settings.spotify_path, settings.spotify_copy_mode
            )
            if result.errors:
                summary["errors"].extend(result.errors)

            for _item, track, path in pending:
                target = settings.spotify_path / path.name
                if target.exists():
                    update = {
                        "status": SYNCED,
                        "synced_at": _now(),
                        "sync_error": None,
                    }
                    if settings.spotify_copy_mode == "move":
                        if settings.spotify_format == "mp3":
                            update["mp3_path"] = str(target)
                        else:
                            update["m4a_path"] = str(target)
                    self.db.update_track(track.id, **update)
                    summary["synced"] += 1
                else:
                    self.db.update_track(
                        track.id,
                        sync_error="Copying to the Spotify folder failed.",
                    )
                    _record_failure(
                        summary, track.id,
                        f"{track.display_title}: Spotify copy failed",
                    )
                tracker.complete_unit("sync", track.display_title, track.id)
                self.track_changed.emit(track.id)

        if settings.spotify_auto_restart and summary["synced"]:
            ok, message = self.spotify.restart()
            self.message.emit("INFO" if ok else "WARNING", message)

        # Remove Spotify copies left behind by renamed files, now that the
        # replacements have been copied in.
        for stale_name in summary.get("_stale_spotify", []):
            if stale_name in {path.name for path in files}:
                continue
            stale = settings.spotify_path / stale_name
            try:
                if stale.exists():
                    stale.unlink()
            except OSError as exc:
                logger.debug("Could not remove stale Spotify copy: %s", exc)

    @staticmethod
    def _outputs_exist(track: Track, settings: Settings) -> bool:
        paths = []
        if "mp3" in settings.formats and track.mp3_path:
            paths.append(Path(track.mp3_path))
        if "m4a" in settings.formats and track.m4a_path:
            paths.append(Path(track.m4a_path))
        return bool(paths) and all(path.exists() for path in paths)
