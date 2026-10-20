"""Link classification and normalization.

The extractor decides what a link really is (yt-dlp reports the type), but
classification gives the UI a useful hint and produces canonical source URLs
so adding the same playlist twice does not create duplicate library entries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

YOUTUBE_HOSTS = {
    "youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com",
}
TWITTER_HOSTS = {
    "twitter.com", "mobile.twitter.com", "m.twitter.com",
    "x.com", "mobile.x.com", "m.x.com",
}
TWITTER_PROXIES = {"vxtwitter.com", "fxtwitter.com", "fixupx.com"}

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TWITTER_STATUS_RE = re.compile(r"/status(?:es)?/(\d+)")
TWITTER_USER_RE = re.compile(r"^/([A-Za-z0-9_]{1,30})/status(?:es)?/(\d+)")

PLATFORM_YOUTUBE = "youtube"
PLATFORM_TWITTER = "twitter"
PLATFORM_OTHER = "other"

KIND_PLAYLIST = "playlist"
KIND_ITEM = "item"


@dataclass(frozen=True)
class LinkInfo:
    url: str
    platform: str
    kind: str
    media_id: Optional[str] = None


def normalize_url(url: str) -> str:
    """Trim wrapping punctuation and add a scheme when missing."""
    url = (url or "").strip().strip("<>()[]\"'")
    url = url.rstrip(".,;")
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    return url


def _host(parsed) -> str:
    host = (parsed.hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _is_host(host: str, candidates: set[str]) -> bool:
    return host in candidates or any(host.endswith("." + c) for c in candidates)


def _youtube(parsed, normalized: str) -> Optional[LinkInfo]:
    host = _host(parsed)
    path = parsed.path or "/"
    query = parse_qs(parsed.query)

    if host == "youtu.be":
        match = re.match(r"^/([A-Za-z0-9_-]{1,64})", path)
        if match:
            video_id = match.group(1)
            return LinkInfo(
                f"https://www.youtube.com/watch?v={video_id}",
                PLATFORM_YOUTUBE, KIND_ITEM, video_id,
            )
        return None

    if path.rstrip("/") == "/playlist":
        playlist_id = (query.get("list") or [None])[0]
        if playlist_id:
            return LinkInfo(
                f"https://www.youtube.com/playlist?list={playlist_id}",
                PLATFORM_YOUTUBE, KIND_PLAYLIST, playlist_id,
            )
        return None

    if path.rstrip("/") == "/watch":
        video_id = (query.get("v") or [None])[0]
        if video_id and YOUTUBE_ID_RE.match(video_id):
            return LinkInfo(
                f"https://www.youtube.com/watch?v={video_id}",
                PLATFORM_YOUTUBE, KIND_ITEM, video_id,
            )
        playlist_id = (query.get("list") or [None])[0]
        if playlist_id:
            return LinkInfo(
                f"https://www.youtube.com/playlist?list={playlist_id}",
                PLATFORM_YOUTUBE, KIND_PLAYLIST, playlist_id,
            )
        return None

    match = re.match(r"^/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{1,64})", path)
    if match:
        video_id = match.group(1)
        return LinkInfo(
            f"https://www.youtube.com/watch?v={video_id}",
            PLATFORM_YOUTUBE, KIND_ITEM, video_id,
        )

    # Channel, user, and other YouTube pages are left to yt-dlp.
    return LinkInfo(normalized, PLATFORM_YOUTUBE, KIND_PLAYLIST, None)


def _twitter(parsed, normalized: str, proxy: bool) -> Optional[LinkInfo]:
    path = parsed.path or "/"
    match = TWITTER_STATUS_RE.search(path)
    if not match:
        return None

    status_id = match.group(1)
    user_match = TWITTER_USER_RE.match(path)
    if user_match:
        canonical = f"https://x.com/{user_match.group(1)}/status/{status_id}"
    else:
        canonical = f"https://x.com/i/status/{status_id}"
    return LinkInfo(canonical, PLATFORM_TWITTER, KIND_ITEM, status_id)


def classify_url(url: str) -> Optional[LinkInfo]:
    """Classify a link, or return None when it is not usable."""
    normalized = normalize_url(url)
    if not normalized:
        return None

    parsed = urlparse(normalized)
    if parsed.scheme not in ("http", "https"):
        return None

    host = _host(parsed)
    if not host or " " in host or "." not in host:
        return None

    if _is_host(host, YOUTUBE_HOSTS) or host == "youtu.be":
        return _youtube(parsed, normalized)

    if _is_host(host, TWITTER_HOSTS):
        return _twitter(parsed, normalized, proxy=False)

    if _is_host(host, TWITTER_PROXIES):
        return _twitter(parsed, normalized, proxy=True)

    # Anything else is handed to yt-dlp, which supports many sites.
    return LinkInfo(normalized, PLATFORM_OTHER, KIND_ITEM, None)


def split_links(text: str) -> List[str]:
    """Split pasted text into unique normalized links, preserving order."""
    if not text:
        return []

    parts = re.split(r"[\s,]+", text.strip())
    links: List[str] = []
    seen: set[str] = set()

    for part in parts:
        part = part.strip().strip("<>()[]\"'")
        if not part:
            continue
        if not re.match(r"^(https?://|www\.)", part, re.IGNORECASE):
            if not re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}/", part):
                continue
        normalized = normalize_url(part)
        if not normalized or normalized in seen:
            continue
        if classify_url(normalized) is None:
            continue
        seen.add(normalized)
        links.append(normalized)

    return links


def platform_key(extractor_key: Optional[str], fallback: str = PLATFORM_OTHER) -> str:
    """Normalize a yt-dlp extractor key for use as a media identity."""
    if not extractor_key:
        return fallback
    key = extractor_key.strip().lower()
    if key.startswith("youtube"):
        return PLATFORM_YOUTUBE
    if key.startswith("twitter"):
        return PLATFORM_TWITTER
    return key or fallback


def media_key(platform: str, media_id: str) -> str:
    return f"{platform}:{media_id}"
