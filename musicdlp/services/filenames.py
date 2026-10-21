"""Filename rendering, sanitizing, and collision handling."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

INVALID_CHARS = '<>:"/\\|?*'
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
DEFAULT_TEMPLATE = "{title}"


def sanitize_filename(name: str, max_length: int = 140) -> str:
    """Make a name safe on Windows while keeping Unicode letters."""
    text = name or ""
    text = "".join("_" if ch in INVALID_CHARS else ch for ch in text)
    text = "".join(" " if ch in "\r\n\t" else ch for ch in text)
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    text = " ".join(text.split()).strip(" .")

    if not text:
        text = "Untitled"

    stem = text
    suffix = ""
    if "." in text:
        candidate_stem, candidate_suffix = text.rsplit(".", 1)
        if candidate_stem and len(candidate_suffix) <= 8:
            stem, suffix = candidate_stem, "." + candidate_suffix

    if stem.upper() in RESERVED_NAMES:
        stem = "_" + stem

    if len(stem) > max_length:
        stem = stem[:max_length].rstrip(" .") or "Untitled"

    return f"{stem}{suffix}"


class _SafeDict(dict):
    def __missing__(self, key):  # leave unknown placeholders empty
        return ""


class _TrackNumber(int):
    """A track number that renders zero-padded by default.

    ``{track}`` has always produced ``03``. A format spec such as
    ``{track:02d}`` or ``{track:d}`` has to be applied to the number
    itself, so the padding happens here rather than in a pre-made string.
    """

    def __format__(self, spec: str) -> str:
        if not spec:
            return f"{int(self):02d}"
        return format(int(self), spec)


def _track_value(track: Optional[int]) -> object:
    if not track:
        return ""
    try:
        return _TrackNumber(track)
    except (TypeError, ValueError):
        return ""


def render_template(
    template: str,
    *,
    title: str,
    artist: str = "",
    album: str = "",
    track: Optional[int] = None,
    media_id: str = "",
    source: str = "",
    max_length: int = 140,
) -> str:
    """Render a filename template and sanitize the result.

    Supported placeholders: {title}, {artist}, {album}, {track}, {id},
    {media_id}, {source}. ``{track}`` is zero-padded; ``{track:02d}`` and
    the other number format specs also work. The default template is
    ``{title}`` so files keep the published title.
    """
    template = (template or DEFAULT_TEMPLATE).strip() or DEFAULT_TEMPLATE
    values = _SafeDict(
        title=title or "",
        artist=artist or "",
        album=album or "",
        track=_track_value(track),
        id=media_id or "",
        media_id=media_id or "",
        source=source or "",
    )
    try:
        rendered = template.format_map(values)
    except (ValueError, IndexError):
        rendered = title or "Untitled"

    # A template of only separators can collapse to nothing.
    rendered = " ".join(rendered.split())
    return sanitize_filename(rendered, max_length=max_length)


def unique_path(
    directory: Path,
    stem: str,
    extension: str,
    *,
    fallback_id: str = "",
    max_path_length: int = 240,
) -> Path:
    """Return a path that does not collide with an existing file.

    Tries ``stem.ext``, then ``stem [id].ext``, then ``stem (2).ext``.
    """
    directory = Path(directory)
    extension = extension if extension.startswith(".") else f".{extension}"
    stem = sanitize_filename(stem)

    def fits(candidate_stem: str) -> bool:
        return len(str(directory / f"{candidate_stem}{extension}")) <= max_path_length

    if not fits(stem):
        overflow = len(str(directory / f"{stem}{extension}")) - max_path_length
        stem = stem[: max(20, len(stem) - overflow - 8)].rstrip(" .") or "Untitled"

    candidate = directory / f"{stem}{extension}"
    if not candidate.exists():
        return candidate

    if fallback_id:
        tagged = f"{stem} [{sanitize_filename(fallback_id, 40)}]"
        if fits(tagged):
            candidate = directory / f"{tagged}{extension}"
            if not candidate.exists():
                return candidate

    for index in range(2, 1000):
        numbered = f"{stem} ({index})"
        candidate = directory / f"{numbered}{extension}"
        if not candidate.exists():
            return candidate

    raise OSError(f"Could not find a free filename for {stem!r}")


def safe_stem(text: str, max_length: int = 120) -> str:
    """Convenience wrapper used for temporary names."""
    return sanitize_filename(text, max_length=max_length)
