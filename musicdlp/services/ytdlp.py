"""yt-dlp integration: extraction, download, progress, and cancellation."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yt_dlp

from musicdlp.core.config import Settings
from musicdlp.core.errors import AppError, Cancelled, classify_ytdlp_error
from musicdlp.core.models import Track
from musicdlp.services.filenames import safe_stem
from musicdlp.services.urls import (
    KIND_ITEM,
    KIND_PLAYLIST,
    classify_url,
    platform_key,
)
logger = logging.getLogger("musicdlp.ytdlp")

ProgressCallback = Callable[[dict], None]


@dataclass
class ExtractedTrack:
    platform: str
    media_id: str
    url: str
    title: str
    uploader: str = ""
    duration: int = 0
    thumbnail_url: str = ""
    description: str = ""
    source_index: Optional[int] = None
    position: int = 1


@dataclass
class ExtractedSource:
    url: str
    kind: str
    platform: str
    external_id: Optional[str]
    title: str
    uploader: str
    thumbnail_url: str
    tracks: list[ExtractedTrack] = field(default_factory=list)


class YtDlpService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.node_available = shutil.which("node") is not None

    # ------------------------------------------------------------------ opts

    def _cookie_opts(self) -> dict:
        mode = self.settings.cookies_mode
        if mode == "browser":
            return {"cookiesfrombrowser": (self.settings.cookies_browser,)}
        if mode == "file":
            path = Path(self.settings.cookies_file).expanduser()
            if path.exists():
                return {"cookiefile": str(path)}
            logger.warning("Cookies file not found: %s", path)
        return {}

    def _base_opts(self) -> dict:
        opts: dict = {
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "logger": logger,
            "socket_timeout": 20,
            "retries": self.settings.max_retries,
            "fragment_retries": self.settings.max_retries,
            "sleep_interval_requests": 1,
            "geo_bypass": True,
            "nocheckcertificate": False,
            "ignore_no_formats_error": True,
        }
        if self.node_available:
            opts["js_runtimes"] = {"node": {}}
            opts["remote_components"] = {"ejs:github": {}}
        opts.update(self._cookie_opts())
        return opts

    # -------------------------------------------------------------- extract

    def extract(self, url: str, cancel_event=None) -> ExtractedSource:
        info = classify_url(url)
        source_hint = info.platform if info else "other"

        opts = self._base_opts()
        opts.update({
            "skip_download": True,
            # A single link needs full extraction so that a post with no
            # video (a text-only tweet, a dead link) fails now, while the
            # user is adding it, instead of later during the download.
            "extract_flat": False if (info and info.kind == KIND_ITEM)
            else "in_playlist",
        })

        logger.info("Reading link: %s", url)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                data = ydl.extract_info(url, download=False)
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            raise classify_ytdlp_error(exc) from exc

        if not data:
            raise AppError("The link returned no information.",
                           "Check the URL and try again.", kind="extract")

        is_playlist = data.get("_type") in ("playlist", "multi_video")
        platform = platform_key(data.get("extractor_key"), source_hint)

        if is_playlist:
            return self._build_playlist(url, data, platform)

        return self._build_single(url, data, platform)

    def _build_playlist(self, url: str, data: dict, platform: str) -> ExtractedSource:
        entries = [entry for entry in (data.get("entries") or []) if entry]
        shared_page = self._entries_share_page(entries, url)

        tracks: list[ExtractedTrack] = []
        seen: set[tuple[str, str]] = set()

        for position, entry in enumerate(entries, start=1):
            track = self._parse_entry(
                entry,
                position=position,
                source_url=url,
                source_platform=platform,
                shared_page=shared_page,
            )
            if track is None:
                continue
            key = (track.platform, track.media_id)
            if key in seen:
                continue
            seen.add(key)
            tracks.append(track)

        if not tracks:
            raise AppError(
                "No downloadable items were found in this link.",
                "It may be empty, private, or contain only images.",
                kind="extract",
            )

        link = classify_url(url)
        return ExtractedSource(
            url=link.url if link else url,
            kind=KIND_PLAYLIST,
            platform=platform,
            external_id=str(data.get("id") or "") or None,
            title=(data.get("title") or "Untitled").strip(),
            uploader=self._uploader(data),
            thumbnail_url=self._thumbnail(data),
            tracks=tracks,
        )

    def _build_single(self, url: str, data: dict, platform: str) -> ExtractedSource:
        track = self._parse_entry(
            data, position=1, source_url=url, source_platform=platform,
            shared_page=False,
        )
        if track is None:
            raise AppError("The item could not be read.",
                           "Check the link and try again.", kind="extract")

        link = classify_url(url)
        return ExtractedSource(
            url=link.url if link else url,
            kind=KIND_ITEM,
            platform=track.platform,
            external_id=track.media_id,
            title=track.title,
            uploader=track.uploader,
            thumbnail_url=track.thumbnail_url,
            tracks=[track],
        )

    def _parse_entry(
        self,
        entry: dict,
        *,
        position: int,
        source_url: str,
        source_platform: str,
        shared_page: bool,
    ) -> Optional[ExtractedTrack]:
        media_id = str(entry.get("id") or "").strip()
        if not media_id:
            return None

        platform = platform_key(entry.get("extractor_key"), source_platform)
        url = (
            entry.get("webpage_url")
            or entry.get("original_url")
            or entry.get("url")
            or source_url
        )
        if not str(url).startswith(("http://", "https://")):
            # Flat YouTube entries sometimes carry only an id.
            if platform == "youtube":
                url = f"https://www.youtube.com/watch?v={media_id}"
            else:
                url = source_url

        source_index = None
        if shared_page:
            source_index = int(entry.get("playlist_index") or position)

        return ExtractedTrack(
            platform=platform,
            media_id=media_id,
            url=str(url),
            title=(entry.get("title") or "Untitled").strip(),
            uploader=self._uploader(entry),
            duration=int(entry.get("duration") or 0),
            thumbnail_url=self._thumbnail(entry),
            description=entry.get("description") or "",
            source_index=source_index,
            position=position,
        )

    @staticmethod
    def _entries_share_page(entries: list[dict], source_url: str) -> bool:
        resolved = [
            entry.get("webpage_url") or entry.get("url") or source_url
            for entry in entries
        ]
        return len(resolved) > 1 and len(set(resolved)) == 1

    @staticmethod
    def _uploader(data: dict) -> str:
        return (
            data.get("uploader")
            or data.get("channel")
            or data.get("creator")
            or data.get("uploader_id")
            or ""
        ).strip()

    @staticmethod
    def _thumbnail(data: dict) -> str:
        direct = data.get("thumbnail") or ""
        if direct:
            return direct
        for thumb in reversed(data.get("thumbnails") or []):
            if thumb.get("url"):
                return thumb["url"]
        media_id = str(data.get("id") or "")
        if media_id and platform_key(data.get("extractor_key")) == "youtube":
            return f"https://i.ytimg.com/vi/{media_id}/hqdefault.jpg"
        return ""

    # ------------------------------------------------------------- download

    def download(
        self,
        track: Track,
        raw_dir: Path,
        *,
        progress_cb: Optional[ProgressCallback] = None,
        cancel_event=None,
    ) -> Path:
        raw_dir = Path(raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)

        prefix = f"[{safe_stem(track.platform, 24)}_{safe_stem(track.media_id, 60)}]"
        outtmpl = str(raw_dir / f"{prefix}.%(ext)s")

        last_emit = [0.0]

        def hook(data: dict) -> None:
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            if progress_cb is None:
                return
            status = data.get("status")
            if status == "downloading":
                now = time.monotonic()
                if now - last_emit[0] < 0.2:
                    return
                last_emit[0] = now
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                downloaded = data.get("downloaded_bytes") or 0
                percent = (downloaded / total * 100) if total else 0
                progress_cb({
                    "stage": "download",
                    "percent": max(0.0, min(100.0, percent)),
                    "downloaded": downloaded,
                    "total": total,
                    "speed": data.get("speed") or 0,
                    "eta": data.get("eta") or 0,
                })
            elif status == "finished":
                progress_cb({
                    "stage": "download",
                    "percent": 100.0,
                    "downloaded": data.get("total_bytes") or 0,
                    "total": data.get("total_bytes") or 0,
                    "speed": 0,
                    "eta": 0,
                })

        opts = self._base_opts()
        opts.update({
            "skip_download": False,
            "noplaylist": track.source_index is None,
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "progress_hooks": [hook],
            "writeinfojson": False,
            "writethumbnail": False,
        })
        if track.source_index:
            opts["playlist_items"] = str(track.source_index)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(track.url, download=True)
                if not info:
                    raise AppError("The download returned no data.",
                                   "Try again.", kind="download")

                item_info = info
                if info.get("_type") in ("playlist", "multi_video"):
                    entries = [e for e in (info.get("entries") or []) if e]
                    if entries:
                        item_info = entries[0]

                path = self._resolve_file(ydl, item_info, raw_dir, prefix)
        except Cancelled:
            raise
        except AppError:
            raise
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            raise classify_ytdlp_error(exc) from exc

        if path is None or not path.exists():
            raise AppError(
                "The download finished but the file could not be found.",
                "Check that the output folder is writable, then retry.",
                kind="download",
            )
        if path.stat().st_size <= 0:
            path.unlink(missing_ok=True)
            raise AppError("The downloaded file is empty.",
                           "Try again.", kind="download")
        return path

    @staticmethod
    def _resolve_file(ydl, info: dict, raw_dir: Path, prefix: str) -> Optional[Path]:
        for download in info.get("requested_downloads") or []:
            candidate = download.get("filepath") or download.get("_filename")
            if candidate and Path(candidate).exists():
                return Path(candidate)

        try:
            prepared = ydl.prepare_filename(info)
        except Exception:
            prepared = ""
        if prepared and Path(prepared).exists():
            return Path(prepared)

        candidates = [
            path for path in raw_dir.iterdir()
            if path.is_file()
            and path.name.startswith(prefix)
            and not path.name.endswith(".part")
        ]
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)
        return None

    # ------------------------------------------------------------ diagnostics

    def version(self) -> str:
        try:
            return yt_dlp.version.__version__
        except Exception:
            return "unknown"
