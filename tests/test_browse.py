"""Browsing the library by artist and album."""

from musicdlp.core.db import Database
from musicdlp.ui.dialogs.browse import (
    NO_ALBUM, UNKNOWN_ARTIST, BrowseDialog, group_rows,
)


def _rows(tmp_path):
    db = Database(tmp_path / "library.db")
    source_id = db.add_source(
        url="https://example.test/list",
        kind="playlist",
        platform="youtube",
        external_id="list",
        title="A list",
        uploader="Uploader",
        item_count=3,
        thumbnail_url=None,
    )
    ids = []
    for media_id, title, artist, album, number in (
        ("a", "First", "Artist A", "Album One", 1),
        ("b", "Second", "Artist A", "Album One", 2),
        ("c", "Third", "Artist B", "", None),
    ):
        track_id = db.upsert_track(
            platform="youtube", media_id=media_id,
            url=f"https://youtu.be/{media_id}", title=title,
        )
        db.update_track(
            track_id, meta_title=title,
            meta_artist=artist or None,
            meta_album=album or None,
            meta_track=number,
        )
        ids.append(track_id)
    db.set_source_tracks(source_id, [(track_id, i) for i, track_id in enumerate(ids)])
    rows = db.get_all_track_rows()
    return db, rows


def test_grouping_puts_an_album_under_its_artist(tmp_path):
    db, rows = _rows(tmp_path)

    groups = group_rows(rows)

    assert list(groups["Artist A"]) == ["Album One"]
    assert [row.track.display_title for row in groups["Artist A"]["Album One"]] == [
        "First", "Second",
    ]
    assert list(groups["Artist B"]) == [NO_ALBUM]
    db.close()


def test_a_song_with_no_artist_is_still_reachable(tmp_path):
    db, rows = _rows(tmp_path)
    for track_id in [row.track.id for row in rows]:
        db.update_track(track_id, meta_artist=None, uploader=None)
    rows = db.get_all_track_rows()

    groups = group_rows(rows)

    assert list(groups) == [UNKNOWN_ARTIST]
    db.close()


def test_the_dialog_lists_artists_albums_and_their_songs(qtbot, tmp_path):
    db, rows = _rows(tmp_path)
    dialog = BrowseDialog(rows, None)
    qtbot.addWidget(dialog)

    top = dialog.tree.topLevelItemCount()
    assert top == 3  # All songs + two artists

    artist_a = dialog.tree.topLevelItem(1)
    assert artist_a.text(0) == "Artist A"
    assert artist_a.childCount() == 1
    assert artist_a.child(0).text(0) == "Album One"

    dialog.tree.setCurrentItem(artist_a.child(0))
    assert dialog.covers.count() == 2
    assert dialog.count_label.text().startswith("2 songs")

    dialog._tick_shown()
    assert len(dialog.ticked_ids) == 2
    db.close()

def test_highlighted_covers_can_be_used_without_ticking_the_whole_group(
    qtbot, tmp_path
):
    db, rows = _rows(tmp_path)
    dialog = BrowseDialog(rows, None)
    qtbot.addWidget(dialog)
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(1).child(0))

    assert dialog.footer.primary_button.isEnabled() is False
    dialog.covers.item(0).setSelected(True)

    assert dialog.footer.primary_button.isEnabled() is True
    dialog._use_ticked()
    assert dialog.ticked_ids == [rows[0].track.id]
    db.close()


def test_highlighting_in_a_second_group_keeps_the_first_groups_ticks(
    qtbot, tmp_path
):
    db, rows = _rows(tmp_path)
    dialog = BrowseDialog(rows, None)
    qtbot.addWidget(dialog)
    artist_a = dialog.tree.topLevelItem(1)
    # Tick both songs in Album One.
    dialog.tree.setCurrentItem(artist_a.child(0))
    dialog._tick_shown()
    first_group = list(dialog.ticked_ids)
    assert len(first_group) == 2

    # Switch to the other artist and highlight its one cover.
    dialog.tree.setCurrentItem(dialog.tree.topLevelItem(2))
    dialog.covers.item(0).setSelected(True)
    dialog._use_ticked()

    assert dialog.ticked_ids == sorted(first_group + [rows[2].track.id])
    db.close()
