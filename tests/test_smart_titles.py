"""Smart title parsing against realistic upload titles."""

from musicdlp.services.smart_titles import (
    AUTO_THRESHOLD,
    clean_channel,
    is_collection_channel,
    suggest,
)

KNOWN = {"Young Thug", "Drake", "Lil Uzi Vert"}


def test_artist_dash_title():
    result = suggest(
        "DRAKE - AT THE GATES FT LIL UZI VERT (OG) (CDQ QUALITY)",
        channel="MuddyBeats",
        known_artists=KNOWN,
    )
    assert result.title == "At The Gates"
    assert result.artist == "Drake, Lil Uzi Vert"
    assert result.confidence >= AUTO_THRESHOLD


def test_featured_artist_moves_to_artist_field():
    result = suggest("Kendrick Lamar - HUMBLE. (feat. Someone)", channel="Music")
    assert result.title == "Humble."
    assert "Someone" in result.artist


def test_topic_channel_is_the_artist():
    result = suggest("Yoru ni Kakeru", channel="YOASOBI - Topic")
    assert result.title == "Yoru ni Kakeru"
    assert result.artist == "YOASOBI"
    assert result.confidence >= 0.9


def test_upload_noise_is_removed():
    result = suggest(
        "Plastic Love (Official Music Video) [HD]",
        channel="Mariya Takeuchi",
    )
    assert "Official" not in result.title
    assert "HD" not in result.title
    assert result.title == "Plastic Love"


def test_japanese_cover_markers_are_removed():
    result = suggest(
        "【歌ってみた】シャルル / バルーン covered by 森カリオペ",
        channel="Mori Calliope",
    )
    assert "歌ってみた" not in result.title


def test_collection_channel_prefix_is_dropped_and_artist_found():
    result = suggest(
        "THUGGERS VAULT ひ - NEW LEAK MONDAY MORNING (2018) YOUNG THUG",
        channel="THUGGERS VAULT ひ",
        known_artists=KNOWN,
    )
    assert result.title == "Monday Morning", result.title
    assert result.artist == "Young Thug", result.artist


def test_collection_channel_with_artist_first():
    result = suggest(
        "THUGGERS VAULT ひ - NEW LEAK  YOUNG THUG  HOW COME (SMF ERA)  "
        "PROD. PIERRE   JOIN THE DIS...",
        channel="THUGGERS VAULT ひ",
        known_artists=KNOWN,
    )
    assert result.title == "How Come", result.title
    assert result.artist == "Young Thug", result.artist


def test_unknown_artist_without_separator_is_not_applied():
    """No separator and no known name: suggest, but stay low confidence."""
    result = suggest(
        "THUGGERS VAULT ひ - NEW LEAK MONDAY MORNING (2018) YOUNG THUG",
        channel="THUGGERS VAULT ひ",
        known_artists=set(),
    )
    assert result.confidence < AUTO_THRESHOLD
    assert not result.confident


def test_description_credits_win():
    result = suggest(
        "some messy upload",
        channel="whatever",
        description="Artist: Frank Ocean\nTrack: Pink + White\n",
    )
    assert result.title == "Pink + White"
    assert result.artist == "Frank Ocean"
    assert result.confidence >= 0.9


def test_plain_title_is_left_alone():
    result = suggest("Me at the zoo", channel="jawed")
    assert result.title == "Me at the zoo"
    assert result.artist == ""
    assert not result.confident


def test_year_and_era_tags_removed():
    result = suggest("Song Name (2018) (SMF ERA)", channel="Someone")
    assert "2018" not in result.title


def test_clean_channel():
    assert clean_channel("YOASOBI - Topic") == ("YOASOBI", True)
    assert clean_channel("DrakeVEVO") == ("Drake", False)
    assert clean_channel("Some Artist") == ("Some Artist", False)


def test_collection_channel_detection():
    assert is_collection_channel("THUGGERS VAULT ひ")
    assert is_collection_channel("Lyrics Archive")
    assert not is_collection_channel("Young Thug")
    assert not is_collection_channel("Mariya Takeuchi")
