"""Library operations: adding links, refreshing sources, deleting items."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.errors import AppError, Cancelled
from musicdlp.core.models import is_converted, is_downloaded
from musicdlp.core.paths import art_cache_dir
from musicdlp.services.filenames import safe_stem
from musicdlp.services.smart_titles import suggest as smart_suggest
from musicdlp.services.ytdlp import YtDlpService

logger = logging.getLogger("musicdlp.library")


@dataclass
class AddResult:
    sources: list[int] = field(default_factory=list)
    tracks_total: int = 0
    tracks_new: int = 0
    tracks_owned: int = 0
    titles_fixed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.sources)


class LibraryService:
    def __init__(self, db: Database, settings: Settings, ytdlp: YtDlpService) -> None:
        self.db = db
        self.settings = settings
        self.ytdlp = ytdlp

    # ------------------------------------------------------------- add/refresh

    def add_links(
        self,
        urls: Iterable[str],
        *,
        cancel_event=None,
        progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> AddResult:
        urls = list(urls)
        result = AddResult()
        total = len(urls)

        for index, url in enumerate(urls, start=1):
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            if progress:
                progress(index - 1, total, url)
            try:
                self._add_one(url, result, cancel_event)
            except Cancelled:
                raise
            except AppError as exc:
                logger.error("Could not add %s: %s", url, exc.user_text())
                result.errors.append(exc.user_text())
            except Exception as exc:
                logger.exception("Could not add %s", url)
                result.errors.append(
                    "That link could not be read. Check that it is complete "
                    "and try again."
                )
            finally:
                if progress:
                    progress(index, total, url)

        return result

    def _add_one(self, url: str, result: AddResult, cancel_event=None) -> None:
        extracted = self.ytdlp.extract(url, cancel_event=cancel_event)
        source_id = self.db.add_source(
            url=extracted.url,
            kind=extracted.kind,
            platform=extracted.platform,
            external_id=extracted.external_id,
            title=extracted.title,
            uploader=extracted.uploader,
            item_count=len(extracted.tracks),
            thumbnail_url=extracted.thumbnail_url,
        )
        links = self._store_tracks(extracted.tracks, result)
        self.db.set_source_tracks(source_id, links)
        result.sources.append(source_id)
        result.tracks_total += len(links)

    def refresh_source(self, source_id: int, *, cancel_event=None) -> AddResult:
        source = self.db.get_source(source_id)
        if source is None:
            raise AppError("That library entry no longer exists.", kind="library")

        result = AddResult()
        extracted = self.ytdlp.extract(source.url, cancel_event=cancel_event)

        # Keep the existing URL so the source keeps its identity even if the
        # canonical form changed since it was first added.
        self.db.add_source(
            url=source.url,
            kind=extracted.kind,
            platform=extracted.platform,
            external_id=extracted.external_id,
            title=extracted.title,
            uploader=extracted.uploader,
            item_count=len(extracted.tracks),
            thumbnail_url=extracted.thumbnail_url,
        )
        links = self._store_tracks(extracted.tracks, result)
        self.db.set_source_tracks(source_id, links)
        result.sources.append(source_id)
        result.tracks_total = len(links)
        return result

    def _store_tracks(self, tracks, result: AddResult) -> list[tuple[int, int]]:
        links: list[tuple[int, int]] = []
        known = self.db.known_artists() if self.settings.smart_titles else set()

        for track in tracks:
            existing = self.db.get_track_by_media(track.platform, track.media_id)
            track_id = self.db.upsert_track(
                platform=track.platform,
                media_id=track.media_id,
                url=track.url,
                title=track.title,
                uploader=track.uploader or None,
                duration=track.duration,
                thumbnail_url=track.thumbnail_url or None,
                description=track.description or None,
                source_index=track.source_index,
            )
            links.append((track_id, track.position))
            if existing is None:
                result.tracks_new += 1
            elif is_downloaded(existing.status):
                result.tracks_owned += 1

            if self.settings.smart_titles:
                applied = self._apply_smart_title(track_id, track, known)
                if applied:
                    result.titles_fixed += 1

        return links

    def _apply_smart_title(self, track_id: int, track, known: set[str]) -> bool:
        """Store a suggestion, and use it when the parser is confident.

        A track whose details the user has already edited is never touched.
        """
        stored = self.db.get_track(track_id)
        if stored is None:
            return False

        suggestion = smart_suggest(
            track.title,
            channel=track.uploader,
            description=track.description,
            known_artists=known,
        )
        if not suggestion.title:
            return False

        update: dict = {
            "smart_title": suggestion.title,
            "smart_artist": suggestion.artist or None,
            "smart_confidence": suggestion.confidence,
            "smart_reason": suggestion.reason,
        }

        untouched = stored.meta_title is None and stored.meta_artist is None
        if suggestion.confident and untouched:
            update["meta_title"] = suggestion.title
            if suggestion.artist:
                update["meta_artist"] = suggestion.artist

        self.db.update_track(track_id, **update)
        return bool(suggestion.confident and untouched)

    # ------------------------------------------------------------- delete

    def delete_source(self, source_id: int, *, delete_files: bool = False) -> None:
        rows = self.db.get_tracks_for_source(source_id)
        self.db.delete_source(source_id)

        if not delete_files:
            return

        for row in rows:
            if self.db.count_track_links(row.track.id) == 0:
                self._delete_files(row.track)
                self.db.delete_track(row.track.id)

    def delete_track(self, track_id: int, *, delete_files: bool = False) -> None:
        track = self.db.get_track(track_id)
        self.db.unlink_track_everywhere(track_id)
        if delete_files and track is not None:
            self._delete_files(track)
            self.db.delete_track(track_id)

    def _delete_files(self, track) -> None:
        for path in (track.raw_path, track.mp3_path, track.m4a_path,
                     track.cover_path):
            if not path:
                continue
            candidate = Path(path)
            try:
                if candidate.is_file():
                    candidate.unlink()
            except OSError as exc:
                logger.warning("Could not delete %s: %s", candidate, exc)

    # ------------------------------------------------------------- metadata

    def set_metadata(
        self,
        track_id: int,
        *,
        title: str,
        artist: str = "",
        album: str = "",
        track_number: Optional[int] = None,
    ) -> None:
        """Save edited metadata and flag the written file as out of date."""
        track = self.db.get_track(track_id)
        self.db.update_track(
            track_id,
            meta_title=(title or "").strip() or None,
            meta_artist=(artist or "").strip() or None,
            meta_album=(album or "").strip() or None,
            meta_track=track_number,
            # A converted file still holds the old tags and filename.
            needs_update=1 if track and is_converted(track.status) else 0,
        )

    def set_cover(self, track_id: int, image_path: Optional[Path]) -> Optional[Path]:
        """Use a user-chosen image as the cover art for a track.

        The image is copied into the app cache so it survives the original
        file being moved or deleted.
        """
        track = self.db.get_track(track_id)
        if track is None:
            return None

        if image_path is None:
            self.db.update_track(
                track_id,
                cover_path=None,
                needs_update=1 if is_converted(track.status) else 0,
            )
            return None

        source = Path(image_path)
        if not source.exists():
            raise AppError("That image could not be found.", kind="file")

        destination = art_cache_dir() / f"custom_{track.platform}_{safe_stem(track.media_id, 60)}.jpg"
        try:
            from PIL import Image, ImageOps

            image = Image.open(source)
            if image.mode != "RGB":
                image = image.convert("RGB")
            image = ImageOps.fit(
                image, (640, 640),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, "JPEG", quality=92, optimize=True)
        except Exception as exc:
            raise AppError(
                "That file could not be used as an image.",
                "Choose a JPG or PNG picture.",
                kind="file",
                cause=exc,
            ) from exc

        self.db.update_track(
            track_id,
            cover_path=str(destination),
            needs_update=1 if is_converted(track.status) else 0,
        )
        return destination

    def restore_video_cover(self, track_id: int) -> None:
        """Drop a custom cover so the next conversion re-fetches the video's."""
        track = self.db.get_track(track_id)
        if track is None:
            return
        self.db.update_track(
            track_id,
            cover_path=None,
            needs_update=1 if is_converted(track.status) else 0,
        )

    # ------------------------------------------------------------- cleanup

    def clean_partial_files(self) -> int:
        """Remove interrupted yt-dlp temp files from the raw folder."""
        raw = self.settings.raw_dir
        if not raw.exists():
            return 0
        removed = 0
        for pattern in ("*.part", "*.ytdl", "*.tmp"):
            for path in raw.glob(pattern):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        if removed:
            logger.info("Removed %d partial file(s)", removed)
        return removed
