"""A light fingerprint per output file, to catch the wrong-file accidents.

Two things can go wrong without anyone noticing:

* two library rows end up pointing at one file (a name collision on the
  way to Spotify, or a relink that matched the wrong copy), and
* a row's file is replaced by a different song while the row keeps its
  old details.

A full audio hash would answer both, but it means reading every byte of
every song. This is deliberately cheap: the size, the duration, and a
hash of the first and last 64 KiB. It is recorded whenever Music DLP
itself writes a file, so a mismatch means something else changed it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from musicdlp.core.db import Database

logger = logging.getLogger("musicdlp.fingerprint")

CHUNK = 64 * 1024


@dataclass(frozen=True)
class Fingerprint:
    size: int
    head: str
    tail: str
    duration_ms: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "size": self.size,
            "head": self.head,
            "tail": self.tail,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Fingerprint":
        return cls(
            size=int(data["size"]),
            head=str(data["head"]),
            tail=str(data["tail"]),
            duration_ms=(
                int(data["duration_ms"])
                if data.get("duration_ms") is not None
                else None
            ),
        )

    def signature(self) -> tuple:
        """What has to match for two files to count as the same audio."""
        return (self.size, self.head, self.tail)


def fingerprint_file(path: Path) -> Optional[Fingerprint]:
    """Fingerprint one file, or None when it cannot be read."""
    path = Path(path)
    try:
        size = path.stat().st_size
        if size <= 0:
            return None
        with path.open("rb") as handle:
            head = hashlib.sha256(handle.read(min(CHUNK, size))).hexdigest()
            handle.seek(max(0, size - CHUNK))
            tail = hashlib.sha256(handle.read(CHUNK)).hexdigest()
    except OSError as exc:
        logger.debug("Could not fingerprint %s: %s", path, exc)
        return None

    duration_ms = None
    try:
        # mutagen reads the container header, not the audio: this is cheap.
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(path))
        if audio is not None and getattr(audio, "info", None) is not None:
            length = getattr(audio.info, "length", None)
            if length:
                duration_ms = int(round(float(length) * 1000))
    except Exception:  # noqa: BLE001 - a broken tag block is not fatal
        duration_ms = None

    return Fingerprint(size=size, head=head, tail=tail, duration_ms=duration_ms)


def fingerprints_for_track(track) -> dict[str, dict]:
    """Fingerprints of the output files this row currently has."""
    result: dict[str, dict] = {}
    for raw in (track.mp3_path, track.m4a_path):
        if not raw:
            continue
        path = Path(raw)
        found = fingerprint_file(path)
        if found is not None:
            result[str(path)] = found.to_dict()
    return result


def record_fingerprints(db: Database, track_id: int) -> None:
    """Store what the row's files look like right now.

    Called after every write Music DLP makes itself, so a later mismatch
    means a change from outside the app.
    """
    track = db.get_track(track_id)
    if track is None:
        return
    payload = fingerprints_for_track(track)
    db.update_track(
        track_id,
        fingerprints=json.dumps(payload) if payload else None,
    )


def stored_fingerprints(track) -> dict[str, dict]:
    if not track.fingerprints:
        return {}
    try:
        data = json.loads(track.fingerprints)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(path): value
        for path, value in data.items()
        if isinstance(value, dict)
    }


def find_shared_files(db: Database) -> list[dict]:
    """Different songs whose stored output has one and the same content."""
    by_signature: dict[tuple, list[tuple[int, str, str]]] = {}
    for track in db.get_all_tracks():
        for path, value in stored_fingerprints(track).items():
            if not Path(path).is_file():
                continue
            try:
                signature = Fingerprint.from_dict(value).signature()
            except (KeyError, TypeError, ValueError):
                continue
            by_signature.setdefault(signature, []).append(
                (track.id, track.display_title, path)
            )

    groups = []
    for entries in by_signature.values():
        if len({track_id for track_id, _title, _path in entries}) < 2:
            continue
        groups.append({
            "tracks": [
                {"id": track_id, "title": title}
                for track_id, title, _path in entries
            ],
            "path": entries[0][2],
        })
    return groups


def find_changed_files(db: Database) -> list[dict]:
    """Rows whose file no longer matches what Music DLP last wrote."""
    changed = []
    for track in db.get_all_tracks():
        for path, value in stored_fingerprints(track).items():
            current_path = Path(path)
            if not current_path.is_file():
                # A deleted file is already shown as "File missing".
                continue
            try:
                old = Fingerprint.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue
            now = fingerprint_file(current_path)
            if now is None:
                continue
            if now.signature() != old.signature():
                changed.append({
                    "track_id": track.id,
                    "title": track.display_title,
                    "path": path,
                })
    return changed


def check_library(db: Database) -> dict:
    """Shared and changed outputs, plus how many files were inspected."""
    checked = 0
    for track in db.get_all_tracks():
        checked += sum(
            1 for path in stored_fingerprints(track)
            if Path(path).is_file()
        )
    return {
        "checked": checked,
        "shared": find_shared_files(db),
        "changed": find_changed_files(db),
    }