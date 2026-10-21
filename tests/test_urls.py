"""Link classification and splitting."""

from musicdlp.services.urls import (
    KIND_ITEM,
    KIND_PLAYLIST,
    PLATFORM_OTHER,
    PLATFORM_TWITTER,
    PLATFORM_YOUTUBE,
    classify_url,
    platform_key,
    split_links,
)


def test_playlist_url_stays_a_playlist():
    info = classify_url("https://www.youtube.com/playlist?list=PLabc123")
    assert info.platform == PLATFORM_YOUTUBE
    assert info.kind == KIND_PLAYLIST
    assert info.media_id == "PLabc123"
    assert info.url == "https://www.youtube.com/playlist?list=PLabc123"


def test_youtube_video():
    info = classify_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert info.kind == KIND_ITEM
    assert info.media_id == "dQw4w9WgXcQ"
    assert info.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_watch_url_with_playlist_is_a_video():
    info = classify_url(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123"
    )
    assert info.kind == KIND_ITEM
    assert info.media_id == "dQw4w9WgXcQ"


def test_watch_url_with_radio_mix_is_a_single_video():
    """A "&list=RD..." radio link used to expand an endless queue."""
    info = classify_url(
        "https://www.youtube.com/watch?v=gjRoRUM93eE&list=RDgjRoRUM93eE"
        "&start_radio=1"
    )
    assert info.kind == KIND_ITEM
    assert info.media_id == "gjRoRUM93eE"
    assert info.url == "https://www.youtube.com/watch?v=gjRoRUM93eE"


def test_radio_playlist_url_is_not_treated_as_a_playlist():
    assert classify_url(
        "https://www.youtube.com/playlist?list=RDgjRoRUM93eE"
    ) is None
    info = classify_url(
        "https://www.youtube.com/playlist?list=RDgjRoRUM93eE&v=gjRoRUM93eE"
    )
    assert info.kind == KIND_ITEM
    assert info.media_id == "gjRoRUM93eE"


def test_youtu_be_and_shorts():
    for url in (
        "https://youtu.be/dQw4w9WgXcQ?t=10",
        "https://youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
    ):
        info = classify_url(url)
        assert info.kind == KIND_ITEM, url
        assert info.media_id == "dQw4w9WgXcQ", url
        assert info.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_schemeless_link():
    info = classify_url("youtu.be/dQw4w9WgXcQ")
    assert info.media_id == "dQw4w9WgXcQ"
    assert info.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_x_status():
    info = classify_url("https://x.com/someuser/status/1234567890")
    assert info.platform == PLATFORM_TWITTER
    assert info.kind == KIND_ITEM
    assert info.media_id == "1234567890"
    assert info.url == "https://x.com/someuser/status/1234567890"


def test_twitter_status_with_query_and_photo_suffix():
    info = classify_url("https://twitter.com/someuser/status/123/photo/1?s=20")
    assert info.platform == PLATFORM_TWITTER
    assert info.media_id == "123"
    assert info.url == "https://x.com/someuser/status/123"


def test_twitter_web_status():
    info = classify_url("https://twitter.com/i/web/status/910031516746514432")
    assert info.media_id == "910031516746514432"
    assert info.url == "https://x.com/i/status/910031516746514432"


def test_twitter_proxy_is_rewritten():
    info = classify_url("https://vxtwitter.com/someuser/status/123")
    assert info.platform == PLATFORM_TWITTER
    assert info.media_id == "123"
    assert info.url.startswith("https://x.com/")


def test_twitter_profile_is_not_supported():
    assert classify_url("https://x.com/someuser") is None


def test_other_site_is_accepted():
    info = classify_url("https://vimeo.com/12345")
    assert info.platform == PLATFORM_OTHER
    assert info.kind == KIND_ITEM


def test_invalid_strings():
    assert classify_url("not a link") is None
    assert classify_url("") is None
    assert classify_url("ftp://example.com/file") is None


def test_split_links_dedupes_and_ignores_text():
    text = (
        "watch this https://youtu.be/dQw4w9WgXcQ, "
        "and https://x.com/user/status/123\n"
        "https://youtu.be/dQw4w9WgXcQ"
    )
    assert split_links(text) == [
        "https://youtu.be/dQw4w9WgXcQ",
        "https://x.com/user/status/123",
    ]


def test_split_links_empty():
    assert split_links("") == []
    assert split_links("hello world") == []


def test_platform_key():
    assert platform_key("Youtube") == "youtube"
    assert platform_key("Twitter") == "twitter"
    assert platform_key("Vimeo") == "vimeo"
    assert platform_key(None, "other") == "other"
