"""Matching a Music DLP folder against Spotify's Local Files index."""

from musicdlp.services.spotify_sync import (
    FolderTrack,
    LocalTrack,
    build_plan,
    folder_tracks_from_rows,
)


def _local(uri, title, artist="", album="Singles", seconds=None):
    return LocalTrack(
        uri=uri, title=title, artist=artist, album=album, seconds=seconds
    )


def test_a_song_that_is_already_in_the_playlist_is_not_added_again():
    folder = [FolderTrack(1, "How Come", "Young Thug", 140)]
    local = [_local("spotify:local:A:S:How+Come:140", "How Come", "Young Thug", seconds=140)]
    plan = build_plan(folder, local, ["spotify:local:A:S:How+Come:140"])

    assert plan.to_add == []
    assert plan.to_remove == []
    assert plan.unmatched == []


def test_a_missing_song_is_added():
    folder = [FolderTrack(1, "How Come", "Young Thug", 140)]
    local = [_local("spotify:local:A:S:How+Come:140", "How Come", "Young Thug", seconds=140)]
    plan = build_plan(folder, local, [])

    assert plan.to_add == ["spotify:local:A:S:How+Come:140"]
    assert plan.to_remove == []


def test_a_song_no_longer_in_the_folder_is_removed():
    folder = [FolderTrack(1, "How Come", "Young Thug", 140)]
    local = [
        _local("spotify:local:A:S:How+Come:140", "How Come", "Young Thug", seconds=140),
        _local("spotify:local:A:S:Other:200", "Other", "Someone", seconds=200),
    ]
    plan = build_plan(
        folder,
        local,
        ["spotify:local:A:S:How+Come:140", "spotify:local:A:S:Other:200"],
        local_files_complete=True,
    )

    assert plan.to_remove == ["spotify:local:A:S:Other:200"]


def test_a_real_spotify_track_is_removed_by_an_exact_mirror():
    folder = [FolderTrack(1, "How Come", "Young Thug", 140)]
    local = [_local("spotify:local:A:S:How+Come:140", "How Come", "Young Thug", seconds=140)]
    plan = build_plan(
        folder, local, ["spotify:track:abc123"], local_files_complete=True
    )

    assert plan.to_remove == ["spotify:track:abc123"]


def test_an_empty_local_index_never_removes_anything():
    """Spotify can answer with an empty index while it is scanning."""
    folder = [FolderTrack(1, "How Come", "Young Thug", 140)]
    playlist = ["spotify:local:A:S:How+Come:140", "spotify:track:hand-picked"]

    plan = build_plan(folder, [], playlist, local_files_complete=True)

    assert plan.to_remove == []
    assert plan.safe_to_remove is False
    assert "incomplete" in plan.remove_blocked_reason.lower()


def test_an_incomplete_local_scan_never_removes_anything():
    folder = [FolderTrack(1, "How Come", "Young Thug", 140)]
    local = [_local("spotify:local:A:S:Other:200", "Other", "Someone", seconds=200)]

    plan = build_plan(
        folder,
        local,
        ["spotify:local:A:S:Other:200"],
        local_files_complete=False,
    )

    assert plan.to_remove == []
    assert plan.safe_to_remove is False


def test_an_empty_folder_never_empties_the_playlist():
    playlist = ["spotify:local:A:S:How+Come:140", "spotify:track:hand-picked"]

    plan = build_plan([], [], playlist, local_files_complete=True)

    assert plan.to_remove == []
    assert plan.safe_to_remove is False
    assert "no songs" in plan.remove_blocked_reason.lower()


def test_a_complete_scan_with_no_matches_leaves_the_playlist_alone():
    folder = [FolderTrack(1, "How Come", "Young Thug", 140)]
    local = [_local("spotify:local:A:S:Elsewhere:200", "Elsewhere", "Someone", seconds=200)]
    playlist = ["spotify:local:A:S:How+Come:141"]

    plan = build_plan(folder, local, playlist, local_files_complete=True)

    assert plan.to_remove == []
    assert plan.safe_to_remove is False
    assert "matched" in plan.remove_blocked_reason.lower()


def test_additions_still_work_when_removals_are_held_back():
    folder = [FolderTrack(1, "How Come", "Young Thug", 140)]
    local = [_local("spotify:local:A:S:How+Come:140", "How Come", "Young Thug", seconds=140)]

    plan = build_plan(folder, local, [], local_files_complete=False)

    assert plan.to_add == ["spotify:local:A:S:How+Come:140"]
    assert plan.to_remove == []


def test_a_song_with_no_local_file_is_reported_not_added():
    folder = [FolderTrack(7, "Not Converted", "Nobody", 100)]
    plan = build_plan(folder, [], [])

    assert plan.to_add == []
    assert [t.track_id for t in plan.unmatched] == [7]


def test_a_cover_with_a_different_artist_does_not_match():
    folder = [FolderTrack(1, "Stay", "Jasmine Thompson", 200)]
    local = [_local("spotify:local:X:S:Stay:200", "Stay", "Rihanna", seconds=200)]

    plan = build_plan(folder, local, [])
    assert plan.to_add == []
    assert len(plan.unmatched) == 1


def test_a_length_far_off_does_not_match():
    folder = [FolderTrack(1, "Stay", "Someone", 200)]
    local = [_local("spotify:local:X:S:Stay:999", "Stay", "Someone", seconds=999)]

    plan = build_plan(folder, local, [])
    assert plan.unmatched


def test_the_closer_length_wins_when_two_copies_share_a_title():
    folder = [FolderTrack(1, "Intro", "Artist", 61)]
    local = [
        _local("spotify:local:X:S:Intro:60", "Intro", "Artist", seconds=60),
        _local("spotify:local:X:S:Intro:120", "Intro", "Artist", seconds=120),
    ]
    plan = build_plan(folder, local, [])

    assert plan.to_add == ["spotify:local:X:S:Intro:60"]


def test_punctuation_and_accents_do_not_prevent_a_match():
    folder = [FolderTrack(1, "Déjà Vu!", "Beyoncé", 240)]
    local = [_local("spotify:local:B:S:Deja+Vu:240", "Deja Vu", "Beyonce", seconds=240)]

    plan = build_plan(folder, local, [])
    assert plan.to_add == ["spotify:local:B:S:Deja+Vu:240"]


def test_a_song_reached_through_two_folder_links_is_added_once():
    folder = [
        FolderTrack(1, "How Come", "Young Thug", 140),
        FolderTrack(1, "How Come", "Young Thug", 140),
    ]
    local = [_local("spotify:local:A:S:How+Come:140", "How Come", "Young Thug", seconds=140)]
    plan = build_plan(folder, local, [])

    assert plan.to_add == ["spotify:local:A:S:How+Come:140"]


def test_local_track_parses_spotify_duration_shape():
    local = LocalTrack.from_panel(
        {
            "uri": "spotify:local:A:S:T:140",
            "name": "T",
            "artists": [{"name": "A"}, {"name": "B"}],
            "album": {"name": "S"},
            "duration": {"milliseconds": 140500},
        }
    )
    assert local.seconds == 140
    assert local.artist == "A, B"
    assert local.album == "S"


def test_local_track_parses_a_plain_millisecond_duration():
    local = LocalTrack.from_panel(
        {"uri": "u", "name": "T", "artists": [], "duration": 140000}
    )
    assert local.seconds == 140


def test_folder_tracks_from_rows_reads_display_values():
    class FakeTrack:
        id = 4
        display_title = "Song"
        display_artist = "Artist"
        duration = 90

    class FakeRow:
        track = FakeTrack()

    tracks = folder_tracks_from_rows([FakeRow()])
    assert tracks == [FolderTrack(4, "Song", "Artist", 90)]


def test_two_songs_with_identical_tags_are_reported_before_syncing():
    """M5: Spotify keys a local file by tags, so one entry would vanish."""
    folder = [
        FolderTrack(1, "Same Name", "Same Artist", 100),
        FolderTrack(2, "Same Name", "Same Artist", 100),
    ]
    local = [
        _local(
            "spotify:local:Same+Artist:Singles:Same+Name:100",
            "Same Name", "Same Artist", seconds=100,
        )
    ]
    plan = build_plan(folder, local, [], local_files_complete=True)

    assert len(plan.duplicate_tags) == 1
    assert plan.duplicate_tags[0]["title"] == "Same Name"
    assert plan.duplicate_tags[0]["track_ids"] == [1, 2]
    assert plan.summary()["duplicate_tags"] == 1
    # Only one URI can be added, which is exactly why the warning matters.
    assert plan.to_add == ["spotify:local:Same+Artist:Singles:Same+Name:100"]


def test_distinct_songs_do_not_raise_the_duplicate_warning():
    folder = [
        FolderTrack(1, "One", "Artist", 100),
        FolderTrack(2, "Two", "Artist", 200),
    ]
    local = [
        _local("spotify:local:Artist:S:One:100", "One", "Artist", seconds=100),
        _local("spotify:local:Artist:S:Two:200", "Two", "Artist", seconds=200),
    ]
    plan = build_plan(folder, local, [], local_files_complete=True)
    assert plan.duplicate_tags == []


def test_a_local_uri_encodes_the_parts_so_colons_do_not_split_the_link():
    from musicdlp.services.spotify_sync import local_uri

    uri = local_uri("Song: Part 2", "An Artist", "An Album", 245)

    assert uri == (
        "spotify:local:An+Artist:An+Album:Song%3A+Part+2:245"
    )


def test_a_local_uri_is_full_seconds_and_never_negative():
    from musicdlp.services.spotify_sync import local_uri

    assert local_uri("Song", "A", "B", 245.9).endswith(":245")
    assert local_uri("Song", "A", "B", None).endswith(":0")
    assert local_uri("Song", "A", "B", -4).endswith(":0")


def test_a_search_uri_is_used_before_the_song_reaches_spotify():
    from musicdlp.services.spotify_sync import search_uri

    assert search_uri("Song A") == "spotify:search:Song%20A"


def test_a_local_uri_uses_the_album_the_file_was_tagged_with():
    from musicdlp.core.models import Track
    from musicdlp.services.spotify_sync import track_local_uri

    track = Track(
        id=1, platform="youtube", media_id="m", url="", source_index=None,
        title="Song", uploader="Artist", duration=180, thumbnail_url=None,
        description=None, status="converted", error=None, meta_title="Song",
        meta_artist="Artist", meta_album=None, meta_track=None, raw_path=None,
        mp3_path=None, m4a_path=None, cover_path=None, downloaded_at=None,
        converted_at=None, synced_at=None,
    )

    class Playlist:
        kind = "playlist"
        title = "My Playlist"

    class Video:
        kind = "item"
        title = "A video"

    assert track_local_uri(track, Playlist()).endswith(":My+Playlist:Song:180")
    assert track_local_uri(track, Video()).endswith(":Singles:Song:180")
    assert track_local_uri(track).endswith(":Singles:Song:180")


def test_an_album_read_from_the_file_wins_over_the_current_source():
    from musicdlp.core.models import Track
    from musicdlp.services.spotify_sync import track_local_uri

    track = Track(
        id=1, platform="youtube", media_id="m", url="", source_index=None,
        title="Song", uploader="Artist", duration=180, thumbnail_url=None,
        description=None, status="converted", error=None, meta_title="Song",
        meta_artist="Artist", meta_album=None, meta_track=None, raw_path=None,
        mp3_path=None, m4a_path=None, cover_path=None, downloaded_at=None,
        converted_at=None, synced_at=None,
    )

    class Folder:
        kind = "folder"
        title = "A folder"

    assert track_local_uri(track, Folder(), album="Road Trip").endswith(
        ":Road+Trip:Song:180"
    )


def test_every_uri_part_can_come_from_the_files_own_tags():
    from musicdlp.core.models import Track
    from musicdlp.services.spotify_sync import track_local_uri

    track = Track(
        id=1, platform="youtube", media_id="m", url="", source_index=None,
        title="Upload title", uploader="Uploader", duration=180,
        thumbnail_url=None, description=None, status="converted", error=None,
        meta_title="Edited title", meta_artist="Edited artist",
        meta_album=None, meta_track=None, raw_path=None, mp3_path=None,
        m4a_path=None, cover_path=None, downloaded_at=None, converted_at=None,
        synced_at=None,
    )

    uri = track_local_uri(
        track, title="Tag Title", artist="Tag Artist",
        album="Tag Album", seconds=181,
    )

    assert uri == (
        "spotify:local:Tag+Artist:Tag+Album:Tag+Title:181"
    )
