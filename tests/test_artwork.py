"""Cover art quality: bigger sources, no blurry stretching."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from musicdlp.services import artwork, thumbnails


def _png(size: tuple[int, int], colour=(120, 80, 40)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


def _size_of(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


# ------------------------------------------------------------------ sources

def test_the_itunes_url_is_rewritten_to_the_largest_cover():
    url = "https://is1-ssl.mzstatic.com/image/thumb/Music/a/b/100x100bb.jpg"
    assert artwork._large_artwork(url).endswith("3000x3000bb.jpg")


def test_an_empty_itunes_url_stays_empty():
    assert artwork._large_artwork("") == ""


def test_cover_art_archive_is_asked_for_a_large_image():
    assert "front-1200" in artwork.COVER_ARCHIVE_URL


def test_the_youtube_fallback_list_starts_with_the_largest():
    assert thumbnails.youtube_fallbacks("abc")[0].endswith("maxresdefault.jpg")


# --------------------------------------------------------------- saving

def test_a_small_cover_is_never_stretched_beyond_its_own_size(tmp_path):
    saved = artwork._save_square(_png((900, 900)), tmp_path / "a.jpg")
    assert saved is not None
    assert _size_of(saved) == (900, 900)


def test_a_very_small_cover_is_lifted_to_spotify_minimum(tmp_path):
    saved = artwork._save_square(_png((360, 360)), tmp_path / "b.jpg")
    assert saved is not None
    assert _size_of(saved) == (artwork.MIN_STORED_SIZE,) * 2


def test_a_huge_cover_is_capped_at_the_stored_size(tmp_path):
    saved = artwork._save_square(_png((3000, 3000)), tmp_path / "c.jpg")
    assert saved is not None
    assert _size_of(saved) == (artwork.TARGET_COVER_SIZE,) * 2


def test_a_wide_image_is_cropped_square(tmp_path):
    saved = artwork._save_square(_png((1280, 720)), tmp_path / "d.jpg")
    assert saved is not None
    width, height = _size_of(saved)
    assert width == height
    assert width == 720


def test_the_placeholder_square_is_refused(tmp_path):
    """YouTube answers a missing size with a 120x90 grey square."""
    assert artwork._save_square(_png((120, 90)), tmp_path / "e.jpg") is None


def test_the_thumbnail_placeholder_is_refused(tmp_path):
    service = thumbnails.ThumbnailService(tmp_path / "thumbs")
    assert service._save_image(_png((120, 90)), tmp_path / "f.jpg") is None


# ----------------------------------------------------------------- caching

def test_the_cover_cache_name_carries_the_size(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "musicdlp.core.paths.art_cache_dir", lambda: tmp_path
    )
    path = artwork.cover_cache_path("youtube", "abc")
    assert str(artwork.TARGET_COVER_SIZE) in path.name


def test_an_old_thumbnail_cache_is_not_reused(tmp_path):
    service = thumbnails.ThumbnailService(tmp_path / "thumbs")
    assert f"v{thumbnails.CACHE_VERSION}" in service.cached_path("y", "abc").name


def test_a_placeholder_falls_through_to_the_next_size(tmp_path, monkeypatch):
    """A missing maxres must not be saved as a cover."""
    service = thumbnails.ThumbnailService(tmp_path / "thumbs")

    class Response:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

    sizes = {"big": _png((120, 90)), "good": _png((640, 480))}

    def fake_get(url, **kwargs):
        return Response(sizes["big" if "maxres" in url else "good"])

    monkeypatch.setattr(thumbnails.requests, "get", fake_get)
    saved = service.fetch(
        "youtube", "abc", "https://x/maxresdefault.jpg",
        ["https://x/sddefault.jpg"],
    )
    assert saved is not None
    assert _size_of(saved) == (640, 640)


def test_a_cached_cover_is_returned_without_a_download(tmp_path, monkeypatch):
    destination = tmp_path / "cached.jpg"
    destination.write_bytes(_png((900, 900)))

    def boom(*args, **kwargs):
        raise AssertionError("should not have downloaded")

    monkeypatch.setattr(artwork.requests, "get", boom)
    found = artwork.find_cover("A", "T", destination)
    assert found == destination


# ------------------------------------------------------- choosing a result

def test_artist_credit_is_split_into_names():
    assert artwork._artist_parts("Kasane Teto SV, Yoshida Yasei") == [
        "Kasane Teto SV", "Yoshida Yasei"
    ]
    assert artwork._artist_parts("Drake feat. Lil Uzi Vert") == [
        "Drake", "Lil Uzi Vert"
    ]
    assert artwork._artist_parts("") == []


def test_a_live_arrangement_never_beats_the_actual_single(monkeypatch):
    """Searching a long artist credit returns the live album first."""
    results = [
        {"trackName": "OVERRIDE (Teto Live 2025 -live arrange-)",
         "artistName": "Yoshida Yasei, Kasane Teto & Asa",
         "collectionName": "TetoLive2025 Live Compilation",
         "artworkUrl100": "https://x/live/100x100bb.jpg"},
        {"trackName": "Override",
         "artistName": "Yoshida Yasei",
         "collectionName": "Override - Single",
         "artworkUrl100": "https://x/single/100x100bb.jpg"},
    ]
    monkeypatch.setattr(
        artwork, "_itunes_search", lambda term, limit=8: results
    )
    chosen = artwork._itunes_cover("Kasane Teto SV, Yoshida Yasei", "Override")
    assert "single" in chosen


def test_a_karaoke_version_is_never_chosen(monkeypatch):
    results = [
        {"trackName": "Override KARAOKE Original by Yoshida Yasei",
         "artistName": "Uta-Cha-Oh",
         "collectionName": "Override KARAOKE - Single",
         "artworkUrl100": "https://x/karaoke/100x100bb.jpg"},
        {"trackName": "Override", "artistName": "Yoshida Yasei",
         "collectionName": "Override - Single",
         "artworkUrl100": "https://x/real/100x100bb.jpg"},
    ]
    monkeypatch.setattr(
        artwork, "_itunes_search", lambda term, limit=8: results
    )
    assert "real" in artwork._itunes_cover("Yoshida Yasei", "Override")


def test_the_single_beats_an_english_cover(monkeypatch):
    results = [
        {"trackName": "Override (English Cover)",
         "artistName": "Yoshida Yasei & Will Stetson",
         "collectionName": "Override (English Cover) - Single",
         "artworkUrl100": "https://x/cover/100x100bb.jpg"},
        {"trackName": "Override", "artistName": "Yoshida Yasei",
         "collectionName": "Override - Single",
         "artworkUrl100": "https://x/real/100x100bb.jpg"},
    ]
    monkeypatch.setattr(
        artwork, "_itunes_search", lambda term, limit=8: results
    )
    assert "real" in artwork._itunes_cover("Yoshida Yasei", "Override")


def test_a_credited_artist_further_down_the_credit_still_matches(monkeypatch):
    """The single may be filed under the producer, not the vocalist."""
    results = [
        {"trackName": "Override", "artistName": "Yoshida Yasei",
         "collectionName": "Override - Single",
         "artworkUrl100": "https://x/real/100x100bb.jpg"},
    ]
    monkeypatch.setattr(
        artwork, "_itunes_search", lambda term, limit=8: results
    )
    assert artwork._itunes_cover("Kasane Teto SV, Yoshida Yasei", "Override")
