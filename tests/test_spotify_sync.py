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
