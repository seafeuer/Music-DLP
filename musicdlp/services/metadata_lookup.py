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


def _search(artist: str, title: str, *, official: bool = False) -> list[dict]:
    query = [f'recording:"{title}"']
    if artist:
        query.append(f'artist:"{artist}"')
    if official:
        # Without this, the top hits are often bootleg live recordings,
        # because those are what the index happens to rank highest.
        query.append("status:official")
    params = {
        "query": " AND ".join(query),
        "fmt": "json",
        "limit": 10,
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
    return sorted(releases, key=_release_rank)[0]


# The kind of release a song most likely belongs to, best first. A
# greatest-hits collection or a live bootleg is a last resort: it is a real
# release, but it is not the album the song came from.
_TYPE_RANK = {
    "album": 0,
    "ep": 1,
    "single": 2,
    "broadcast": 3,
    "other": 4,
    "compilation": 5,
    "live": 6,
    "soundtrack": 7,
    "dj-mix": 8,
    "mixtape/street": 9,
    "audio drama": 10,
    "audiobook": 11,
    "spokenword": 12,
    "interview": 13,
}

_STATUS_RANK = {"official": 0, "promotion": 1, "bootleg": 3, "pseudo-release": 3}


def _release_rank(release: dict) -> tuple:
    """Sort key for releases: the most album-like, official, early one wins."""
    group = release.get("release-group") or {}
    status = str(release.get("status") or "").strip().lower()
    primary = str(group.get("primary-type") or "").strip().lower()
    secondary = {
        str(kind).strip().lower() for kind in (group.get("secondary-types") or [])
    }
    is_live = 1 if ("live" in secondary or primary == "live") else 0

    # A compilation is filed as an "Album" in MusicBrainz; the secondary
    # type is what says it is a greatest-hits or a various-artists disc.
    kind = primary
    for marker in ("compilation", "dj-mix", "soundtrack", "mixtape/street"):
        if marker in secondary:
            kind = marker
            break

    media = release.get("media") or []
    has_track = any(
        entry.get("track-offset") is not None
        for entry in media if isinstance(entry, dict)
    )
    return (
        is_live,
        _STATUS_RANK.get(status, 2),
        _TYPE_RANK.get(kind, 4),
        _date_rank(release.get("date")),
        0 if has_track else 1,
    )


def _date_rank(date: object) -> tuple:
    """Sort key for a partial date, so 2019-03-29 sorts before a bare 2019.

    A release with no date at all is a last resort, not the earliest one.
    """
    text = str(date or "").strip()
    if not text:
        return (9999, 99, 99)
    parts = text.split("-")
    numbers = []
    for part in parts[:3]:
        try:
            numbers.append(int(part[:4] if len(part) > 4 else part))
        except ValueError:
            numbers.append(99)
    while len(numbers) < 3:
        numbers.append(99)
    return tuple(numbers)


def _recording_rank(recording: dict) -> tuple:
    """Sort key for recordings: a studio take beats a live or remix one."""
    note = " ".join(str(recording.get("disambiguation") or "").lower().split())
    live = 1 if any(
        word in note for word in ("live", "concert", "session", "bootleg")
    ) else 0
    try:
        score = -int(recording.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    return (live, score)


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

    best = _lookup_once(artist, title, album, official=False)
    # A bootleg or a live recording is a real match, but a studio album is
    # the better answer. Ask for official releases only and keep whichever
    # ranks better.
    if best is None or not _is_good_match(best[0]):
        official = _lookup_once(artist, title, album, official=True)
        if official is not None and (best is None or official[0] < best[0]):
            best = official

    if best is None:
        logger.info("No details found for %s — %s", artist or "?", title)
        return None

    result = best[1]
    logger.info(
        "Found details for %s — %s: %s", artist or "?", title, result.album
    )
    return result


def _lookup_once(
    artist: str, title: str, album: str, *, official: bool
) -> Optional[tuple[tuple, Lookup]]:
    """The best (rank, result) from one search, or None."""
    recordings = _search(artist, title, official=official)
    if not recordings:
        return None

    wanted_title = title.lower()
    best: Optional[tuple[tuple, Lookup]] = None
    for recording in sorted(recordings, key=_recording_rank):
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

        credited = _artist_of(recording) or artist
        date = str(release.get("date") or "")
        result = Lookup(
            artist=credited,
            album=_clean(release.get("title", "")),
            track=_track_number(release),
            year=date[:4] if len(date) >= 4 else "",
            score=score,
            release_id=str(release.get("id") or ""),
        )
        if not result.usable:
            continue

        # A release the user already named always wins. After that, a song
        # credited to a different artist is the wrong song: "At the Gates"
        # by a film composer should never beat Drake's own upload.
        exact = bool(album) and \
            _clean(result.album).lower() == _clean(album).lower()
        rank: tuple = (
            (0,) if exact
            else (
                1,
                _artist_rank(artist, credited),
                _release_rank(release),
                _recording_rank(recording),
            )
        )
        if best is None or rank < best[0]:
            best = (rank, result)

    return best


def _is_good_match(rank: tuple) -> bool:
    """Whether a candidate is the studio album, not a bootleg or a live set."""
    if rank[0] == 0:
        return True  # the album the user already named
    if len(rank) < 3:
        return False
    artist_rank, release_rank = rank[1], rank[2]
    is_live, status_rank, type_rank = release_rank[0], release_rank[1], release_rank[2]
    return (
        artist_rank == 0
        and is_live == 0
        and status_rank == 0
        and type_rank <= _TYPE_RANK["broadcast"]
    )


def _artist_rank(requested: str, credited: str) -> int:
    """0 when the catalogue credits the artist that was asked for."""
    wanted = _artist_tokens(requested)
    found = _artist_tokens(credited)
    if not wanted or not found:
        return 1
    # "Drake" against "Drake, Lil Uzi Vert" is the same artist: the name
    # asked for opens the credit. "Drake" against "Christopher Drake" is
    # not, so a film composer cannot win on a shared surname.
    if found[:len(wanted)] == wanted:
        return 0
    return 1


def _artist_tokens(text: str) -> list[str]:
    """The words of an artist credit, with accents and separators removed."""
    import unicodedata

    cleaned = unicodedata.normalize("NFKD", (text or "").lower())
    cleaned = "".join(
        char for char in cleaned if not unicodedata.combining(char)
    )
    for separator in (",", "&", "feat.", "featuring", "ft.", "with", "/"):
        cleaned = cleaned.replace(separator, " ")
    return [
        word for word in "".join(
            char if char.isalnum() else " " for char in cleaned
        ).split() if word
    ]
