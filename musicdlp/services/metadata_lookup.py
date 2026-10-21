"""Look up album, artist, and track number in free music catalogues.

This is the non-AI half of metadata fixing: MusicBrainz knows the release a
recording belongs to and where it sits on that release, so a downloaded
single can be given its album and track number without asking a model.

The service is best-effort. A lookup that finds nothing returns None and the
song keeps whatever it already had.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

import requests

logger = logging.getLogger("musicdlp.lookup")

USER_AGENT = (
    "MusicDLP/2.6 (personal music library tool) "
    "https://github.com/seafeuer/Music-DLP"
)
SEARCH_URL = "https://musicbrainz.org/ws/2/recording"

# MusicBrainz asks for at most one request per second from a client. Keep a
# shared clock so a batch of lookups stays polite.
_MIN_INTERVAL = 1.1
_lock = threading.Lock()
_last_call = 0.0


@dataclass
class Lookup:
    """What the catalogue says about one song."""

    artist: str = ""
    album: str = ""
    track: Optional[int] = None
    year: str = ""
    score: int = 0
    release_id: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.album or self.artist or self.track)


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _wait_for_turn() -> None:
    global _last_call
    with _lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL - (now - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _search(artist: str, title: str) -> list[dict]:
    query = [f'recording:"{title}"']
    if artist:
        query.append(f'artist:"{artist}"')
    params = {
        "query": " AND ".join(query),
        "fmt": "json",
        "limit": 5,
    }
    _wait_for_turn()
    try:
        response = requests.get(
            SEARCH_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
    except requests.exceptions.RequestException as exc:
        logger.debug("MusicBrainz lookup failed: %s", exc)
        return []
    if response.status_code >= 400:
        return []
    try:
        data = response.json()
    except ValueError:
        return []
    return list(data.get("recordings") or [])


def _artist_of(recording: dict) -> str:
    credits = recording.get("artist-credit") or []
    names = []
    for credit in credits:
        if isinstance(credit, dict):
            name = credit.get("name") or (credit.get("artist") or {}).get("name")
            if name:
                names.append(str(name))
    return ", ".join(names)


def _release_candidates(recording: dict) -> list[dict]:
    releases = recording.get("releases") or []
    return [release for release in releases if isinstance(release, dict)]


def _pick_release(releases: list[dict], album: str) -> Optional[dict]:
    """Choose the release most likely to be the real one."""
    if not releases:
        return None
    wanted = _clean(album).lower()
    if wanted:
        for release in releases:
            if _clean(release.get("title", "")).lower() == wanted:
                return release
    # Otherwise prefer a release that says where the track sits, and among
    # those the earliest one, which is usually the original.
    def rank(release: dict) -> tuple:
        media = release.get("media") or []
        has_track = any(entry.get("track-offset") is not None
                        for entry in media if isinstance(entry, dict))
        return (0 if has_track else 1, str(release.get("date") or "9999"))

    return sorted(releases, key=rank)[0]


def _track_number(release: dict) -> Optional[int]:
    for medium in release.get("media") or []:
        if not isinstance(medium, dict):
            continue
        offset = medium.get("track-offset")
        if offset is None:
            continue
        try:
            return int(offset) + 1
        except (TypeError, ValueError):
            continue
    return None


def lookup(artist: str, title: str, *, album: str = "") -> Optional[Lookup]:
    """Find the release for a song. Returns None when nothing matches."""
    artist = _clean(artist)
    title = _clean(title)
    if not title:
        return None

    recordings = _search(artist, title)
    if not recordings:
        return None

    wanted_title = title.lower()
    for recording in recordings:
        found_title = _clean(recording.get("title", "")).lower()
        if wanted_title and wanted_title not in found_title and \
                found_title not in wanted_title:
            continue

        release = _pick_release(_release_candidates(recording), album)
        if release is None:
            continue

        try:
            score = int(recording.get("score") or 0)
        except (TypeError, ValueError):
            score = 0

        date = str(release.get("date") or "")
        result = Lookup(
            artist=_artist_of(recording) or artist,
            album=_clean(release.get("title", "")),
            track=_track_number(release),
            year=date[:4] if len(date) >= 4 else "",
            score=score,
            release_id=str(release.get("id") or ""),
        )
        if result.usable:
            logger.info("Found details for %s — %s", artist or "?", title)
            return result

    logger.info("No details found for %s — %s", artist or "?", title)
    return None
