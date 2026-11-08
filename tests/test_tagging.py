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


def test_an_atomic_retag_leaves_no_temp_and_writes_the_tags(silent_mp3):
    from musicdlp.services.tagging import embed_tags_atomic

    assert embed_tags_atomic(
        silent_mp3, title="Safe", artist="Artist", album="Album", track=2
    )

    assert read_tags(silent_mp3)["title"] == "Safe"
    assert not list(silent_mp3.parent.glob("*.mdlp-tmp*"))
    assert not list(silent_mp3.parent.glob(".*.mdlp-tmp*"))


def test_a_failed_atomic_retag_keeps_the_original_file(monkeypatch, silent_mp3):
    from musicdlp.services import tagging

    before = silent_mp3.read_bytes()

    def broken(*_args, **_kwargs):
        return False

    monkeypatch.setattr(tagging, "embed_tags", broken)
    assert tagging.embed_tags_atomic(silent_mp3, title="Never written") is False

    assert silent_mp3.read_bytes() == before
    assert not list(silent_mp3.parent.glob(".*.mdlp-tmp*"))


def test_a_failed_cover_replacement_keeps_the_original_file(
    monkeypatch, silent_mp3, cover
):
    from musicdlp.services import tagging

    before = silent_mp3.read_bytes()
    monkeypatch.setattr(tagging, "replace_cover", lambda *_a, **_k: False)

    assert tagging.replace_cover_atomic(silent_mp3, cover) is False
    assert silent_mp3.read_bytes() == before


def test_read_tags_reports_the_files_own_length(silent_mp3):
    from musicdlp.services.tagging import read_tags

    assert read_tags(silent_mp3)["length"] == 1
