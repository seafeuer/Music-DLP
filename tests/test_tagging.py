"""Tag format: Spotify needs ID3v2.3 with an embedded picture."""

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from musicdlp.services.tagging import embed_tags, read_tags


@pytest.fixture()
def silent_mp3(tmp_path):
    path = tmp_path / "song.mp3"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=1", "-c:a", "libmp3lame",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return path


@pytest.fixture()
def cover(tmp_path):
    path = tmp_path / "cover.jpg"
    Image.new("RGB", (640, 640), (200, 40, 90)).save(path, "JPEG")
    return path


def test_mp3_tags_are_id3v23_with_picture(silent_mp3, cover):
    assert embed_tags(
        silent_mp3,
        title="Song",
        artist="Artist",
        album="Album",
        track=3,
        cover_path=cover,
    )

    header = silent_mp3.read_bytes()[:5]
    assert header[:3] == b"ID3"
    assert header[3] == 3, "Spotify reads artwork reliably only from ID3v2.3"

    tags = read_tags(silent_mp3)
    assert tags["title"] == "Song"
    assert tags["artist"] == "Artist"
    assert tags["album"] == "Album"
    assert tags["track"] == "3"
    assert tags["has_cover"] is True


def test_retagging_keeps_a_single_picture(silent_mp3, cover):
    for _ in range(3):
        embed_tags(
            silent_mp3, title="Song", artist="Artist", album="Album",
            track=1, cover_path=cover,
        )

    from mutagen.id3 import ID3

    assert len(ID3(str(silent_mp3)).getall("APIC")) == 1
    assert silent_mp3.read_bytes()[3] == 3


def test_tags_without_cover_are_still_valid(silent_mp3):
    assert embed_tags(silent_mp3, title="Bare", artist="Nobody")
    tags = read_tags(silent_mp3)
    assert tags["title"] == "Bare"
    assert tags["has_cover"] is False
    assert silent_mp3.read_bytes()[3] == 3
