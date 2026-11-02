"""Work out how a Music DLP folder and a Spotify playlist should line up.

Spotify's local files are indexed under URIs shaped like::

    spotify:local:<Artist>:<Album>:<Title>:<seconds>

The panel reads that index from inside Spotify and hands it here together
with the playlist's current contents. This module does no network or disk
work, so the matching rules can be tested on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from musicdlp.services.artwork import _norm

# A local file's length in the URI is whole seconds, and the tags Spotify
# read may differ by a second or two from what yt-dlp reported.
DURATION_TOLERANCE_SECONDS = 3


@dataclass
class LocalTrack:
    """One entry in Spotify's Local Files index."""

    uri: str
    title: str = ""
    artist: str = ""
    album: str = ""
    seconds: Optional[int] = None

    @classmethod
    def from_panel(cls, data: dict) -> "LocalTrack":
        """Build from the JSON the panel sends.

        Spotify reports ``duration`` as ``{"milliseconds": n}``; older
        payloads carry a plain number. Both are accepted.
        """
        duration = data.get("duration")
        seconds: Optional[int] = None
        if isinstance(duration, dict):
            millis = duration.get("milliseconds")
            if isinstance(millis, (int, float)) and millis > 0:
                # Spotify stores whole seconds in the URI, so round down to
                # match how it would have written the same file.
                seconds = int(millis // 1000)
        elif isinstance(duration, (int, float)) and duration > 0:
            # Some payloads give seconds here and some give milliseconds;
            # anything over an hour in "seconds" is really milliseconds.
            seconds = int(duration // 1000) if duration > 36000 else int(duration)

        artists = data.get("artists") or []
        if isinstance(artists, list):
            names = []
            for artist in artists:
                if isinstance(artist, dict):
                    names.append(str(artist.get("name") or ""))
                else:
                    names.append(str(artist or ""))
            artist = ", ".join(name for name in names if name)
        elif isinstance(artists, str):
            artist = artists

        album = data.get("album") or ""
        if isinstance(album, dict):
            album = album.get("name") or ""

        return cls(
            uri=str(data.get("uri") or ""),
            title=str(data.get("name") or data.get("title") or ""),
            artist=artist,
            album=str(album),
            seconds=seconds,
        )


@dataclass
class FolderTrack:
    """One song in a Music DLP folder, in the form matching needs."""

    track_id: int
    title: str
    artist: str
    seconds: Optional[int] = None


@dataclass
class SyncPlan:
    """What a sync has to do to make the playlist match the folder."""

    to_add: list[str] = field(default_factory=list)
    to_remove: list[str] = field(default_factory=list)
    # Folder songs with no local file to point at. They are not errors:
    # the song may not be converted yet, or Spotify may not have rescanned.
    unmatched: list[FolderTrack] = field(default_factory=list)
    matched: list[tuple[FolderTrack, str]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.to_add and not self.to_remove

    def summary(self) -> dict:
        return {
            "add": len(self.to_add),
            "remove": len(self.to_remove),
            "unmatched": len(self.unmatched),
            "matched": len(self.matched),
        }


def _title_key(title: str) -> str:
    return _norm(title)


def _artist_key(artist: str) -> str:
    return _norm(artist)


def _length_delta(left: Optional[int], right: Optional[int]) -> Optional[int]:
    if left is None or right is None:
        return None
    return abs(int(left) - int(right))


def _pick(
    candidates: list[LocalTrack],
    folder_track: FolderTrack,
) -> Optional[LocalTrack]:
    """Choose the local file that is most likely this song.

    A shared title is not enough on its own: covers and sped-up versions
    share one. The artist has to agree when both sides know it, and where
    two copies qualify, the closer length wins.
    """
    wanted_artist = _artist_key(folder_track.artist)
    scored: list[tuple[int, int, LocalTrack]] = []

    for candidate in candidates:
        delta = _length_delta(folder_track.seconds, candidate.seconds)
        if delta is not None and delta > DURATION_TOLERANCE_SECONDS:
            continue

        candidate_artist = _artist_key(candidate.artist)
        if wanted_artist and candidate_artist:
            if candidate_artist != wanted_artist:
                continue
            artist_rank = 0
        else:
            # One side has no artist to compare. Accept it, but behind any
            # candidate that matched on artist.
            artist_rank = 1

        # Unknown length sorts after a known, close one.
        length_rank = delta if delta is not None else DURATION_TOLERANCE_SECONDS + 1
        scored.append((artist_rank, length_rank, candidate))

    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]))
    return scored[0][2]


def build_plan(
    folder_tracks: Iterable[FolderTrack],
    local_tracks: Iterable[LocalTrack],
    playlist_uris: Iterable[str],
) -> SyncPlan:
    """Return the additions and removals that make the playlist match.

    The playlist is mirrored exactly: songs in it that the folder does not
    contain are removed, including real Spotify tracks added by hand.
    """
    index: dict[str, list[LocalTrack]] = {}
    for local in local_tracks:
        if not local.uri:
            continue
        index.setdefault(_title_key(local.title), []).append(local)

    plan = SyncPlan()
    wanted: list[str] = []
    seen: set[str] = set()

    for folder_track in folder_tracks:
        candidates = index.get(_title_key(folder_track.title)) or []
        match = _pick(candidates, folder_track)
        if match is None:
            plan.unmatched.append(folder_track)
            continue
        plan.matched.append((folder_track, match.uri))
        if match.uri not in seen:
            seen.add(match.uri)
            wanted.append(match.uri)

    current = [uri for uri in playlist_uris if uri]
    current_set = set(current)

    plan.to_add = [uri for uri in wanted if uri not in current_set]
    plan.to_remove = [uri for uri in current if uri not in seen]
    return plan


def folder_tracks_from_rows(rows: Iterable) -> list[FolderTrack]:
    """Adapt ``TrackRow`` objects (or anything shaped like one) for matching."""
    out: list[FolderTrack] = []
    for row in rows:
        track = getattr(row, "track", row)
        out.append(
            FolderTrack(
                track_id=int(getattr(track, "id", 0) or 0),
                title=str(getattr(track, "display_title", "") or ""),
                artist=str(getattr(track, "display_artist", "") or ""),
                seconds=getattr(track, "duration", None),
            )
        )
    return out
