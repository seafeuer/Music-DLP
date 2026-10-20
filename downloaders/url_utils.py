"""
URL classification helpers.

Music DLP accepts three kinds of links:

- YouTube playlists  (youtube.com/playlist?list=...)
- Single media items (YouTube videos, X/Twitter posts, and anything else
  yt-dlp supports)
- Bare domains are accepted too, e.g. "youtu.be/abc123" or
  "x.com/user/status/123".

The rest of the app only needs to know the platform and whether the link is
a playlist or a single item; yt-dlp does the actual resolution.
"""

from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import parse_qs, urlparse
import re


# Hosts are compared without a leading "www."
YOUTUBE_HOSTS = {
    'youtube.com',
    'm.youtube.com',
    'music.youtube.com',
    'youtube-nocookie.com',
}

TWITTER_HOSTS = {
    'twitter.com',
    'mobile.twitter.com',
    'm.twitter.com',
    'x.com',
    'mobile.x.com',
    'm.x.com',
}

# Common X/Twitter proxy domains that serve the same /user/status/<id> paths.
TWITTER_PROXY_HOSTS = {
    'vxtwitter.com',
    'fxtwitter.com',
    'fixupx.com',
}

_YOUTUBE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
_TWITTER_STATUS_RE = re.compile(r'/status(?:es)?/(\d+)')


@dataclass(frozen=True)
class LinkInfo:
    """A parsed, normalized link."""

    url: str
    platform: str      # 'youtube', 'twitter' or 'other'
    kind: str          # 'playlist' or 'video'
    media_id: Optional[str] = None


def normalize_url(url: str) -> str:
    """Strip wrapping punctuation and add a scheme when it is missing."""
    url = (url or '').strip().strip('<>()[]"\'')
    # Trailing punctuation is almost always sentence punctuation, not URL.
    url = url.rstrip('.,;')

    if not url:
        return ''

    if url.startswith('//'):
        url = 'https:' + url

    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', url):
        url = 'https://' + url

    return url


def _host(parsed) -> str:
    host = (parsed.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    return host


def _is_host(host: str, candidates: set) -> bool:
    return host in candidates or any(host.endswith('.' + c) for c in candidates)


def classify_url(url: str) -> Optional[LinkInfo]:
    """Classify a link.

    Returns:
        LinkInfo, or None when the string is not a usable http(s) link.
        Unsupported hosts are returned as platform 'other' kind 'video' so
        yt-dlp can still try them; a clear error comes back if it cannot.
    """
    normalized = normalize_url(url)
    if not normalized:
        return None

    parsed = urlparse(normalized)
    if parsed.scheme not in ('http', 'https'):
        return None

    host = _host(parsed)
    path = parsed.path or '/'
    query = parse_qs(parsed.query)

    # Reject strings that only look like URLs (spaces, no dotted host).
    if not host or ' ' in host or '.' not in host:
        return None

    if host == 'youtu.be':
        match = re.match(r'^/([A-Za-z0-9_-]{11})', path)
        if match:
            return LinkInfo(normalized, 'youtube', 'video', match.group(1))
        return None

    if _is_host(host, YOUTUBE_HOSTS):
        # A /playlist link is a playlist.
        if path.rstrip('/') == '/playlist':
            playlist_id = (query.get('list') or [None])[0]
            if playlist_id:
                return LinkInfo(normalized, 'youtube', 'playlist', playlist_id)
            return None

        # /watch?v=... is a single video, even when it carries &list=.
        # (A shared video should download the video, not the whole playlist.)
        if path.rstrip('/') == '/watch':
            video_id = (query.get('v') or [None])[0]
            if video_id and _YOUTUBE_ID_RE.match(video_id):
                return LinkInfo(normalized, 'youtube', 'video', video_id)
            playlist_id = (query.get('list') or [None])[0]
            if playlist_id:
                return LinkInfo(normalized, 'youtube', 'playlist', playlist_id)
            return None

        match = re.match(r'^/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{11})', path)
        if match:
            return LinkInfo(normalized, 'youtube', 'video', match.group(1))

        return None

    if _is_host(host, TWITTER_PROXY_HOSTS):
        match = _TWITTER_STATUS_RE.search(path)
        if match:
            canonical = f"https://x.com{path}"
            if parsed.query:
                canonical += f"?{parsed.query}"
            return LinkInfo(canonical, 'twitter', 'video', match.group(1))
        return None

    if _is_host(host, TWITTER_HOSTS):
        match = _TWITTER_STATUS_RE.search(path)
        if match:
            return LinkInfo(normalized, 'twitter', 'video', match.group(1))
        return None

    # Everything else is handed to yt-dlp as a single item. It supports
    # hundreds of sites, so this is more useful than rejecting the link.
    return LinkInfo(normalized, 'other', 'video', None)


def is_supported_url(url: str) -> bool:
    """Return True when the link can be handed to yt-dlp."""
    return classify_url(url) is not None


def split_links(text: str) -> List[str]:
    """Split pasted text into unique, normalized links.

    Accepts links separated by whitespace, newlines, or commas, and ignores
    any non-link text. Order is preserved.
    """
    if not text:
        return []

    parts = re.split(r'[\s,]+', text.strip())
    links: List[str] = []
    seen = set()

    for part in parts:
        part = part.strip().strip('<>()[]"\'')
        if not part:
            continue

        # Require either a scheme or something that looks like a hostname.
        if not re.match(r'^(https?://|www\.)', part, re.IGNORECASE):
            if not re.match(r'^[A-Za-z0-9.-]+\.[A-Za-z]{2,}/', part):
                continue

        normalized = normalize_url(part)
        if not normalized or normalized in seen:
            continue
        if classify_url(normalized) is None:
            continue

        seen.add(normalized)
        links.append(normalized)

    return links


def single_collection_id(platform: str, media_id: str) -> str:
    """Build a stable database id for a one-item collection."""
    safe_platform = re.sub(r'[^A-Za-z0-9_-]', '_', platform or 'other')
    safe_id = re.sub(r'[^A-Za-z0-9_-]', '_', media_id or 'item')
    return f"single_{safe_platform}_{safe_id}"
