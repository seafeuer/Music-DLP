"""Export song names for an external chatbot, and import the reply.

This is the no-setup path to AI naming: the app writes a small file
containing the songs that need names, the user pastes it into any chatbot,
and pastes the reply back. No account, no key, no local model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

INSTRUCTIONS = """You are naming songs for a music library.

Below is a JSON list of songs. For each one, work out the real song title
and the original artist.

Rules:
- "artist" is the original artist, not the channel that uploaded it.
- Remove channel names, "official video", lyrics tags, quality tags
  (HD, CDQ), years, and other upload noise from the title.
- Put featured performers in "artist", separated by commas.
- If you cannot identify a song, keep the cleaned title and use an empty
  artist, with a confidence below 0.4.
- Never invent an artist.

Reply with JSON only: a list in the same order, each item shaped like
{"id": "...", "title": "...", "artist": "...", "album": "...",
 "confidence": 0.0}

Songs:
"""


@dataclass
class ExportedSong:
    id: int
    title: str
    channel: str
    description: str = ""


@dataclass
class ImportedName:
    id: int
    title: str
    artist: str
    album: str = ""
    confidence: float = 0.0


def build_prompt(songs: Iterable[ExportedSong]) -> str:
    payload = [
        {
            "id": str(song.id),
            "title": song.title,
            "channel": song.channel,
            "description": (song.description or "")[:200],
        }
        for song in songs
    ]
    return INSTRUCTIONS + json.dumps(payload, indent=2, ensure_ascii=False)


def write_prompt(path: Path, songs: Iterable[ExportedSong]) -> Path:
    path = Path(path)
    path.write_text(build_prompt(songs), encoding="utf-8")
    return path


def parse_reply(text: str) -> list[ImportedName]:
    """Read the chatbot's answer, tolerating fences and extra prose."""
    if not text or not text.strip():
        raise ValueError("There is nothing to import.")

    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        # A single object is also acceptable.
        obj_start = text.find("{")
        obj_end = text.rfind("}")
        if obj_start == -1 or obj_end <= obj_start:
            raise ValueError(
                "No JSON list was found. Paste the reply exactly as the "
                "chatbot gave it."
            )
        text = "[" + text[obj_start:obj_end + 1] + "]"
    else:
        text = text[start:end + 1]

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"The reply is not valid JSON: {exc}") from exc

    if isinstance(data, dict):
        data = data.get("songs") or data.get("results") or [data]
    if not isinstance(data, list):
        raise ValueError("The reply was not a list of songs.")

    results: list[ImportedName] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        try:
            song_id = int(str(raw_id).strip())
        except (TypeError, ValueError):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        try:
            confidence = float(item.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        results.append(
            ImportedName(
                id=song_id,
                title=title,
                artist=str(item.get("artist") or "").strip(),
                album=str(item.get("album") or "").strip(),
                confidence=max(0.0, min(1.0, confidence)),
            )
        )

    if not results:
        raise ValueError("No usable songs were found in the reply.")
    return results


def songs_needing_help(rows) -> list[ExportedSong]:
    """Songs without a confident suggestion."""
    result: list[ExportedSong] = []
    for row in rows:
        track = row.track
        confident = (
            track.smart_confidence is not None
            and track.smart_confidence >= 0.8
        )
        if confident:
            continue
        result.append(
            ExportedSong(
                id=track.id,
                title=track.title,
                channel=track.uploader or "",
                description=track.description or "",
            )
        )
    return result


def default_export_path(folder: Path) -> Optional[Path]:
    folder = Path(folder)
    if not folder.exists():
        return None
    return folder / "songs-for-ai.txt"
