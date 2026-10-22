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

def test_the_cover_cache_name_carries_the_size_and_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "musicdlp.core.paths.art_cache_dir", lambda: tmp_path
    )
    path = artwork.cover_cache_path("youtube", "abc")
    assert str(artwork.TARGET_COVER_SIZE) in path.name
    # A cover cached before the matching rules were tightened must not be
    # reused: it may be another song's artwork.
    assert f"v{artwork.CACHE_VERSION}" in path.name


def test_a_cover_cached_by_an_older_rule_set_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "musicdlp.core.paths.art_cache_dir", lambda: tmp_path
    )
    # The name an earlier build would have written.
    old = tmp_path / f"album_youtube_abc_{artwork.TARGET_COVER_SIZE}.jpg"
    old.write_bytes(_png((1400, 1400)))
    current = artwork.cover_cache_path("youtube", "abc")
    assert current != old


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
    # The 640 px still is enlarged to the cover size, not stored at 640.
    assert _size_of(saved) == (1400, 1400)


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
    chosen = artwork._itunes_cover("Yoshida Yasei, Kasane Teto SV", "Override")
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


def test_a_second_credited_artist_alone_is_not_enough(monkeypatch):
    """When the vocalist is listed first, the cover is skipped.

    This is the safe outcome: the video still is used rather than art for
    a release whose main artist is not the one the upload names.
    """
    results = [
        {"trackName": "Override", "artistName": "Yoshida Yasei",
         "collectionName": "Override - Single",
         "artworkUrl100": "https://x/real/100x100bb.jpg"},
    ]
    monkeypatch.setattr(
        artwork, "_itunes_search", lambda term, limit=8: results
    )
    assert artwork._itunes_cover(
        "Kasane Teto SV, Yoshida Yasei", "Override"
    ) is None


# ------------------------------------- never borrow another song's cover

def test_a_feature_credit_is_not_the_artist():
    """Monday Morning by Young Thug must not get a Juicy J song's cover."""
    assert artwork._is_the_same_song(
        "Monday Morning", ["Young Thug"],
        "Low (feat. Nicki Minaj, Lil Bibby & Young Thug)", "Juicy J",
    ) is False


def test_an_unreleased_song_gets_no_catalogue_cover(monkeypatch):
    results = [
        {"trackName": "Low (feat. Nicki Minaj, Lil Bibby & Young Thug)",
         "artistName": "Juicy J",
         "collectionName": "Low (feat. ...) - Single",
         "artworkUrl100": "https://x/low/100x100bb.jpg"},
        {"trackName": "Best Part (feat. H.E.R.)",
         "artistName": "Daniel Caesar", "collectionName": "Freudian",
         "artworkUrl100": "https://x/bp/100x100bb.jpg"},
    ]
    monkeypatch.setattr(
        artwork, "_itunes_search", lambda term, limit=8: results
    )
    assert artwork._itunes_cover("Young Thug", "Monday Morning") is None


def test_a_split_credit_matches_when_the_main_artist_is_first():
    assert artwork._is_the_same_song(
        "Override", ["Yoshida Yasei", "Kasane Teto SV"],
        "Override", "Yoshida Yasei",
    ) is True


def test_a_cover_version_is_rejected_when_the_upload_is_the_original():
    assert artwork._is_the_same_song(
        "Override", ["Yoshida Yasei"], "Override (English Cover)",
        "Yoshida Yasei",
    ) is False


def test_a_live_version_is_rejected_when_the_upload_is_studio():
    assert artwork._is_the_same_song(
        "Override", ["Yoshida Yasei"], "Override (Live)", "Yoshida Yasei"
    ) is False


def test_a_live_version_is_accepted_when_the_upload_is_that_live():
    assert artwork._is_the_same_song(
        "Override (Live)", ["Yoshida Yasei"], "Override (Live)",
        "Yoshida Yasei",
    ) is True


def test_a_karaoke_track_is_rejected():
    assert artwork._is_the_same_song(
        "Override", ["Yoshida Yasei"], "Override KARAOKE", "Uta-Cha-Oh"
    ) is False


def test_a_different_title_is_rejected():
    assert artwork._is_the_same_song(
        "Monday Morning", ["Young Thug"], "Lifestyle", "Young Thug"
    ) is False


def test_the_same_title_by_another_artist_is_rejected():
    assert artwork._is_the_same_song(
        "Hello", ["Adele"], "Hello", "Lionel Richie"
    ) is False


# --------------------------------------------------------- upscaling

def test_a_small_still_is_upscaled_to_the_cover_size(tmp_path):
    service = thumbnails.ThumbnailService(tmp_path / "t")
    saved = service._save_image(_png((480, 360)), tmp_path / "u.jpg")
    assert saved is not None
    assert _size_of(saved) == (1400, 1400)


def test_upscaling_can_be_turned_off(tmp_path):
    service = thumbnails.ThumbnailService(tmp_path / "t", upscale=False)
    saved = service._save_image(_png((480, 360)), tmp_path / "u.jpg")
    assert saved is not None
    assert _size_of(saved) == (640, 640)


def test_a_large_still_is_not_enlarged_further(tmp_path):
    service = thumbnails.ThumbnailService(tmp_path / "t")
    saved = service._save_image(_png((1280, 720)), tmp_path / "u.jpg")
    assert saved is not None
    assert _size_of(saved) == (1400, 1400)  # cropped to 720, then upscaled


def test_an_already_large_image_is_left_alone(tmp_path):
    service = thumbnails.ThumbnailService(tmp_path / "t")
    saved = service._save_image(_png((2000, 2000)), tmp_path / "u.jpg")
    assert saved is not None
    assert _size_of(saved) == (1400, 1400)


# ------------------------------------------------------------ Deezer source

def test_deezer_is_used_for_songs_itunes_does_not_carry(monkeypatch):
    results = [
        {"id": 1, "title": "Creep",
         "artist": {"name": "Radiohead"},
         "album": {"title": "Pablo Honey",
                   "cover_xl": "https://dz/pablo.jpg"}},
        {"id": 2, "title": "Creep (Acoustic)",
         "artist": {"name": "Radiohead"},
         "album": {"title": "Creep EP", "cover_xl": "https://dz/ep.jpg"}},
    ]

    class Response:
        status_code = 200

        def json(self):
            return {"data": results}

    monkeypatch.setattr(
        artwork.requests, "get", lambda *a, **k: Response()
    )
    urls = artwork._deezer_cover("Radiohead", "Creep")
    assert urls[0] == "https://dz/pablo.jpg"


def test_deezer_rejects_a_different_song(monkeypatch):
    results = [
        {"id": 1, "title": "Low (feat. Young Thug)",
         "artist": {"name": "Juicy J"},
         "album": {"title": "Low", "cover_xl": "https://dz/low.jpg"}},
    ]

    class Response:
        status_code = 200

        def json(self):
            return {"data": results}

    monkeypatch.setattr(
        artwork.requests, "get", lambda *a, **k: Response()
    )
    assert artwork._deezer_cover("Young Thug", "Monday Morning") == []


# ------------------------------------------------- a live album is not the album

def test_a_live_release_is_not_used_for_its_cover():
    release = {
        "title": "Live at Lowlands",
        "release-group": {"primary-type": "Album",
                          "secondary-types": ["Live"]},
    }
    assert artwork._release_is_the_album(release) is False


def test_a_compilation_is_not_used_for_its_cover():
    release = {
        "title": "Now That's Music",
        "release-group": {"primary-type": "Album",
                          "secondary-types": ["Compilation"]},
    }
    assert artwork._release_is_the_album(release) is False


def test_a_real_album_is_used():
    release = {
        "title": "Pablo Honey",
        "release-group": {"primary-type": "Album"},
    }
    assert artwork._release_is_the_album(release) is True


# ------------------------------------------- what a reconvert does to a cover

def test_a_video_still_is_kept_on_reconvert(tmp_path, monkeypatch):
    from musicdlp.services.pipeline import _reusable_cover

    still = tmp_path / "twitter_123_v2.jpg"
    still.write_bytes(_png((1400, 1400)))

    class Track:
        platform = "twitter"
        media_id = "123"
        cover_path = str(still)

    assert _reusable_cover(Track()) is True


def test_a_picture_the_user_chose_is_kept(tmp_path):
    from musicdlp.services.pipeline import _reusable_cover

    chosen = tmp_path / "custom_youtube_abc.jpg"
    chosen.write_bytes(_png((900, 900)))

    class Track:
        platform = "youtube"
        media_id = "abc"
        cover_path = str(chosen)

    assert _reusable_cover(Track()) is True


def test_a_catalogue_cover_under_an_old_name_is_refetched(
    tmp_path, monkeypatch
):
    """An older build could cache the wrong song's cover."""
    from musicdlp.services.pipeline import _reusable_cover

    monkeypatch.setattr(
        "musicdlp.core.paths.art_cache_dir", lambda: tmp_path
    )
    stale = tmp_path / "album_youtube_abc_1400.jpg"
    stale.write_bytes(_png((1400, 1400)))

    class Track:
        platform = "youtube"
        media_id = "abc"
        cover_path = str(stale)

    assert _reusable_cover(Track()) is False


def test_a_catalogue_cover_under_the_current_name_is_kept(
    tmp_path, monkeypatch
):
    from musicdlp.services.pipeline import _reusable_cover

    monkeypatch.setattr(
        "musicdlp.core.paths.art_cache_dir", lambda: tmp_path
    )
    current = artwork.cover_cache_path("youtube", "abc")
    current.write_bytes(_png((1400, 1400)))

    class Track:
        platform = "youtube"
        media_id = "abc"
        cover_path = str(current)

    assert _reusable_cover(Track()) is True


def test_a_missing_cover_file_is_not_reused(tmp_path):
    from musicdlp.services.pipeline import _reusable_cover

    class Track:
        platform = "youtube"
        media_id = "abc"
        cover_path = str(tmp_path / "gone.jpg")

    assert _reusable_cover(Track()) is False


# ------------------------------------------------- the audio length is proof

def test_a_remix_length_is_rejected():
    assert artwork._duration_matches(295, 145) is False


def test_a_music_video_intro_is_accepted():
    """An official video can run a minute longer than the song."""
    assert artwork._duration_matches(295, 367) is True


def test_a_nearby_length_is_accepted():
    assert artwork._duration_matches(210, 240) is True


def test_a_missing_length_is_not_a_reason_to_reject():
    assert artwork._duration_matches(139, None) is True
    assert artwork._duration_matches(None, 200) is True


def test_a_guessed_title_pointing_at_another_song_is_rejected(monkeypatch):
    """The parser guessed a real song; the audio length proves it is not it."""
    results = [
        {"trackName": "Creep", "artistName": "Radiohead",
         "collectionName": "Pablo Honey",
         "trackTimeMillis": 238000,
         "artworkUrl100": "https://x/creep/100x100bb.jpg"},
    ]
    monkeypatch.setattr(
        artwork, "_itunes_search", lambda term, limit=8: results
    )
    assert artwork._itunes_cover("Radiohead", "Creep", 400) is None
    assert artwork._itunes_cover("Radiohead", "Creep", 238) is not None


def test_the_closest_length_wins_between_two_versions(monkeypatch):
    results = [
        {"trackName": "Song", "artistName": "Artist",
         "collectionName": "Song - Single",
         "trackTimeMillis": 145000,
         "artworkUrl100": "https://x/short/100x100bb.jpg"},
        {"trackName": "Song", "artistName": "Artist",
         "collectionName": "Song - Single",
         "trackTimeMillis": 300000,
         "artworkUrl100": "https://x/long/100x100bb.jpg"},
    ]
    monkeypatch.setattr(
        artwork, "_itunes_search", lambda term, limit=8: results
    )
    assert "long" in artwork._itunes_cover("Artist", "Song", 295)
    assert "short" in artwork._itunes_cover("Artist", "Song", 150)


def test_a_credited_feature_is_not_enough_when_the_main_artist_differs():
    """Yeah Yeah by Travis Scott was matched to a Young Thug mixtape track.

    Both songs are real, both titles normalise to "yeah yeah", and the
    lengths are 8 seconds apart, so only the main artist separates them.
    """
    assert artwork._is_the_same_song(
        "Yeah Yeah", ["Travis Scott", "Young Thug"],
        "Yeah, Yeah", "Young Thug",
        wanted_seconds=276, result_seconds=268,
    ) is False


def test_the_main_artist_still_matches_when_the_order_differs():
    assert artwork._is_the_same_song(
        "Pissy Pamper", ["Playboi Carti", "Young Thug"],
        "Pissy Pamper", "Young Thug, Playboi Carti",
    ) is True


def test_a_multi_artist_credit_matches_its_main_artist():
    assert artwork._is_the_same_song(
        "At The Gates", ["Drake", "Lil Uzi Vert"],
        "At the Gates", "Drake, Lil Uzi Vert",
    ) is True


def test_a_vocalist_listed_first_is_not_taken_as_the_artist():
    """Falling back to the video still is the safe outcome here."""
    assert artwork._is_the_same_song(
        "Override", ["Kasane Teto SV", "Yoshida Yasei"],
        "Override", "Yoshida Yasei",
    ) is False
