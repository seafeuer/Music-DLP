"""Look up real album artwork instead of using a video still.

Two free services are used, neither needing an API key:

* the iTunes Search API, which returns a square cover at a usable size, and
* MusicBrainz plus the Cover Art Archive, as a fallback.

Both are best-effort: when nothing is found the caller keeps the video
thumbnail it already had.
"""

from __future__ import annotations

import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import requests
from PIL import Image, ImageOps

logger = logging.getLogger("musicdlp.artwork")

USER_AGENT = (
    "MusicDLP/2.5 (personal music library tool) "
    "https://github.com/seafeuer/Music-DLP"
)
ITUNES_URL = "https://itunes.apple.com/search"
MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2/recording"
COVER_ARCHIVE_URL = "https://coverartarchive.org/release/{mbid}/front-1200"

# iTunes serves artwork at any size the publisher uploaded, up to 3000 px
# square. The search result only advertises a 100 px thumbnail, so the URL
# has to be rewritten to ask for the large one.
ITUNES_SIZES = ("3000x3000bb", "1400x1400bb", "600x600bb")

# What a cover is stored at. Spotify asks for at least 640 px and prefers
# 3000; 1400 looks sharp on a phone and a laptop without making every file
# a megabyte of artwork. Art smaller than this is never stretched up.
TARGET_COVER_SIZE = 1400

# Anything below this is YouTube's "no thumbnail" placeholder, not a picture.
MIN_USABLE_PIXELS = 300

# Spotify will not use art below this size. A smaller picture is enlarged
# to it so the player never has to show something tinier than it should.
MIN_STORED_SIZE = 640


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _download(url: str, timeout: int = 15) -> Optional[bytes]:
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=timeout
        )
    except requests.exceptions.RequestException as exc:
        logger.debug("Artwork download failed (%s): %s", url, exc)
        return None
    if response.status_code >= 400 or not response.content:
        return None
    return response.content


def _pixel_size(data: bytes) -> tuple[int, int]:
    """The image's own size, or (0, 0) when it is not a readable picture."""
    try:
        with Image.open(BytesIO(data)) as image:
            return image.size
    except Exception:  # noqa: BLE001 - a broken download is just a miss
        return (0, 0)


def _save_square(
    data: bytes, destination: Path, size: int = TARGET_COVER_SIZE
) -> Optional[Path]:
    """Crop to a square and save it, never enlarging a small picture.

    Upscaling a 200 px image to 1400 px only makes a blurry file bigger, so
    the stored size is the source's own square edge when that is smaller
    than the target.
    """
    try:
        image = Image.open(BytesIO(data))
        if image.mode != "RGB":
            image = image.convert("RGB")
        edge = min(image.size)
        if edge < MIN_USABLE_PIXELS:
            logger.debug("Ignoring a %s px image as a cover", image.size)
            return None
        target = min(size, max(MIN_STORED_SIZE, edge))
        image = ImageOps.fit(
            image, (target, target), method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(".tmp.jpg")
        image.save(tmp, "JPEG", quality=92, optimize=True)
        os.replace(tmp, destination)
        return destination
    except Exception as exc:
        logger.debug("Could not process artwork: %s", exc)
        return None


# ------------------------------------------------------------------ iTunes


def _itunes_cover(artist: str, title: str) -> Optional[str]:
    term = _clean(f"{artist} {title}") if artist else _clean(title)
    if not term:
        return None
    params = {"term": term, "entity": "song", "limit": 8}
    try:
        response = requests.get(
            ITUNES_URL, params=params,
            headers={"User-Agent": USER_AGENT}, timeout=15,
        )
        if response.status_code >= 400:
            return None
        results = response.json().get("results") or []
    except (requests.exceptions.RequestException, ValueError) as exc:
        logger.debug("iTunes lookup failed: %s", exc)
        return None

    wanted_title = _clean(title).lower()
    wanted_artist = _clean(artist).lower()

    for result in results:
        track = _clean(result.get("trackName", "")).lower()
        performer = _clean(result.get("artistName", "")).lower()
        # Prefer a result whose title matches; ignore obvious misses.
        if wanted_title and wanted_title not in track and track not in wanted_title:
            continue
        if wanted_artist and wanted_artist not in performer and performer not in wanted_artist:
            continue
        artwork = _large_artwork(result.get("artworkUrl100"))
        if artwork:
            return artwork

    # Nothing matched by title and artist; take the first cover with art.
    for result in results:
        artwork = _large_artwork(result.get("artworkUrl100"))
        if artwork:
            return artwork
    return None


def _large_artwork(url: str) -> str:
    """Ask iTunes for the biggest version of a cover it has."""
    url = (url or "").strip()
    if not url:
        return ""
    for size in ITUNES_SIZES:
        if f"/{size}." in url or url.endswith(f"/{size}"):
            return url.replace(f"/{size}.", f"/{ITUNES_SIZES[0]}.")
    # The usual form is ".../100x100bb.jpg".
    for old in ("100x100bb", "100x100", "60x60bb", "60x60"):
        if old in url:
            return url.replace(old, ITUNES_SIZES[0])
    return url


# --------------------------------------------------------- MusicBrainz


def _musicbrainz_cover(artist: str, title: str) -> Optional[str]:
    if not title:
        return None
    query_parts = [f'recording:"{title}"']
    if artist:
        query_parts.append(f'artist:"{artist}"')
    params = {"query": " AND ".join(query_parts), "fmt": "json", "limit": 5}
    try:
        response = requests.get(
            MUSICBRAINZ_URL, params=params,
            headers={"User-Agent": USER_AGENT}, timeout=15,
        )
        if response.status_code >= 400:
            return None
        data = response.json()
    except (requests.exceptions.RequestException, ValueError):
        return None

    for recording in data.get("recordings") or []:
        releases = recording.get("releases") or []
        for release in releases:
            release_id = release.get("id")
            if release_id:
                return COVER_ARCHIVE_URL.format(mbid=release_id)
    return None


# ---------------------------------------------------------------- public


def cover_cache_path(platform: str, media_id: str) -> Path:
    """Where a song's cover is cached.

    The size is part of the name, so a cover stored at the old 640 px is
    not reused after an upgrade, and the bigger one is fetched once.
    """
    from musicdlp.core.paths import art_cache_dir

    return art_cache_dir() / (
        f"album_{platform}_{media_id}_{TARGET_COVER_SIZE}.jpg"
    )


def find_cover(
    artist: str,
    title: str,
    destination: Path,
    *,
    album: str = "",
    refresh: bool = False,
) -> Optional[Path]:
    """Find and save square cover art. Returns None when nothing is found.

    ``refresh`` ignores an already-cached image, for when the song's details
    have changed since the last lookup.
    """
    destination = Path(destination)
    if not refresh and destination.exists() and destination.stat().st_size > 0:
        return destination

    artist = _clean(artist)
    title = _clean(title)
    album = _clean(album)
    if not title:
        return None

    candidates: list[str] = []
    for lookup in (
        lambda: _itunes_cover(artist, title),
        lambda: _musicbrainz_cover(artist, title),
        lambda: _itunes_cover(artist, album) if album else None,
    ):
        try:
            url = lookup()
        except Exception as exc:
            logger.debug("Cover lookup failed: %s", exc)
            continue
        if url and url not in candidates:
            candidates.append(url)

    for url in candidates:
        data = _download(url)
        if not data:
            continue
        saved = _save_square(data, destination)
        if saved:
            logger.info("Found cover art for %s — %s", artist or "?", title)
            return saved

    logger.info("No cover art found for %s — %s", artist or "?", title)
    return None
