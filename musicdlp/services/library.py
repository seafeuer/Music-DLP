"""Library operations: adding links, refreshing sources, deleting items."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.errors import AppError, Cancelled
from musicdlp.core.models import (
    COVER_CUSTOM,
    COVER_VIDEO,
    is_converted,
    is_downloaded,
)
from musicdlp.core.paths import art_cache_dir
from musicdlp.services.artwork import cover_cache_path, find_cover
from musicdlp.services.filenames import safe_stem
from musicdlp.services.smart_titles import suggest as smart_suggest
from musicdlp.services.tagging import replace_cover
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

    def delete_track(
        self,
        track_id: int,
        *,
        delete_files: bool = False,
        forget: bool = False,
    ) -> None:
        """Remove a track from the library.

        By default the song is only unlinked from its sources, so a playlist
        refresh can bring it back. ``forget`` also drops the row, which is
        what removing a duplicate copy means: the entry is gone for good.
        """
        track = self.db.get_track(track_id)
        self.db.unlink_track_everywhere(track_id)
        if delete_files and track is not None:
            self._delete_files(track)
        if (delete_files or forget) and track is not None:
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

    def set_suggestion(
        self,
        track_id: int,
        title: str,
        artist: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Record a suggestion without applying it to the song."""
        self.db.update_track(
            track_id,
            smart_title=(title or "").strip() or None,
            smart_artist=(artist or "").strip() or None,
            smart_confidence=float(confidence or 0.0),
            smart_reason=(reason or "").strip() or None,
        )

    def clear_suggestion(self, track_id: int) -> None:
        """Forget a suggestion once its name has been applied.

        Without this the row keeps offering a suggestion that is already in
        use, because the stored name still differs from the old upload title.
        """
        self.db.update_track(
            track_id,
            smart_title=None,
            smart_artist=None,
            smart_confidence=None,
            smart_reason=None,
        )

    def refresh_cover(
        self, track_id: int, *, thumbnails=None
    ) -> str:
        """Look the artwork up again and put it into the files that exist.

        Returns ``updated``, ``unchanged``, ``skipped`` (no file to write),
        ``kept`` (the cover is one the user chose) or ``error``. The audio
        is not re-encoded: only the picture changes.

        This is what makes a corrected lookup reach songs that were
        converted before the rules were fixed, without downloading them
        again.
        """
        track = self.db.get_track(track_id)
        if track is None:
            return "error"

        # A picture the user picked, or the video's own still they chose
        # instead, is not "wrong". Looking it up again would put back the
        # artwork they just rejected.
        if getattr(track, "cover_source", None) in (COVER_CUSTOM, COVER_VIDEO):
            return "kept"

        files = [
            Path(path) for path in (track.mp3_path, track.m4a_path)
            if path and Path(path).exists()
        ]
        if not files:
            return "skipped"

        artist = track.display_artist
        title = track.display_title
        found = None
        if title:
            found = find_cover(
                artist,
                title,
                cover_cache_path(track.platform, track.media_id),
                album=track.display_album,
                refresh=True,
                seconds=track.duration,
            )

        # No catalogue entry really is this song, so the video's own
        # picture is used, enlarged to the cover size.
        if found is None and thumbnails is not None:
            from musicdlp.services.thumbnails import youtube_fallbacks

            fallbacks = (
                youtube_fallbacks(track.media_id)
                if track.platform == "youtube" else None
            )
            found = thumbnails.fetch(
                track.platform, track.media_id,
                track.thumbnail_url, fallbacks, refresh=True,
            )
        if found is None:
            return "unchanged"

        wrote = False
        for path in files:
            if replace_cover(path, found):
                wrote = True
        if not wrote:
            return "error"

        # A copy already delivered to Spotify has the old picture too.
        for path in files:
            copy = self.settings.spotify_path / path.name
            if copy.exists() and copy != path:
                replace_cover(copy, found)

        self.db.update_track(track_id, cover_path=str(found))
        return "updated"

    def relink_from_folder(self, folder: Path) -> dict:
        """Point tracks whose file is missing at a copy found in a folder.

        A library made on another computer, or one whose output folder was
        changed, keeps paths that no longer resolve. Choosing the folder
        the songs are in now repairs them, instead of downloading
        everything again.

        Files are matched by name, so this only ever links a file whose
        name is exactly the one the track expected.
        """
        folder = Path(folder)
        counts = {"relinked": 0, "missing": 0}
        if not folder.is_dir():
            return counts

        by_name: dict[str, Path] = {}
        try:
            for candidate in folder.rglob("*"):
                if candidate.is_file() and candidate.suffix.lower() in (
                    ".mp3", ".m4a"
                ):
                    by_name.setdefault(candidate.name.lower(), candidate)
        except OSError as exc:
            logger.warning("Could not read %s: %s", folder, exc)
            return counts

        for track in self.db.get_all_tracks():
            current = [p for p in (track.mp3_path, track.m4a_path) if p]
            if any(Path(p).exists() for p in current):
                continue
            wanted = [Path(p).name.lower() for p in current]
            found = next(
                (by_name[name] for name in wanted if name in by_name), None
            )
            if found is None:
                counts["missing"] += 1
                continue

            updates = {"mp3_path": str(found)} if found.suffix.lower() == ".mp3"                 else {"m4a_path": str(found)}
            # The file is back, so a song that was only marked missing
            # returns to the state it had.
            if is_converted(track.status):
                updates["needs_update"] = 0
            self.db.update_track(track.id, **updates)
            counts["relinked"] += 1
        return counts

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
                cover_source=None,
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
            cover_source=COVER_CUSTOM,
            needs_update=1 if is_converted(track.status) else 0,
        )
        return destination

    def restore_video_cover(self, track_id: int) -> None:
        """Drop a custom cover so the video's own picture is used.

        The choice is recorded, not just the missing path: a conversion
        would otherwise look for catalogue art again and quietly put back
        the picture the user just rejected.
        """
        track = self.db.get_track(track_id)
        if track is None:
            return
        self.db.update_track(
            track_id,
            cover_path=None,
            cover_source=COVER_VIDEO,
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
