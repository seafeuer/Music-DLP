"""Smart titles are applied at add time and never overwrite manual edits."""

import logging

import pytest

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.services.library import LibraryService


class FakeExtractor:
    def __init__(self, titles):
        self.titles = titles

    def extract(self, url, cancel_event=None):
        from musicdlp.services.ytdlp import ExtractedSource, ExtractedTrack

        tracks = []
        for index, (media_id, title, channel) in enumerate(self.titles, start=1):
            tracks.append(
                ExtractedTrack(
                    platform="youtube",
                    media_id=media_id,
                    url=f"https://www.youtube.com/watch?v={media_id}",
                    title=title,
                    uploader=channel,
                    duration=100,
                    position=index,
                )
            )
        return ExtractedSource(
            url=url,
            kind="playlist",
            platform="youtube",
            external_id="PLx",
            title="Test",
            uploader="Channel",
            thumbnail_url="",
            tracks=tracks,
        )


@pytest.fixture()
def library(tmp_path):
    settings = Settings(
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
        first_run_done=True,
        smart_titles=True,
    )
    db = Database(tmp_path / "library.db")
    service = LibraryService(db, settings, FakeExtractor([]))
    yield service, db, settings
    db.close()


def _add(service, titles):
    service.ytdlp = FakeExtractor(titles)
    return service.add_links(["https://www.youtube.com/playlist?list=PLx"])


def test_confident_title_is_applied(library):
    service, db, _settings = library
    result = _add(service, [
        ("vid1", "DRAKE - AT THE GATES FT LIL UZI VERT (OG) (CDQ QUALITY)",
         "MuddyBeats"),
    ])
    assert result.titles_fixed == 1

    track = db.get_track_by_media("youtube", "vid1")
    assert track.meta_title == "At The Gates"
    assert track.meta_artist == "Drake, Lil Uzi Vert"
    assert track.smart_confidence >= 0.8


def test_uncertain_title_is_only_suggested(library):
    service, db, _settings = library
    result = _add(service, [
        ("vid2", "THUGGERS VAULT ひ - NEW LEAK MONDAY MORNING (2018) YOUNG THUG",
         "THUGGERS VAULT ひ"),
    ])
    assert result.titles_fixed == 0

    track = db.get_track_by_media("youtube", "vid2")
    assert track.meta_title is None
    assert track.smart_title
    assert track.has_suggestion


def test_known_artist_lets_the_parser_split(library):
    service, db, _settings = library
    # First add gives the parser a confirmed artist name to learn from.
    _add(service, [("vid3", "DRAKE - AT THE GATES", "MuddyBeats")])

    _add(service, [
        ("vid4", "THUGGERS VAULT ひ - NEW LEAK MONDAY MORNING (2018) YOUNG THUG",
         "THUGGERS VAULT ひ"),
    ])
    # "Young Thug" is not known yet, so this stays a suggestion.
    track = db.get_track_by_media("youtube", "vid4")
    assert track.meta_title is None

    # Confirm the artist once, then a new upload with the same shape splits.
    db.update_track(track.id, meta_artist="Young Thug")
    _add(service, [
        ("vid5", "THUGGERS VAULT ひ - NEW LEAK  YOUNG THUG  HOW COME (SMF ERA)",
         "THUGGERS VAULT ひ"),
    ])
    learned = db.get_track_by_media("youtube", "vid5")
    assert learned.meta_title == "How Come"
    assert learned.meta_artist == "Young Thug"


def test_manual_edits_are_never_overwritten(library):
    service, db, _settings = library
    _add(service, [("vid6", "DRAKE - AT THE GATES", "MuddyBeats")])
    track = db.get_track_by_media("youtube", "vid6")
    service.set_metadata(track.id, title="My Own Title", artist="My Artist")

    _add(service, [("vid6", "DRAKE - AT THE GATES", "MuddyBeats")])
    track = db.get_track_by_media("youtube", "vid6")
    assert track.meta_title == "My Own Title"
    assert track.meta_artist == "My Artist"


def test_setting_off_disables_suggestions(library):
    service, db, settings = library
    settings.smart_titles = False
    _add(service, [("vid7", "DRAKE - AT THE GATES", "MuddyBeats")])
    track = db.get_track_by_media("youtube", "vid7")
    assert track.meta_title is None
    assert track.smart_title is None
