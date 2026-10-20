"""Cover art download, square crop, and disk cache."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, Optional

import requests
from PIL import Image, ImageOps

from musicdlp.services.filenames import safe_stem

logger = logging.getLogger("musicdlp.thumbnails")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)


def youtube_fallbacks(media_id: str) -> list[str]:
    return [
        f"https://i.ytimg.com/vi/{media_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{media_id}/sddefault.jpg",
        f"https://i.ytimg.com/vi/{media_id}/hqdefault.jpg",
    ]


class ThumbnailService:
    def __init__(self, cache_dir: Path, size: int = 640) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.size = size

    def cached_path(self, platform: str, media_id: str) -> Path:
        stem = safe_stem(f"{platform}_{media_id}", max_length=80)
        return self.cache_dir / f"{stem}.jpg"

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
                return self._save_image(response.content, cached)
            except Exception as exc:
                logger.debug("Cover art failed (%s): %s", candidate, exc)

        logger.info("No cover art available for %s:%s", platform, media_id)
        return None

    def _save_image(self, data: bytes, destination: Path) -> Path:
        from io import BytesIO

        image = Image.open(BytesIO(data))
        if image.mode != "RGB":
            image = image.convert("RGB")

        image = ImageOps.fit(
            image,
            (self.size, self.size),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

        tmp = destination.with_suffix(".tmp.jpg")
        image.save(tmp, "JPEG", quality=92, optimize=True)
        os.replace(tmp, destination)
        return destination
