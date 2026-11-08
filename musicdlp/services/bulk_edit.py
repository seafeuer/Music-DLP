"""Apply one album/artist/number/title rule to many songs.

The desktop's per-song editor is the right tool for one song. A folder of
twenty leaks or a playlist burned from a mix needs the same change on all of
them, and doing that one dialog at a time is where mistakes happen. This
module holds the rule itself so it can be tested without a window: the
dialog only collects the values and the window saves the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from musicdlp.core.models import Track


@dataclass
class BulkEdit:
    """The changes to make. ``None`` means "leave this field alone"."""

    album: Optional[str] = None
    artist: Optional[str] = None
    # Track numbers count up from ``track_start``; ``None`` leaves them.
    track_start: Optional[int] = None
    track_step: int = 1
    # A title find/replace. ``find`` empty means "no title change".
    find: str = ""
    replace: str = ""
    use_regex: bool = False
    case_sensitive: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(
            self.album is not None
            or self.artist is not None
            or self.track_start is not None
            or self.find
        )


def title_edit(
    title: str, edit: BulkEdit
) -> str:
    """The title after the find/replace rule.

    A regex that does not compile is reported as ValueError; the dialog
    checks it first, but the service must not accept a half-valid rule
    just because a different caller forgot to.
    """
    if not edit.find:
        return title
    flags = 0 if edit.case_sensitive else re.IGNORECASE
    if edit.use_regex:
        try:
            pattern = re.compile(edit.find, flags)
        except re.error as exc:
            raise ValueError(f"That pattern is not valid: {exc}") from exc
        return pattern.sub(edit.replace, title)
    if edit.case_sensitive:
        return title.replace(edit.find, edit.replace)
    try:
        pattern = re.compile(re.escape(edit.find), flags)
    except re.error as exc:  # pragma: no cover - escaped input is valid
        raise ValueError(f"That text could not be matched: {exc}") from exc
    return pattern.sub(lambda _match: edit.replace, title)


def fields_for(
    track: Track, edit: BulkEdit, index: int = 0
) -> dict[str, object]:
    """The ``set_metadata`` fields for one song.

    Only fields the rule owns are returned, so a bulk album change cannot
    erase an artist the user set by hand.
    """
    fields: dict[str, object] = {}
    if edit.album is not None and edit.album.strip() != track.display_album:
        fields["album"] = edit.album.strip()
    if edit.artist is not None and edit.artist.strip() != track.display_artist:
        fields["artist"] = edit.artist.strip()
    if edit.track_start is not None:
        number = int(edit.track_start) + (int(edit.track_step) * index)
        if number != (track.display_track or None):
            fields["track_number"] = number
    if edit.find:
        title = title_edit(track.display_title, edit)
        if title != track.display_title:
            fields["title"] = title
    return fields


def order_rows(rows: list, visible_ids: list[int]) -> list:
    """Put the songs in the order the user is looking at them.

    The database returns ticked songs by id, which is insertion order, not
    the order of the playlist or the current sort. Track numbering applied
    in that order would number a playlist backwards.
    """
    order = {int(track_id): index for index, track_id in enumerate(visible_ids)}
    return sorted(
        rows,
        key=lambda row: order.get(
            row.track.id, len(order) + row.track.id
        ),
    )


def preview(
    tracks: list[Track], edit: BulkEdit, limit: int = 3
) -> list[str]:
    """A few before → after lines for the dialog."""
    lines: list[str] = []
    for index, track in enumerate(tracks[:limit]):
        fields = fields_for(track, edit, index)
        title = str(fields.get("title", track.display_title))
        artist = str(fields.get("artist", track.display_artist))
        album = str(fields.get("album", track.display_album))
        number = fields.get("track_number", track.display_track)
        bits = [title or "Untitled"]
        if artist:
            bits.append(artist)
        if album:
            bits.append(album)
        if number:
            bits.append(f"#{number}")
        lines.append(" — ".join(bits))
    return lines