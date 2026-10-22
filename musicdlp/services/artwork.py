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
    "MusicDLP/2.8 (personal music library tool) "
    "https://github.com/seafeuer/Music-DLP"
)
ITUNES_URL = "https://itunes.apple.com/search"
MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2/recording"
DEEZER_URL = "https://api.deezer.com/search"
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


def _artist_parts(artist: str) -> list[str]:
    """The separate names in an artist credit, main artist first."""
    text = _clean(artist)
    for separator in (" feat. ", " featuring ", " & ", ",", " / ", " x "):
        text = text.replace(separator, "|")
    parts = [_clean(part) for part in text.split("|")]
    return [part for part in parts if part]


def _norm(text: str) -> str:
    """A comparison form: lower case, no accents, no punctuation."""
    import unicodedata

    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    cleaned = "".join(
        char if char.isalnum() else " " for char in text
    )
    return " ".join(cleaned.split())


# Qualifiers that name a different recording of a song. A cover of "Monday
# Morning" is not the upload being tagged, so its artwork must not be used.
_DIFFERENT_RECORDING = (
    "live", "cover", "karaoke", "instrumental", "remix", "tribute",
    "backing track", "sped up", "nightcore", "acoustic", "reprise",
)


def _qualifiers(text: str) -> set[str]:
    """The words inside brackets, which say how a version differs."""
    import re

    found = re.findall(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]", text or "")
    return {_norm(item) for item in found if item.strip()}


def _core_title(text: str) -> str:
    """The title with its bracketed qualifiers removed."""
    import re

    return _norm(re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", text or ""))


# How far a catalogue track's length may be from the uploaded audio and
# still be the same recording. An official music video can run a minute
# longer than the song it contains, so this is set to catch a completely
# different song rather than a video intro. Among several candidates the
# closest length wins, which is what actually picks the right version.
DURATION_TOLERANCE_SECONDS = 60
DURATION_TOLERANCE_RATIO = 0.30


def _duration_gap(wanted: Optional[float], found: Optional[float]) -> float:
    """The relative difference in length, or 0 when either is unknown."""
    try:
        wanted = float(wanted or 0)
        found = float(found or 0)
    except (TypeError, ValueError):
        return 0.0
    if wanted <= 0 or found <= 0:
        return 0.0
    return abs(wanted - found) / max(wanted, found)


def _duration_matches(wanted: Optional[float], found: Optional[float]) -> bool:
    """Whether two track lengths could be one recording.

    This is the one check that does not depend on how a title was written
    or parsed, which is exactly what goes wrong when a name is guessed.
    """
    try:
        wanted = float(wanted or 0)
        found = float(found or 0)
    except (TypeError, ValueError):
        return True
    if wanted <= 0 or found <= 0:
        # Nothing to compare against; do not reject on a missing length.
        return True
    difference = abs(wanted - found)
    allowance = max(
        DURATION_TOLERANCE_SECONDS, max(wanted, found) * DURATION_TOLERANCE_RATIO
    )
    return difference <= allowance


def _is_the_same_song(
    wanted_title: str,
    wanted_parts: list[str],
    result_title: str,
    result_artist: str,
    *,
    wanted_seconds: Optional[float] = None,
    result_seconds: Optional[float] = None,
) -> bool:
    """Whether a catalogue result is the song that was asked for.

    This is deliberately strict. A song that is not in the catalogue has no
    cover there, and using a different song's cover is worse than using the
    video's own picture: "Monday Morning" by Young Thug was given the cover
    of a Juicy J song he is only featured on.
    """
    wanted_core = _core_title(wanted_title)
    result_core = _core_title(result_title)
    if not wanted_core or wanted_core != result_core:
        return False

    # The main artist named by the upload has to be on the release. A
    # feature credit alone is not enough: "Yeah Yeah" by Travis Scott was
    # matched to a different Young Thug song because he is credited on
    # both, and the two titles and lengths were nearly identical.
    wanted = [part for part in wanted_parts if _norm(part)]
    credited = _artist_parts(result_artist)
    credited_norm = {_norm(part) for part in credited}
    if wanted:
        main = _norm(wanted[0])
        if main not in credited_norm:
            return False
        primary = _norm(credited[0]) if credited else ""
        if primary not in {_norm(part) for part in wanted}:
            return False

    # A version marker that the upload title does not carry means this is a
    # different recording, however well the names line up.
    wanted_text = _norm(wanted_title)
    for marker in _DIFFERENT_RECORDING:
        if marker in wanted_text:
            continue
        if marker in _core_title(result_title):
            return False
        if any(marker in item for item in _qualifiers(result_title)):
            return False

    # Last and strongest: the audio itself. Two songs can share a title and
    # an artist, and a guessed name can point at a real but different song.
    # A length that is nowhere near the upload is proof it is not the same
    # recording.
    return _duration_matches(wanted_seconds, result_seconds)


def _itunes_search(term: str, limit: int = 8) -> list[dict]:
    if not term:
        return []
    params = {"term": term, "entity": "song", "limit": limit}
    try:
        response = requests.get(
            ITUNES_URL, params=params,
            headers={"User-Agent": USER_AGENT}, timeout=15,
        )
        if response.status_code >= 400:
            return []
        return list(response.json().get("results") or [])
    except (requests.exceptions.RequestException, ValueError) as exc:
        logger.debug("iTunes lookup failed: %s", exc)
        return []


def _search_titles(title: str) -> list[str]:
    """Titles to search for: the full one, and it without its brackets.

    An upload title often carries a long parenthetical — "90210 (but it
    will change your life)" — and the catalogue answers that with
    unrelated tracks. The plain title finds the song. Matching still uses
    the full title, so this only widens what is searched, never what is
    accepted.
    """
    import re

    full = _clean(title)
    stripped = _clean(
        re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", title or "")
    )
    titles = [full]
    if stripped and _norm(stripped) != _norm(full):
        titles.append(stripped)
    return titles


def _itunes_cover(
    artist: str, title: str, seconds: Optional[float] = None
) -> Optional[str]:
    """The best cover iTunes has for this exact song, or None.

    Searching the whole artist credit can miss the song entirely: for
    "Kasane Teto SV, Yoshida Yasei" the single by "Yoshida Yasei" never
    comes back, only compilations that happen to credit everyone. So each
    artist is searched on its own as well, and everything found is ranked
    together.

    Nothing is returned when no result is actually this song. An unreleased
    track has no cover in the catalogue, and borrowing another song's is
    worse than falling back to the video's own picture.
    """
    terms: list[str] = []
    for search_title in _search_titles(title):
        if artist:
            terms.append(_clean(f"{artist} {search_title}"))
            for part in _artist_parts(artist):
                terms.append(_clean(f"{part} {search_title}"))
        terms.append(search_title)

    results: list[dict] = []
    seen = set()
    for term in terms:
        for result in _itunes_search(term):
            key = result.get("trackId") or result.get("artworkUrl100")
            if key in seen:
                continue
            seen.add(key)
            results.append(result)

    if not results:
        return None

    wanted_parts = _artist_parts(artist)
    wanted_core = _core_title(title)

    scored: list[tuple[tuple, str]] = []
    for result in results:
        track = _clean(result.get("trackName", ""))
        performer = _clean(result.get("artistName", ""))
        album = _clean(result.get("collectionName", ""))
        result_seconds = None
        millis = result.get("trackTimeMillis")
        if millis:
            try:
                result_seconds = float(millis) / 1000.0
            except (TypeError, ValueError):
                result_seconds = None
        if not _is_the_same_song(
            title, wanted_parts, track, performer,
            wanted_seconds=seconds, result_seconds=result_seconds,
        ):
            continue
        artwork = _large_artwork(result.get("artworkUrl100"))
        if not artwork:
            continue
        scored.append(
            (
                (
                    _itunes_kind_rank(_norm(track), _norm(album)),
                    0 if _core_title(track) == wanted_core else 1,
                    _duration_gap(seconds, result_seconds),
                ),
                artwork,
            )
        )

    if not scored:
        logger.info("No catalogue cover is this song: %s — %s", artist, title)
        return None
    scored.sort(key=lambda pair: pair[0])
    return scored[0][1]


def _itunes_kind_rank(track: str, album: str) -> int:
    """Penalise the releases that are a different recording of the song."""
    text = f"{track} {album}"
    for marker in ("karaoke", "backing track", "instrumental", "tribute"):
        if marker in text:
            return 4
    if "live" in text:
        return 3
    if "cover" in text:
        return 2
    if "remix" in text:
        return 2
    return 0


def _itunes_album_cover(artist: str, album: str) -> Optional[str]:
    """The cover of a named album by a named artist.

    Used when the song's own title found nothing — an unreleased track that
    is filed on a known album, or a title the catalogue spells differently.
    The album name and the artist both have to match, so a different album
    is never borrowed.
    """
    album = _clean(album)
    if not album:
        return None
    wanted_album = _norm(album)
    wanted_parts = {_norm(part) for part in _artist_parts(artist) if part}

    terms = [_clean(f"{artist} {album}") if artist else album, album]
    seen = set()
    scored: list[tuple[int, str]] = []
    for term in terms:
        params = {"term": term, "entity": "album", "limit": 10}
        try:
            response = requests.get(
                ITUNES_URL, params=params,
                headers={"User-Agent": USER_AGENT}, timeout=15,
            )
            if response.status_code >= 400:
                continue
            results = response.json().get("results") or []
        except (requests.exceptions.RequestException, ValueError):
            continue
        for result in results:
            found = _norm(result.get("collectionName", ""))
            if found != wanted_album:
                continue
            performer = _norm(result.get("artistName", ""))
            if wanted_parts and performer not in wanted_parts:
                continue
            artwork = _large_artwork(result.get("artworkUrl100"))
            if not artwork or artwork in seen:
                continue
            seen.add(artwork)
            scored.append((0, artwork))
        if scored:
            break
    if not scored:
        return None
    return scored[0][1]


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


# ------------------------------------------------------------------ Deezer


def _deezer_cover(
    artist: str, title: str, seconds: Optional[float] = None
) -> list[str]:
    """Cover URLs from Deezer, best first.

    Deezer is the fallback for songs iTunes does not carry: it has a much
    wider catalogue of non-Western and independent releases, and it is
    free and needs no key. Its largest cover is 1000 px.
    """
    if not title:
        return []
    terms: list[str] = []
    for search_title in _search_titles(title):
        if artist:
            terms.append(_clean(f"{artist} {search_title}"))
            for part in _artist_parts(artist):
                terms.append(_clean(f"{part} {search_title}"))
        terms.append(search_title)

    results: list[dict] = []
    seen = set()
    for term in terms:
        try:
            response = requests.get(
                DEEZER_URL, params={"q": term, "limit": 10},
                headers={"User-Agent": USER_AGENT}, timeout=15,
            )
            if response.status_code >= 400:
                continue
            data = response.json()
        except (requests.exceptions.RequestException, ValueError) as exc:
            logger.debug("Deezer lookup failed: %s", exc)
            continue
        for item in data.get("data") or []:
            key = item.get("id")
            if key in seen:
                continue
            seen.add(key)
            results.append(item)

    wanted_parts = _artist_parts(artist)
    wanted_core = _core_title(title)

    scored: list[tuple[tuple, str]] = []
    for item in results:
        track = _clean(item.get("title", ""))
        performer = _clean((item.get("artist") or {}).get("name", ""))
        album = item.get("album") or {}
        album_name = _clean(album.get("title", ""))
        if not _is_the_same_song(
            title, wanted_parts, track, performer,
            wanted_seconds=seconds, result_seconds=item.get("duration"),
        ):
            continue
        url = album.get("cover_xl") or album.get("cover_big") or ""
        if not url:
            continue
        scored.append(
            (
                (
                    _itunes_kind_rank(_norm(track), _norm(album_name)),
                    0 if _core_title(track) == wanted_core else 1,
                    _duration_gap(seconds, item.get("duration")),
                ),
                url,
            )
        )

    if not scored:
        return []
    scored.sort(key=lambda pair: pair[0])
    ordered: list[str] = []
    for _rank, url in scored:
        if url not in ordered:
            ordered.append(url)
    return ordered


# --------------------------------------------------------- MusicBrainz


def _musicbrainz_cover(
    artist: str, title: str, seconds: Optional[float] = None
) -> list[str]:
    """Cover Art Archive URLs for this song, best release first.

    A recording is listed on many releases and only some carry artwork, so
    every matching release is offered and the caller tries them in order.
    """
    if not title:
        return []
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
            return []
        data = response.json()
    except (requests.exceptions.RequestException, ValueError):
        return []

    wanted_parts = _artist_parts(artist)
    urls: list[str] = []
    for recording in data.get("recordings") or []:
        # The same strict test as iTunes: a recording credited to someone
        # else is a different song, even if it matched the search.
        credited = []
        for credit in recording.get("artist-credit") or []:
            if isinstance(credit, dict):
                name = credit.get("name") or (credit.get("artist") or {}).get("name")
                if name:
                    credited.append(str(name))
        length = recording.get("length")
        try:
            result_seconds = float(length) / 1000.0 if length else None
        except (TypeError, ValueError):
            result_seconds = None
        if not _is_the_same_song(
            title, wanted_parts, str(recording.get("title") or ""),
            ", ".join(credited),
            wanted_seconds=seconds, result_seconds=result_seconds,
        ):
            continue
        for release in recording.get("releases") or []:
            release_id = release.get("id")
            if not release_id:
                continue
            if not _release_is_the_album(release):
                continue
            url = COVER_ARCHIVE_URL.format(mbid=release_id)
            if url not in urls:
                urls.append(url)
    return urls


def _release_is_the_album(release: dict) -> bool:
    """Whether this release is the album the song belongs to.

    A live album or a various-artists compilation does contain the song,
    but its cover is not the song's cover, so it is skipped. Without this
    a studio track can be given a "Live at Lowlands" sleeve.
    """
    group = release.get("release-group") or {}
    primary = str(group.get("primary-type") or "").strip().lower()
    secondary = {
        str(kind).strip().lower() for kind in (group.get("secondary-types") or [])
    }
    if "live" in secondary or primary == "live":
        return False
    if "compilation" in secondary:
        return False
    if "dj-mix" in secondary or "mixtape/street" in secondary:
        return False
    return primary in ("album", "single", "ep", "")


# ---------------------------------------------------------------- public


# Bumped when the lookup rules change enough that a cover cached by an
# older version might be the wrong song. A version before 3 could embed a
# different song's artwork for an unreleased track.
CACHE_VERSION = 3


def cover_cache_path(platform: str, media_id: str) -> Path:
    """Where a song's cover is cached.

    The size and the rule version are part of the name, so a cover stored
    by an older version is not reused: it is looked up again once, and the
    corrected artwork replaces it.
    """
    from musicdlp.core.paths import art_cache_dir

    return art_cache_dir() / (
        f"album_{platform}_{media_id}_{TARGET_COVER_SIZE}_v{CACHE_VERSION}.jpg"
    )


def find_cover(
    artist: str,
    title: str,
    destination: Path,
    *,
    album: str = "",
    refresh: bool = False,
    seconds: Optional[float] = None,
) -> Optional[Path]:
    """Find and save square cover art. Returns None when nothing is found.

    ``refresh`` ignores an already-cached image, for when the song's details
    have changed since the last lookup. ``seconds`` is the length of the
    audio being tagged; when it is known, a catalogue track whose length is
    nowhere near it is rejected, which is the surest way to avoid another
    song's cover.
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

    def offer(urls) -> None:
        if not urls:
            return
        if isinstance(urls, str):
            urls = [urls]
        for url in urls:
            if url and url not in candidates:
                candidates.append(url)

    for lookup in (
        lambda: _itunes_cover(artist, title, seconds),
        lambda: _deezer_cover(artist, title, seconds),
        lambda: _musicbrainz_cover(artist, title, seconds),
        lambda: _itunes_album_cover(artist, album) if album else None,
    ):
        try:
            offer(lookup())
        except Exception as exc:
            logger.debug("Cover lookup failed: %s", exc)
            continue

    # Art is tried in order and the first one that downloads wins. A
    # release in the catalogue often has no picture attached, so a 404 is
    # expected rather than an error.
    for url in candidates[:8]:
        data = _download(url)
        if not data:
            continue
        saved = _save_square(data, destination)
        if saved:
            logger.info("Found cover art for %s — %s", artist or "?", title)
            return saved

    logger.info("No cover art found for %s — %s", artist or "?", title)
    return None
