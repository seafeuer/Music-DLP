"""Work out a song title and artist from a messy video title.

YouTube and X provide no structured artist/track fields for most music
uploads, so this is a deterministic parser, not a guess from a model.

Two signals make it useful:

* the usual ``Artist - Title`` shape, with upload noise removed, and
* artist names the user has already confirmed, which lets it split titles
  that have no separator at all (a leak account posting
  "NEW LEAK MONDAY MORNING YOUNG THUG").

Every suggestion carries a confidence. Low-confidence results are never
applied automatically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

# Auto-apply only at or above this confidence.
AUTO_THRESHOLD = 0.8

SEPARATORS = (" - ", " – ", " — ", " | ", " // ", "｜", "／")

# Phrases that describe the upload, not the song.
_NOISE_PATTERNS = [
    r"\(\s*official\s*(?:music\s*)?video\s*\)",
    r"\[\s*official\s*(?:music\s*)?video\s*\]",
    r"\(\s*official\s*audio\s*\)",
    r"\(\s*(?:audio|audio\s*only)\s*\)",
    r"\(\s*lyrics?\s*\)",
    r"\(\s*lyric\s*video\s*\)",
    r"\(\s*visuali[sz]er\s*\)",
    r"\(\s*(?:m/?v)\s*\)",
    r"\[\s*(?:m/?v)\s*\]",
    r"【\s*(?:m/?v)\s*】",
    r"【\s*official\s*(?:music\s*)?video\s*】",
    r"\(\s*(?:(?:hq|hd|4k|8k|cdq|og|320kbps|flac|quality)\s*)+\)",
    r"\[\s*(?:(?:hq|hd|4k|8k|cdq|og|320kbps|flac|quality)\s*)+\]",
    r"\(\s*remaster(?:ed)?(?:\s*\d{4})?\s*\)",
    r"\(\s*color\s*coded\s*\)",
    r"\(\s*eng(?:lish)?\s*sub(?:s|titles)?\s*\)",
    r"\(\s*prod\.?[^)]*\)",
    r"【\s*[^】]*\s*】",
    r"歌ってみた",
    r"を歌ってみた",
    r"カバー",
    r"\(\s*cover\s*\)",
    r"\bnew\s+leak\b",
    r"\(\s*[a-z0-9 ]*era\s*\)",
    r"#\w+",
    r"\bjoin\s+the\s+(?:discord|dis|telegram|patreon)\b.*$",
    r"\bprod\.?\s+[A-Za-z0-9_.& ]+",
    r"\b(?:full\s*)?(?:mixtape|album)\s+out\s+now\b.*$",
    r"\.{3,}$",
]

# Channel names that are collections or reupload accounts, not the artist.
_NON_ARTIST_CHANNELS = re.compile(
    r"\b(?:vault|vaults|leaks?|clips?|archive|archives|lyrics?|uploads?|"
    r"promo|plug|radio|playlist|hub|central|nation|world|zone|tv|"
    r"beats|sounds?|unreleased|rarities)\b",
    re.IGNORECASE,
)

_TOPIC_SUFFIX = re.compile(r"\s*-\s*topic\s*$", re.IGNORECASE)
_CHANNEL_SUFFIX = re.compile(
    r"\s*(?:-\s*topic|vevo|official|music|records|channel)\s*$",
    re.IGNORECASE,
)
_FEAT = re.compile(
    r"\b(?:ft\.?|feat\.?|featuring)\s+([^()\[\]【】]+)",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\(?\b(?:19|20)\d{2}\b\)?")

_SMALL_WORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "to", "for", "at",
    "by", "with", "from", "vs",
}


@dataclass
class Suggestion:
    title: str
    artist: str
    confidence: float
    reason: str

    @property
    def confident(self) -> bool:
        return self.confidence >= AUTO_THRESHOLD and bool(self.title)


def _strip_noise(text: str) -> str:
    result = text
    for pattern in _NOISE_PATTERNS:
        result = re.sub(pattern, " ", result, flags=re.IGNORECASE)
    result = _YEAR.sub(" ", result)
    # Brackets left empty after their contents were removed.
    result = re.sub(r"[\(\[]\s*[\)\]]", " ", result)
    result = re.sub(r"\s{2,}", " ", result)
    return result.strip(" -–—|/•·")


def _title_case(text: str) -> str:
    """Title-case all-caps text without mangling normal casing.

    Every word is capitalised: song titles read better as "At The Gates"
    than with the small words lowered.
    """
    if not text or not text.isupper():
        return text
    return " ".join(
        word[:1].upper() + word[1:] for word in text.lower().split()
    )


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def clean_channel(channel: str) -> tuple[str, bool]:
    """Return the channel name and whether it is a "- Topic" channel."""
    channel = (channel or "").strip()
    is_topic = bool(_TOPIC_SUFFIX.search(channel))
    if is_topic:
        channel = _TOPIC_SUFFIX.sub("", channel)
    else:
        channel = _CHANNEL_SUFFIX.sub("", channel)
    return channel.strip(), is_topic


def is_collection_channel(channel: str) -> bool:
    """True for upload/collection accounts rather than an artist page."""
    return bool(_NON_ARTIST_CHANNELS.search(channel or ""))


def _looks_like_artist(text: str) -> bool:
    text = text.strip()
    if not text or len(text) > 60:
        return False
    words = text.split()
    if len(words) > 5:
        return False
    if re.search(r"\d{4}", text):
        return False
    return bool(re.search(r"[A-Za-z]", text))


def _split_feat(text: str) -> tuple[str, list[str]]:
    """Separate a featured-artist credit from the title."""
    artists: list[str] = []
    matches = list(_FEAT.finditer(text))
    if not matches:
        return text, artists

    cleaned = text
    for match in matches:
        credit = match.group(1)
        # "FT LIL UZI VERT (OG)" -> stop at a bracket or another credit.
        credit = re.split(r"[(\[【]|\bprod\b", credit, flags=re.IGNORECASE)[0]
        credit = credit.strip(" ,&")
        if credit:
            artists.append(_title_case(credit.upper()))
        cleaned = cleaned.replace(match.group(0), " ")

    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–—|,&")
    return cleaned, artists


def _split_on_separator(text: str) -> Optional[tuple[str, str]]:
    for separator in SEPARATORS:
        if separator in text:
            left, right = text.split(separator, 1)
            left, right = left.strip(), right.strip()
            if left and right:
                return left, right
    return None


def _split_by_known_artist(
    text: str, known_artists: Iterable[str]
) -> Optional[tuple[str, str, str]]:
    """Find a confirmed artist name at the start or end of the text."""
    normalized_text = _normalize(text)
    best: Optional[tuple[str, str, str]] = None

    for artist in known_artists:
        artist = (artist or "").strip()
        if not artist or len(artist) < 3:
            continue
        normalized_artist = _normalize(artist)
        if not normalized_artist:
            continue

        for match in re.finditer(re.escape(normalized_artist), normalized_text):
            before = normalized_text[: match.start()].strip()
            after = normalized_text[match.end():].strip()
            if before and after:
                continue  # artist in the middle is too ambiguous
            remainder = after or before
            if len(remainder) < 2:
                continue
            title = _recover_case(remainder, text)
            best = (title, artist, "matched a known artist name")
            break
        if best:
            break

    return best


def _recover_case(normalized_fragment: str, original: str) -> str:
    """Take the fragment's wording from the original text, keeping its case."""
    words = normalized_fragment.split()
    if not words:
        return ""
    pattern = r"\b" + r"\W+".join(re.escape(word) for word in words) + r"\b"
    match = re.search(pattern, original, flags=re.IGNORECASE)
    if match:
        return match.group(0).strip()
    return " ".join(words)


def suggest(
    title: str,
    channel: str = "",
    description: str = "",
    known_artists: Iterable[str] = (),
) -> Suggestion:
    """Suggest a song title and artist for one upload."""
    raw_title = (title or "").strip()
    if not raw_title:
        return Suggestion("", "", 0.0, "no title")

    channel_name, is_topic = clean_channel(channel)

    # 1. A "- Topic" channel is generated by the artist's distributor.
    if is_topic and channel_name:
        cleaned = _strip_noise(raw_title)
        if cleaned:
            return Suggestion(
                _title_case(cleaned.upper()) if cleaned.isupper() else cleaned,
                channel_name,
                0.95,
                "artist channel",
            )

    # 2. Explicit credits in the description.
    from_description = _from_description(description)
    if from_description is not None:
        return from_description

    text = _strip_noise(raw_title)
    if not text:
        return Suggestion("", "", 0.0, "title is only noise")

    collection_channel = is_collection_channel(channel_name)

    # 3. A channel prefix repeated in the title is upload noise.
    if channel_name:
        normalized_channel = _normalize(channel_name)
        normalized_text = _normalize(text)
        if normalized_channel and normalized_text.startswith(normalized_channel):
            remainder = text[len(channel_name):].strip(" -–—|/•·")
            if len(remainder) >= 3:
                text = _strip_noise(remainder) or text

    # 4. "Artist - Title".
    parts = _split_on_separator(text)
    if parts is not None:
        left, right = parts
        left_clean = _strip_noise(left)
        right_clean = _strip_noise(right)
        if not left_clean or not right_clean:
            return Suggestion(text, "", 0.3, "one side was empty")

        title_part, featured = _split_feat(right_clean)
        artist_part = left_clean

        # If the channel is the artist, prefer it as the source of the name.
        channel_matches_left = (
            _normalize(channel_name) == _normalize(left_clean)
            if channel_name else False
        )

        if collection_channel and channel_matches_left:
            # A reupload account wrote its own name first. Drop it and look
            # for the artist inside the rest of the title.
            inner = _split_by_known_artist(right_clean, known_artists)
            if inner:
                title_part, artist_part, reason = inner
                return Suggestion(
                    _finish_title(title_part, featured),
                    _finish_artist([artist_part] + featured),
                    0.85,
                    reason,
                )
            return Suggestion(
                _finish_title(right_clean, featured),
                _finish_artist([]),
                0.45,
                "upload account prefix removed, artist unknown",
            )

        confidence = 0.88 if channel_matches_left else 0.82
        reason = (
            "channel matches the artist" if channel_matches_left
            else "artist and title separated"
        )
        # Use the channel's own spelling when it is the artist, so names like
        # YOASOBI keep their capitalisation.
        artist_source = [channel_name if channel_matches_left else artist_part]
        return Suggestion(
            _finish_title(title_part, featured),
            _finish_artist(artist_source + featured, preserve=channel_matches_left),
            confidence,
            reason,
        )

    # 5. No separator: use an artist name the user has already confirmed.
    matched = _split_by_known_artist(text, known_artists)
    if matched:
        title_part, artist_part, reason = matched
        title_part, featured = _split_feat(title_part)
        return Suggestion(
            _finish_title(title_part, featured),
            _finish_artist([artist_part] + featured),
            0.85,
            reason,
        )

    # 6. Nothing reliable. Keep the cleaned title and say so.
    cleaned = _title_case(text) if text.isupper() else text
    return Suggestion(cleaned, "", 0.3, "no artist found")


def _finish_title(title: str, featured: list[str]) -> str:
    title = _strip_noise(title).strip(" -–—|,")
    title = _title_case(title)
    return re.sub(r"\s{2,}", " ", title).strip()


def _finish_artist(names: list[str], preserve: bool = False) -> str:
    cleaned: list[str] = []
    for name in names:
        name = _strip_noise(name).strip(" ,&")
        if not name:
            continue
        if not preserve:
            name = _title_case(name) if name.isupper() else name
        if name.lower() not in {n.lower() for n in cleaned}:
            cleaned.append(name)
    return ", ".join(cleaned)


def _from_description(description: str) -> Optional[Suggestion]:
    """Read "Artist: X" / "Track: Y" style credits."""
    if not description:
        return None

    patterns = {
        "artist": r"^\s*(?:artist|by)\s*[:\-]\s*(.+)$",
        "title": r"^\s*(?:track|title|song)\s*[:\-]\s*(.+)$",
    }
    found: dict[str, str] = {}
    for line in description.splitlines()[:40]:
        for key, pattern in patterns.items():
            match = re.match(pattern, line.strip(), flags=re.IGNORECASE)
            if match and key not in found:
                value = match.group(1).strip()
                if 0 < len(value) <= 80:
                    found[key] = value

    if "artist" in found and "title" in found:
        return Suggestion(
            found["title"], found["artist"], 0.9, "credits in the description"
        )
    return None
