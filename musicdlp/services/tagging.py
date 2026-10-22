"""Embed title/artist/album/track tags and cover art."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("musicdlp.tagging")


def embed_tags(
    path: Path,
    *,
    title: str,
    artist: str = "",
    album: str = "",
    track: Optional[int] = None,
    cover_path: Optional[Path] = None,
) -> bool:
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            return _embed_mp3(path, title, artist, album, track, cover_path)
        if suffix in (".m4a", ".mp4", ".aac"):
            return _embed_m4a(path, title, artist, album, track, cover_path)
        logger.warning("No tag writer for %s", suffix)
        return False
    except Exception as exc:
        logger.error("Tagging failed for %s: %s", path.name, exc)
        return False


def _embed_mp3(
    path: Path,
    title: str,
    artist: str,
    album: str,
    track: Optional[int],
    cover_path: Optional[Path],
) -> bool:
    """Write ID3v2.3 tags with UTF-16 text.

    Spotify's Local Files scanner reads ID3v2.4 text frames but frequently
    drops the embedded picture from them. ID3v2.3 with UTF-16 is the
    combination that Spotify, Windows Explorer, and other players all read
    reliably.
    """
    from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TRCK

    try:
        tags = ID3(str(path))
    except Exception:
        tags = ID3()

    tags.delall("TIT2")
    tags.delall("TPE1")
    tags.delall("TALB")
    tags.delall("TRCK")
    tags.delall("APIC")

    tags.add(TIT2(encoding=1, text=title))
    if artist:
        tags.add(TPE1(encoding=1, text=artist))
    if album:
        tags.add(TALB(encoding=1, text=album))
    if track:
        tags.add(TRCK(encoding=1, text=str(track)))

    if cover_path and Path(cover_path).exists():
        data = Path(cover_path).read_bytes()
        tags.add(APIC(encoding=1, mime="image/jpeg", type=3,
                      desc="Cover", data=data))

    tags.save(str(path), v2_version=3)
    return True


def _embed_m4a(
    path: Path,
    title: str,
    artist: str,
    album: str,
    track: Optional[int],
    cover_path: Optional[Path],
) -> bool:
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()

    tags = audio.tags
    tags["\xa9nam"] = [title]
    if artist:
        tags["\xa9ART"] = [artist]
    else:
        tags.pop("\xa9ART", None)
    if album:
        tags["\xa9alb"] = [album]
    else:
        tags.pop("\xa9alb", None)

    if track:
        tags["trkn"] = [(int(track), 0)]
    else:
        tags.pop("trkn", None)

    if cover_path and Path(cover_path).exists():
        data = Path(cover_path).read_bytes()
        tags["covr"] = [MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)]
    else:
        tags.pop("covr", None)

    audio.save()
    return True


def replace_cover(path: Path, cover_path: Path) -> bool:
    """Swap the embedded picture without touching the audio.

    Used to correct a cover on a file that already exists. The audio
    stream is not decoded or re-encoded, so this is instant and the sound
    is bit-for-bit what it was.
    """
    path = Path(path)
    cover_path = Path(cover_path)
    if not path.exists() or not cover_path.exists():
        return False
    data = cover_path.read_bytes()
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            from mutagen.id3 import APIC, ID3

            try:
                tags = ID3(str(path))
            except Exception:
                tags = ID3()
            tags.delall("APIC")
            tags.add(
                APIC(encoding=1, mime="image/jpeg", type=3, desc="Cover",
                     data=data)
            )
            tags.save(str(path), v2_version=3)
            return True
        if suffix in (".m4a", ".mp4", ".aac"):
            from mutagen.mp4 import MP4, MP4Cover

            audio = MP4(str(path))
            if audio.tags is None:
                audio.add_tags()
            audio.tags["covr"] = [
                MP4Cover(data, imageformat=MP4Cover.FORMAT_JPEG)
            ]
            audio.save()
            return True
    except Exception as exc:
        logger.error("Could not replace the cover on %s: %s", path.name, exc)
        return False
    logger.warning("No cover writer for %s", suffix)
    return False


def embedded_cover_size(path: Path) -> Optional[tuple[int, int]]:
    """The pixel size of the picture inside a file, if it has one."""
    from io import BytesIO

    from PIL import Image

    path = Path(path)
    try:
        if path.suffix.lower() == ".mp3":
            from mutagen.id3 import ID3

            frames = ID3(str(path)).getall("APIC")
            data = frames[0].data if frames else None
        else:
            from mutagen.mp4 import MP4

            tags = MP4(str(path)).tags or {}
            covers = tags.get("covr") or []
            data = bytes(covers[0]) if covers else None
        if not data:
            return None
        with Image.open(BytesIO(data)) as image:
            return image.size
    except Exception:
        return None


def read_tags(path: Path) -> dict[str, object]:
    """Small helper used by tests and diagnostics."""
    path = Path(path)
    result: dict[str, object] = {}
    if path.suffix.lower() == ".mp3":
        from mutagen.id3 import ID3

        tags = ID3(str(path))
        result["title"] = str(tags.get("TIT2", ""))
        result["artist"] = str(tags.get("TPE1", ""))
        result["album"] = str(tags.get("TALB", ""))
        result["track"] = str(tags.get("TRCK", ""))
        result["has_cover"] = bool(tags.getall("APIC"))
    elif path.suffix.lower() in (".m4a", ".mp4", ".aac"):
        from mutagen.mp4 import MP4

        audio = MP4(str(path))
        tags = audio.tags or {}
        result["title"] = (tags.get("\xa9nam") or [""])[0]
        result["artist"] = (tags.get("\xa9ART") or [""])[0]
        result["album"] = (tags.get("\xa9alb") or [""])[0]
        result["track"] = (tags.get("trkn") or [(0, 0)])[0][0]
        result["has_cover"] = bool(tags.get("covr"))
    return result
