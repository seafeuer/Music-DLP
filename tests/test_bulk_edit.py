"""One rule applied to many songs."""

import pytest

from musicdlp.core.models import Track
from musicdlp.services.bulk_edit import BulkEdit, fields_for, preview, title_edit


def _track(title="Old Title", artist="Old Artist", album="Old Album", number=4):
    return Track(
        id=1,
        platform="youtube",
        media_id="abc",
        url="https://youtu.be/abc",
        source_index=None,
        title=title,
        uploader=artist,
        duration=100,
        thumbnail_url=None,
        description=None,
        status="new",
        error=None,
        meta_title=title,
        meta_artist=artist,
        meta_album=album,
        meta_track=number,
        raw_path=None,
        mp3_path=None,
        m4a_path=None,
        cover_path=None,
        downloaded_at=None,
        converted_at=None,
        synced_at=None,
    )


def test_an_album_change_leaves_title_artist_and_number_alone():
    fields = fields_for(_track(), BulkEdit(album="New Album"))

    assert fields == {"album": "New Album"}


def test_an_artist_change_does_not_touch_the_album():
    fields = fields_for(_track(), BulkEdit(artist=" New Artist "))

    assert fields == {"artist": "New Artist"}


def test_track_numbers_count_up_from_the_start():
    edit = BulkEdit(track_start=10, track_step=2)

    assert fields_for(_track(), edit, 0) == {"track_number": 10}
    assert fields_for(_track(), edit, 1) == {"track_number": 12}
    assert fields_for(_track(), edit, 3) == {"track_number": 16}


def test_a_plain_title_replacement_is_case_insensitive_by_default():
    edit = BulkEdit(find="old", replace="new")

    assert title_edit("Old Title", edit) == "new Title"
    assert title_edit("OLD TITLE", edit) == "new TITLE"


def test_a_case_sensitive_replacement_matches_the_users_capitalisation():
    edit = BulkEdit(find="Old", replace="New", case_sensitive=True)

    assert title_edit("Old Title", edit) == "New Title"
    assert title_edit("old Title", edit) == "old Title"


def test_a_regex_can_renumber_a_title():
    edit = BulkEdit(
        find=r"^(.*) \[(\d+)\]$", replace=r"\2 - \1", use_regex=True
    )

    assert title_edit("Song [3]", edit) == "3 - Song"


def test_a_broken_pattern_is_reported_before_anything_is_saved():
    with pytest.raises(ValueError, match="not valid"):
        title_edit("Song", BulkEdit(find="(", replace="x", use_regex=True))


def test_a_rule_with_no_title_find_keeps_the_title():
    assert title_edit("Song", BulkEdit()) == "Song"


def test_the_preview_shows_the_changes_in_order():
    tracks = [_track("One"), _track("Two")]

    lines = preview(tracks, BulkEdit(album="Album", track_start=1))

    assert lines == ["One — Old Artist — Album — #1", "Two — Old Artist — Album — #2"]


def test_the_dialog_refuses_to_apply_with_no_rule_chosen(qtbot):
    from musicdlp.ui.dialogs.bulk_edit import BulkEditDialog

    row = _row_for_dialog()
    dialog = BulkEditDialog([row])
    qtbot.addWidget(dialog)

    assert dialog.footer.primary_button.isEnabled() is False

    dialog.album_check.setChecked(True)
    dialog.album_edit.setText("An Album")
    assert dialog.footer.primary_button.isEnabled() is True

    dialog._accept()
    assert dialog.result_edit is not None
    assert dialog.result_edit.album == "An Album"


def test_the_dialog_keeps_apply_disabled_for_a_broken_pattern(qtbot):
    from musicdlp.ui.dialogs.bulk_edit import BulkEditDialog

    dialog = BulkEditDialog([_row_for_dialog()])
    qtbot.addWidget(dialog)
    dialog.find_edit.setText("(")
    dialog.regex_check.setChecked(True)

    assert dialog.footer.primary_button.isEnabled() is False
    assert "not valid" in dialog.preview_label.text()


def _row_for_dialog():
    from musicdlp.core.models import Source, TrackRow

    track = _track()
    source = Source(
        id=1, url="", kind="playlist", platform="youtube",
        external_id=None, title="A list", uploader="Artist",
        item_count=1, thumbnail_url=None, added_at=None, refreshed_at=None,
    )
    return TrackRow(source=source, track=track, position=1)


def test_songs_are_numbered_in_the_order_the_user_sees():
    from musicdlp.services.bulk_edit import order_rows

    tracks = [_track(), _track(), _track()]
    for index, track in enumerate(tracks):
        track.id = index + 1

    class Row:
        def __init__(self, track):
            self.track = track

    rows = [Row(track) for track in tracks]
    ordered = order_rows(rows, [3, 1, 2])

    assert [row.track.id for row in ordered] == [3, 1, 2]
    assert [row.track.id for row in order_rows(rows, [])] == [1, 2, 3]
