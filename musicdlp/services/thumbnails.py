"""Cover art download, square crop, and disk cache."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, Optional

import requests
from PIL import Image, ImageFilter, ImageOps

from musicdlp.services.filenames import safe_stem

logger = logging.getLogger("musicdlp.thumbnails")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)


# YouTube's largest still. maxresdefault is 1280x720 and is missing for
# some older uploads, where the next size down is served instead.
YOUTUBE_SIZES = ("maxresdefault", "sddefault", "hqdefault")

# Anything smaller than this is YouTube's grey "no thumbnail" placeholder.
MIN_USABLE_PIXELS = 300

# Spotify will not use art below this size.
MIN_STORED_SIZE = 640

# Covers stored at 640 px by an earlier version are not reused.
CACHE_VERSION = 2


def youtube_fallbacks(media_id: str) -> list[str]:
    return [
        f"https://i.ytimg.com/vi/{media_id}/{size}.jpg"
        for size in YOUTUBE_SIZES
    ]


class ThumbnailService:
    def __init__(
        self, cache_dir: Path, size: int = 1400, *, upscale: bool = True
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.size = size
        # A video still is the only picture an unreleased song has, and it
        # is smaller than a cover should be. Enlarging it with a good
        # filter looks better on a phone and a laptop than letting the
        # player do a plain stretch.
        self.upscale = upscale

    def cached_path(self, platform: str, media_id: str) -> Path:
        stem = safe_stem(f"{platform}_{media_id}", max_length=80)
        return self.cache_dir / f"{stem}_v{CACHE_VERSION}.jpg"

    def fetch(
        self,
        platform: str,
        media_id: str,
        url: Optional[str],
        fallbacks: Optional[Iterable[str]] = None,
    ) -> Optional[Path]:
        """Download and cache square cover art. Returns None on failure."""
        cached = self.cached_path(platform, media_id)
        if cached.exists() and cached.stat().st_size > 0:
            return cached

        candidates: list[str] = []
        for candidate in [url, *(fallbacks or [])]:
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        for candidate in candidates:
            try:
                response = requests.get(
                    candidate,
                    timeout=15,
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
                saved = self._save_image(response.content, cached)
                if saved is not None:
                    return saved
                # A placeholder; try the next size down instead.
            except Exception as exc:
                logger.debug("Cover art failed (%s): %s", candidate, exc)

        logger.info("No cover art available for %s:%s", platform, media_id)
        return None

    def _save_image(self, data: bytes, destination: Path) -> Optional[Path]:
        from io import BytesIO

        image = Image.open(BytesIO(data))
        if image.mode != "RGB":
            image = image.convert("RGB")

        # YouTube answers a missing size with a 120x90 grey square. Saving
        # that as a cover is worse than having none, so it is rejected and
        # the next size down is tried.
        edge = min(image.size)
        if edge < MIN_USABLE_PIXELS:
            logger.debug("Ignoring a %s px thumbnail", image.size)
            return None

        target = min(self.size, max(MIN_STORED_SIZE, edge))
        if self.upscale and edge < self.size:
            # Go all the way to the cover size, not just to the minimum.
            target = self.size
        enlarged = target > edge
        image = ImageOps.fit(
            image,
            (target, target),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        if enlarged:
            # Lanczos alone comes out soft. A light unsharp pass restores
            # some edge definition without the halos a heavy one adds.
            image = image.filter(
                ImageFilter.UnsharpMask(radius=1.4, percent=70, threshold=3)
            )

        tmp = destination.with_suffix(".tmp.jpg")
        image.save(tmp, "JPEG", quality=92, optimize=True)
        os.replace(tmp, destination)
        return destination
