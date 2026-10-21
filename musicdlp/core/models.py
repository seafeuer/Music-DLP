"""Domain models shared by the services and the UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

# Track lifecycle. A track's status is global: the same media item can appear
# in several sources, but it is downloaded and converted once.
NEW = "new"
QUEUED = "queued"
DOWNLOADING = "downloading"
DOWNLOADED = "downloaded"
CONVERTING = "converting"
CONVERTED = "converted"
SYNCING = "syncing"
SYNCED = "synced"
ERROR = "error"
CANCELLED = "cancelled"
NEEDS_UPDATE = "needs_update"
MISSING = "missing"

STATUSES = (
    NEW, QUEUED, DOWNLOADING, DOWNLOADED, CONVERTING, CONVERTED,
    SYNCING, SYNCED, ERROR, CANCELLED, NEEDS_UPDATE, MISSING,
)

DOWNLOADED_STATES = (DOWNLOADED, CONVERTING, CONVERTED, SYNCING, SYNCED)
CONVERTED_STATES = (CONVERTED, SYNCED)
ACTIVE_STATES = (QUEUED, DOWNLOADING, CONVERTING, SYNCING)

# Sentinel used by the library list for the "all songs" view. Real source
# ids start at 1.
ALL_SONGS_ID = 0


def status_label(status: str) -> str:
    return {
        NEW: "Not downloaded",
        QUEUED: "Queued",
        DOWNLOADING: "Downloading",
        DOWNLOADED: "Downloaded",
        CONVERTING: "Converting",
        CONVERTED: "Converted",
        SYNCING: "Sending to Spotify",
        SYNCED: "In Spotify",
        ERROR: "Error",
        CANCELLED: "Cancelled",
        NEEDS_UPDATE: "Needs update",
        MISSING: "File missing",
    }.get(status, status.title())


def display_status(track: "Track", file_missing: bool = False) -> str:
    """The status a person should see, including pending metadata edits."""
    if file_missing and is_converted(track.status):
        return MISSING
    if track.needs_update and is_converted(track.status):
        return NEEDS_UPDATE
    if track.sync_error and track.status in CONVERTED_STATES:
        return ERROR
    return track.status


def is_downloaded(status: str) -> bool:
    return status in DOWNLOADED_STATES


def is_converted(status: str) -> bool:
    return status in CONVERTED_STATES


def is_active(status: str) -> bool:
    return status in ACTIVE_STATES


def format_duration(seconds: Optional[int]) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return "-"
    if total <= 0:
        return "-"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


@dataclass
class Source:
    id: int
    url: str
    kind: str
    platform: str
    external_id: Optional[str]
    title: str
    uploader: Optional[str]
    item_count: int
    thumbnail_url: Optional[str]
    added_at: Optional[str]
    refreshed_at: Optional[str]
    folder: Optional[str] = None
    filename_template: Optional[str] = None

    @property
    def is_all(self) -> bool:
        return self.id == ALL_SONGS_ID

    @property
    def is_folder(self) -> bool:
        return self.kind == "folder"

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Source":
        return cls(
            id=row["id"],
            url=row["url"],
            kind=row["kind"],
            platform=row["platform"],
            external_id=row["external_id"],
            title=row["title"],
            uploader=row["uploader"],
            item_count=row["item_count"] or 0,
            thumbnail_url=row["thumbnail_url"],
            added_at=row["added_at"],
            refreshed_at=row["refreshed_at"],
            folder=row["folder"] if "folder" in row.keys() else None,
            filename_template=(
                row["filename_template"]
                if "filename_template" in row.keys() else None
            ),
        )


@dataclass
class Track:
    id: int
    platform: str
    media_id: str
    url: str
    source_index: Optional[int]
    title: str
    uploader: Optional[str]
    duration: Optional[int]
    thumbnail_url: Optional[str]
    description: Optional[str]
    status: str
    error: Optional[str]
    meta_title: Optional[str]
    meta_artist: Optional[str]
    meta_album: Optional[str]
    meta_track: Optional[int]
    raw_path: Optional[str]
    mp3_path: Optional[str]
    m4a_path: Optional[str]
    cover_path: Optional[str]
    downloaded_at: Optional[str]
    converted_at: Optional[str]
    synced_at: Optional[str]
    sync_error: Optional[str] = None
    needs_update: bool = False
    smart_title: Optional[str] = None
    smart_artist: Optional[str] = None
    smart_confidence: Optional[float] = None
    smart_reason: Optional[str] = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Track":
        return cls(
            id=row["id"],
            platform=row["platform"],
            media_id=row["media_id"],
            url=row["url"],
            source_index=row["source_index"],
            title=row["title"],
            uploader=row["uploader"],
            duration=row["duration"],
            thumbnail_url=row["thumbnail_url"],
            description=row["description"],
            status=row["status"],
            error=row["error"],
            meta_title=row["meta_title"],
            meta_artist=row["meta_artist"],
            meta_album=row["meta_album"],
            meta_track=row["meta_track"],
            raw_path=row["raw_path"],
            mp3_path=row["mp3_path"],
            m4a_path=row["m4a_path"],
            cover_path=row["cover_path"],
            downloaded_at=row["downloaded_at"],
            converted_at=row["converted_at"],
            synced_at=row["synced_at"],
            sync_error=row["sync_error"] if "sync_error" in row.keys() else None,
            needs_update=bool(
                row["needs_update"] if "needs_update" in row.keys() else 0
            ),
            smart_title=row["smart_title"] if "smart_title" in row.keys() else None,
            smart_artist=row["smart_artist"] if "smart_artist" in row.keys() else None,
            smart_confidence=(
                row["smart_confidence"]
                if "smart_confidence" in row.keys() else None
            ),
            smart_reason=row["smart_reason"] if "smart_reason" in row.keys() else None,
        )

    @property
    def has_suggestion(self) -> bool:
        """True when a smart title exists and is not what is already used."""
        if not self.smart_title:
            return False
        return (
            self.smart_title != self.display_title
            or (self.smart_artist or "") != (self.display_artist or "")
        )

    @property
    def display_title(self) -> str:
        return (self.meta_title or self.title or "").strip() or "Untitled"

    @property
    def display_artist(self) -> str:
        return (self.meta_artist or self.uploader or "").strip()

    @property
    def display_album(self) -> str:
        return (self.meta_album or "").strip()

    @property
    def display_track(self) -> Optional[int]:
        return self.meta_track

    @property
    def output_paths(self) -> list[str]:
        return [p for p in (self.mp3_path, self.m4a_path) if p]


@dataclass
class TrackRow:
    """A track as it appears inside one source."""

    source: Source
    track: Track
    position: int
    file_missing: bool = False

    @property
    def id(self) -> int:
        return self.track.id

    @property
    def display_title(self) -> str:
        return self.track.display_title

    @property
    def display_artist(self) -> str:
        return self.track.display_artist

    @property
    def duration(self) -> Optional[int]:
        return self.track.duration

    @property
    def status(self) -> str:
        return self.track.status


@dataclass
class BatchItem:
    """One unit of work: a track within the source being processed."""

    source: Source
    track: Track
    position: int
