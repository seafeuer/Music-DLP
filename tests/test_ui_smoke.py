"""Offscreen smoke test of the main window."""

import logging

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.ui.main_window import MainWindow, build_context
from musicdlp.ui.theme import apply_theme


@pytest.fixture()
def window(qtbot, qapp, tmp_path, monkeypatch):
    # Never write the real user config from a test.
    monkeypatch.setattr(
        Settings, "save", lambda self, path=None: None
    )

    settings = Settings(
        theme="dark",
        mode="easy",
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
        first_run_done=True,
        notifications=False,
    )
    db = Database(tmp_path / "library.db")
    source_id = db.add_source(
        url="https://www.youtube.com/playlist?list=PLtest",
        kind="playlist",
        platform="youtube",
        external_id="PLtest",
        title="Test playlist",
        uploader="Test channel",
        item_count=2,
        thumbnail_url=None,
    )
    ids = []
    for index, media_id in enumerate(("vid1", "vid2"), start=1):
        ids.append(
            db.upsert_track(
                platform="youtube",
                media_id=media_id,
                url=f"https://www.youtube.com/watch?v={media_id}",
                title=f"Song {index}",
                uploader="Test channel",
                duration=200,
            )
        )
    db.set_source_tracks(source_id, [(ids[0], 1), (ids[1], 2)])

    logger = logging.getLogger("musicdlp.test")
    logger.setLevel(logging.INFO)
    apply_theme(qapp, settings.theme)

    ctx = build_context(settings, db, logger)
    main = MainWindow(ctx)
    qtbot.addWidget(main)
    main.show()
    qtbot.wait(100)
    yield main, db, ids
    try:
        db.close()
    except Exception:
        pass


def test_rows_and_default_selection(window):
    main, db, ids = window
    # The app opens on "All songs", where nothing is ticked automatically.
    assert main.track_table.model.rowCount() == 2
    assert main._current_source_id == 0
    assert main.track_table.checked_count() == 0
    assert main.settings.mode == "easy"
    assert main.action_layout.count() == 1
    # The details panel is visible in both modes so editing stays discoverable.
    assert not main.inspector_scroll.isHidden()

    # Opening the link itself ticks its new songs.
    source_id = db.get_sources()[0].id
    main._load_source(source_id)
    assert main.track_table.checked_count() == 2


def test_pro_mode_and_inspector(window):
    main, _db, _ids = window
    main.mode_combo.setCurrentIndex(1)
    assert main.settings.mode == "pro"
    assert not main.inspector_scroll.isHidden()
    assert main.action_layout.count() == 3

    main.track_table.table.selectRow(0)
    assert main.inspector.title_label.text() == "Song 1"
    assert main.inspector.artist_label.text() == "Test channel"
    assert main.track_table.edit_button.isEnabled()


def test_progress_filter_and_search(window):
    main, _db, ids = window
    # The speed is shown when the status line has room for it; the
    # headless screen is only 800 px wide, so size the window as a user's.
    main.resize(1280, 800)
    QApplication.processEvents()
    main._on_progress({
        "stage": "download",
        "percent": 50,
        "item_percent": 50,
        "title": "Song 1",
        "speed": 1024 * 1024,
        "eta": 65,
        "track_id": ids[0],
    })
    assert main.track_table.model.progress[ids[0]] == 50
    assert "MB/s" in main.status.detail_label.text()

    main.track_table.filter_combo.setCurrentIndex(2)  # Downloaded
    assert main.track_table.proxy.rowCount() == 0
    main.track_table.filter_combo.setCurrentIndex(0)
    assert main.track_table.proxy.rowCount() == 2

    main.track_table.search.setText("song 2")
    assert main.track_table.proxy.rowCount() == 1
    main.track_table.search.clear()


def test_metadata_edit(window):
    main, db, ids = window
    main._apply_metadata(ids[0], "Renamed", "New Artist", "Album", 3)
    track = db.get_track(ids[0])
    assert track.meta_title == "Renamed"
    assert track.meta_artist == "New Artist"
    assert track.meta_album == "Album"
    assert track.meta_track == 3


def test_checkbox_click_keeps_the_window_working(window):
    """Regression: checking a row used to raise and freeze the run."""
    main, _db, ids = window
    model = main.track_table.model
    index = model.index(model.row_index_for_id(ids[0]), 0)

    assert model.setData(index, Qt.Checked, Qt.CheckStateRole)
    assert ids[0] in model.checked
    assert model.setData(index, Qt.Unchecked, Qt.CheckStateRole)
    assert ids[0] not in model.checked

    # The row is still selectable and the details panel follows it.
    main.track_table._select_by_track_id(ids[1])
    assert main.track_table.current_row().track.id == ids[1]
    assert main.inspector.title_label.text() == "Song 2"


def test_inspector_include_checkbox_mirrors_the_list(window):
    main, db, ids = window
    source_id = db.get_sources()[0].id
    main._load_source(source_id)
    main.track_table._select_by_track_id(ids[0])
    assert main.inspector.include_check.isChecked()

    main.inspector.include_check.setChecked(False)
    assert ids[0] not in main.track_table.model.checked

    main.inspector.include_check.setChecked(True)
    assert ids[0] in main.track_table.model.checked


def test_selection_survives_a_reload(window):
    main, _db, ids = window
    main.track_table._select_by_track_id(ids[1])
    main._reload_current_source()
    row = main.track_table.current_row()
    assert row is not None
    assert row.track.id == ids[1]
    assert main.inspector.title_label.text() == "Song 2"


def test_reconvert_on_synced_track_includes_sync(window):
    main, db, ids = window
    db.update_track(
        ids[0],
        status="synced",
        synced_at="2026-01-01T00:00:00",
        mp3_path=str(main.settings.output_path / "MP3" / "Song 1.mp3"),
    )
    main._reload_current_source()

    captured = {}
    original = main._start_pipeline

    def fake_start(rows, stages, **kwargs):
        captured["stages"] = stages
        captured["rows"] = rows

    main._start_pipeline = fake_start
    try:
        main._reconvert_track(ids[0])
    finally:
        main._start_pipeline = original

    assert captured["stages"][-1] == "sync", captured


def test_editing_a_synced_song_rewrites_and_resends_it(window, monkeypatch):
    """Every field, the picture, and the Spotify copy in one Save."""
    from PySide6.QtWidgets import QDialog

    from musicdlp.ui import main_window as main_window_module

    main, db, ids = window
    track_id = ids[0]
    raw = main.settings.output_path / "_raw" / "raw.webm"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(b"raw")
    song = main.settings.output_path / "MP3" / "Song 1.mp3"
    song.parent.mkdir(parents=True, exist_ok=True)
    song.write_bytes(b"old")
    (main.settings.spotify_path).mkdir(parents=True, exist_ok=True)
    (main.settings.spotify_path / song.name).write_bytes(b"old")
    db.update_track(
        track_id, raw_path=str(raw), mp3_path=str(song),
        status="synced", synced_at="2026-01-01T00:00:00",
    )
    main._load_source(db.get_sources()[0].id)

    picture = main.settings.output_path / "chosen.png"
    from PIL import Image
    Image.new("RGB", (800, 800), (1, 2, 3)).save(picture)

    class FakeDetails:
        def __init__(self, *args, **kwargs):
            self.result_values = {
                "title": "Renamed", "artist": "New Artist",
                "album": "New Album", "track_number": 9,
                "cover_path": str(picture), "cover_changed": True,
                "update_file": True,
            }

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(main_window_module, "TrackDetailsDialog", FakeDetails)
    captured = {}
    monkeypatch.setattr(
        main, "_start_pipeline",
        lambda rows, stages, **kwargs: captured.update(
            rows=rows, stages=stages, force=kwargs.get("force")
        ),
    )

    main._open_details(track_id)

    track = db.get_track(track_id)
    assert (track.meta_title, track.meta_artist, track.meta_album,
            track.meta_track) == ("Renamed", "New Artist", "New Album", 9)
    assert track.cover_source == "custom"
    assert captured["stages"] == ["convert", "sync"]
    assert captured["force"] is True
    assert [row.track.id for row in captured["rows"]] == [track_id]



def test_cancelling_an_add_does_not_report_a_failure(window, monkeypatch):
    """Cancel is a normal outcome, not "Could not add link"."""
    from PySide6.QtWidgets import QMessageBox

    from musicdlp.services.library import AddResult

    main, _db, _ids = window
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: shown.append(args)),
    )

    main._on_add_finished(AddResult(errors=["Cancelled."]))

    assert shown == []
    assert "Cancelled" in main.status.status_label.text()


def test_cancelling_a_refresh_is_not_a_problem(window):
    from musicdlp.services.library import AddResult

    main, _db, _ids = window
    main._on_refresh_finished(AddResult(errors=["Cancelled."]))
    assert main.status.status_label.text() == "Cancelled."


def test_library_buttons_stay_disabled_while_a_job_runs(window):
    """Regression: per-row rules used to re-enable Refresh and Remove."""
    main, db, _ids = window
    source_id = db.get_sources()[0].id
    main.sidebar.select_source(source_id)
    assert main.sidebar.refresh_button.isEnabled()
    assert main.sidebar.remove_button.isEnabled()

    main.sidebar.set_busy(True)

    assert not main.sidebar.refresh_button.isEnabled()
    assert not main.sidebar.remove_button.isEnabled()
    assert not main.sidebar.add_button.isEnabled()
    assert not main.sidebar.folder_button.isEnabled()

    main.sidebar.set_busy(False)
    assert main.sidebar.refresh_button.isEnabled()
    assert main.sidebar.remove_button.isEnabled()


def test_removing_a_song_is_refused_while_a_job_runs(window, monkeypatch):
    """A pipeline holds rows in memory; deleting one would strand it."""
    from PySide6.QtWidgets import QMessageBox

    main, db, ids = window
    asked = []
    monkeypatch.setattr(
        main, "_confirm_removal",
        lambda *args, **kwargs: asked.append(args) or True,
    )
    monkeypatch.setattr(main, "_blocked_by_job", lambda: True)

    main._remove_track(ids[0])

    assert asked == []
    assert db.get_track(ids[0]) is not None


def test_refreshing_a_folder_does_not_treat_it_as_a_link(window, monkeypatch):
    """F5 on a folder used to try to read folder:// as a web link."""
    main, db, _ids = window
    folder_id = db.add_folder("A folder")
    main._refresh_sources(folder_id)

    started = []
    monkeypatch.setattr(
        main, "_refresh_source", lambda source_id: started.append(source_id)
    )
    main._refresh_current()

    assert started == []
    assert "folder" in main.status.status_label.text().lower()


def test_use_video_thumbnail_from_the_details_dialog_records_the_choice(
    window, monkeypatch
):
    """Picking the video's picture must be stored as that choice."""
    from PySide6.QtWidgets import QDialog

    from musicdlp.ui import main_window as main_window_module

    main, db, ids = window
    track_id = ids[0]
    db.update_track(track_id, status="converted")

    class FakeDetails:
        def __init__(self, *args, **kwargs):
            self.result_values = {
                "title": "Song 1", "artist": "Channel", "album": "",
                "track_number": None, "cover_path": None,
                "cover_changed": True, "update_file": False,
            }

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(main_window_module, "TrackDetailsDialog", FakeDetails)
    main._open_details(track_id)

    track = db.get_track(track_id)
    assert track.cover_source == "video"
    assert track.cover_path is None
    assert track.needs_update is True


# ------------------------- "Use video thumbnail" reaches Save as a choice

def _details_dialog(tmp_path, *, cover, thumbnail_url):
    from PIL import Image

    from musicdlp.ui.dialogs.track_details import TrackDetailsDialog

    db = Database(tmp_path / "details.db")
    tid = db.upsert_track(
        platform="soundcloud", media_id="sc1",
        url="https://soundcloud.com/a/song", title="A Song",
        uploader="Chan", duration=180, thumbnail_url=thumbnail_url,
    )
    if cover is not None:
        cover.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1400, 1400), (200, 20, 20)).save(cover, "PNG")
        db.update_track(tid, cover_path=str(cover), status="converted",
                        mp3_path=str(tmp_path / "song.mp3"))
    dialog = TrackDetailsDialog(
        db.get_track(tid), album_default="Singles", track_default=1,
        filename_template="{title}",
    )
    return db, dialog


def test_use_video_thumbnail_clears_the_picture_and_marks_it_changed(
    qapp, tmp_path
):
    bad = tmp_path / "art" / "album_soundcloud_sc1_1400_v3.jpg"
    db, dialog = _details_dialog(
        tmp_path, cover=bad, thumbnail_url="https://i1.sndcdn.com/x.jpg"
    )

    dialog._use_video_thumbnail()
    dialog._save()

    assert dialog.result_values["cover_path"] is None
    assert dialog.result_values["cover_changed"] is True
    db.close()


def test_the_video_button_is_offered_only_when_there_is_a_video_picture(
    qapp, tmp_path
):
    bad = tmp_path / "art" / "album_soundcloud_sc1_1400_v3.jpg"
    db, dialog = _details_dialog(tmp_path, cover=bad, thumbnail_url="")
    assert dialog.video_button.isEnabled() is False
    db.close()

    db2, dialog2 = _details_dialog(
        tmp_path / "two", cover=bad, thumbnail_url="https://i1.sndcdn.com/x.jpg"
    )
    assert dialog2.video_button.isEnabled() is True
    db2.close()


def test_a_stored_still_can_be_used_when_the_url_is_gone(qapp, tmp_path):
    """An old post has its picture on disk but no URL to fetch it again.

    The button used to be disabled, so the only picture the song had could
    not be chosen, and clearing it would have left the file with none.
    """
    still = tmp_path / "art" / "twitter_1.jpg"
    db, dialog = _details_dialog(tmp_path, cover=still, thumbnail_url="")

    assert dialog.video_button.isEnabled() is True
    dialog._use_video_thumbnail()
    dialog._save()

    assert dialog.result_values["cover_path"] == str(still)
    assert dialog.result_values["cover_changed"] is True
    db.close()


def test_switching_mode_leaves_no_stale_action_buttons(window):
    """Old buttons stayed visible for a frame and painted over the new ones."""
    main, _db, _ids = window
    main.mode_combo.setCurrentIndex(1)   # Pro: three buttons
    qtbot = None
    first = [
        main.action_layout.itemAt(i).widget()
        for i in range(main.action_layout.count())
    ]
    assert len(first) == 3

    main.mode_combo.setCurrentIndex(0)   # Easy: one button
    assert main.action_layout.count() == 1
    # The Pro buttons must be hidden at once, not merely queued for deletion.
    for button in first:
        assert button.isHidden()


def test_a_highlighted_but_unticked_song_says_it_is_not_included(window):
    """Highlighting a row and ticking it are different; say so."""
    main, db, ids = window
    source_id = db.get_sources()[0].id
    main._load_source(source_id)
    main.track_table.model.clear_checks()
    main.track_table._select_by_track_id(ids[0])

    assert "Not in the next run" in main.inspector.hint_label.text()
    main.track_table.model.toggle_check(ids[0], True)
    assert "Not in the next run" not in main.inspector.hint_label.text()


def test_status_messages_are_pluralised(window, monkeypatch):
    from musicdlp.services.library import AddResult

    main, _db, ids = window
    source_id = main.db.get_sources()[0].id
    result = AddResult(
        sources=[source_id], tracks_total=1, tracks_new=0, tracks_owned=1
    )
    main._on_add_finished(result)
    text = main.status.status_label.text()
    assert text.startswith("Added")
    assert "Song 1" in text


def test_a_failed_batch_opens_the_activity_log(window):
    main, _db, _ids = window
    main.logs_action.setChecked(False)
    main._on_pipeline_finished({
        "downloaded": 1, "converted": 0, "synced": 0, "failed": 2,
        "skipped": 0, "errors": [], "cancelled": False, "stages": ["download"],
    })
    assert main.logs_action.isChecked()
    assert main.log_drawer.isVisible()


def test_the_status_line_keeps_its_song_at_a_narrow_width(qapp):
    from musicdlp.ui.widgets.status_bar import StatusBarWidget

    bar = StatusBarWidget()
    bar.resize(820, 40)
    bar.set_progress({
        "stage": "download", "percent": 40, "item_percent": 42,
        "title": "At The Gates", "speed": 3_200_000, "eta": 19,
    })
    assert "At The Gates" in bar.status_label.text()
    assert "MB/s" not in bar.detail_label.text()
    assert "19" in bar.detail_label.text()


def test_the_window_minimum_fits_the_screen(qapp, qtbot, tmp_path, monkeypatch):
    from musicdlp.core.config import Settings
    from musicdlp.core.db import Database
    from musicdlp.ui.main_window import MainWindow, build_context

    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    settings = Settings(
        output_dir=str(tmp_path / "out"), spotify_dir=str(tmp_path / "sp"),
        first_run_done=True, notifications=False,
    )
    db = Database(tmp_path / "lib.db")
    win = MainWindow(build_context(settings, db, __import__("logging").getLogger("t")))
    qtbot.addWidget(win)
    screen = qapp.primaryScreen().availableGeometry()
    assert win.minimumWidth() <= max(720, screen.width())
    assert win.minimumHeight() <= max(440, screen.height())
    db.close()


def test_the_details_preview_uses_the_song_s_own_format(qapp, tmp_path):
    from musicdlp.ui.dialogs.track_details import TrackDetailsDialog

    db = Database(tmp_path / "fmt.db")
    tid = db.upsert_track(
        platform="youtube", media_id="m1", url="u", title="A Song",
        uploader="C", duration=100,
    )
    song = tmp_path / "A Song.m4a"
    song.write_bytes(b"x")
    db.update_track(tid, status="converted", m4a_path=str(song))
    dialog = TrackDetailsDialog(
        db.get_track(tid), album_default="Singles", track_default=1,
        filename_template="{title}", file_format="m4a",
    )
    assert dialog.preview.text() == "A Song.m4a"
    db.close()



def test_a_job_finish_keeps_the_songs_filter_and_search(window, qtbot):
    """A reload after a job must not reset what the user was looking at."""
    main, db, ids = window
    source_id = db.get_sources()[0].id
    main._load_source(source_id)
    main.track_table.filter_combo.setCurrentIndex(4)  # Problems
    main.track_table.search.setText("song")
    qtbot.wait(20)

    main._on_pipeline_finished({
        "downloaded": 0, "converted": 0, "synced": 0, "failed": 0,
        "skipped": 0, "errors": [], "cancelled": False, "stages": [],
    })

    assert main.track_table.filter_combo.currentIndex() == 4
    assert main.track_table.search.text() == "song"


def test_dismissed_banner_stays_gone(window, monkeypatch):
    """Closing a setup prompt keeps it closed for the session."""
    main, _db, _ids = window
    # Make the machine look like one without FFmpeg, without replacing the
    # whole context (closeEvent still needs it).
    monkeypatch.setattr(main.ctx.ffmpeg, "ffmpeg", None)

    main._refresh_setup_banner()
    assert main.setup_banner.kind == "ffmpeg"

    main.setup_banner.close_button.click()
    assert main.setup_banner.kind is None

    # A later refresh (a download finishing, settings changing) must not
    # bring back the prompt the user just closed.
    main._refresh_setup_banner()
    assert main.setup_banner.kind is None


def test_select_all_ticks_only_the_songs_shown(window, qtbot):
    main, _db, ids = window
    main.track_table.search.setText("Song 1")
    qtbot.wait(30)
    assert main.track_table.proxy.rowCount() == 1

    main.track_table.check_all_visible()

    assert main.track_table.model.checked == {ids[0]}


def test_main_controls_have_accessible_names(window):
    main, _db, _ids = window
    for widget in (
        main.link_edit,
        main.mode_combo,
        main.search_edit,
        main.track_table.search,
        main.track_table.filter_combo,
        main.sidebar.view,
    ):
        assert widget.accessibleName().strip(), widget.objectName() or widget


def test_inspector_actions_stay_visible_at_minimum_height(window, qtbot):
    from PySide6.QtCore import QPoint

    main, db, ids = window
    db.update_track(
        ids[0],
        meta_title="長い日本語の曲名 " * 20,
        error="The Spotify copy failed: " + "a long explanation " * 10,
    )
    source = db.get_sources()[0]
    main._load_source(source.id)
    main.resize(1000, 620)
    qtbot.wait(50)

    row = db.get_tracks_for_source(source.id)[0]
    main.inspector.set_row(row, True)
    qtbot.wait(30)

    button = main.inspector.edit_button
    viewport = main.inspector_scroll.viewport()
    for button in (
        main.inspector.edit_button,
        main.inspector.reconvert_button,
        main.inspector.retry_button,
        main.inspector.source_button,
        main.inspector.reveal_button,
    ):
        point = button.mapTo(viewport, QPoint(0, 0))
        assert point.y() >= 0
        assert point.y() + button.height() <= viewport.height(), button.text()


def test_missing_spotify_is_visible_without_a_tooltip(window):
    main, _db, _ids = window
    main.status.set_spotify_state(
        "Spotify: not found", "Spotify was not found."
    )
    assert "not found" in main.status.spotify_button.text().lower()


def test_problems_filter_finds_missing_and_needs_update(window, qtbot):
    main, db, ids = window
    source = db.get_sources()[0]
    db.update_track(ids[0], status="converted", needs_update=1)
    db.update_track(
        ids[1], status="converted", mp3_path=str(main.settings.output_path / "gone.mp3")
    )
    main._load_source(source.id)

    index = main.track_table.filter_combo.findData("errors")
    main.track_table.filter_combo.setCurrentIndex(index)
    qtbot.wait(30)

    assert main.track_table.proxy.rowCount() == 2


def test_primary_action_sits_next_to_the_more_button(window):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QPushButton

    main, _db, _ids = window
    primary = next(
        button for button in main.action_area.findChildren(QPushButton)
        if button.objectName() == "primary"
    )
    primary_right = primary.mapTo(main, primary.rect().bottomRight()).x()
    more_left = main.more_button.mapTo(main, QPoint(0, 0)).x()

    assert 0 <= more_left - primary_right <= 20


def test_sidebar_buttons_do_nothing_without_a_selection(window):
    from PySide6.QtCore import QModelIndex

    main, _db, _ids = window
    main.sidebar.view.clearSelection()
    main.sidebar.view.setCurrentIndex(QModelIndex())
    main.sidebar._update_buttons()

    assert not main.sidebar.refresh_button.isEnabled()
    assert not main.sidebar.remove_button.isEnabled()


def test_primary_action_keeps_keyboard_focus_when_rebuilt(window, qtbot):
    from PySide6.QtWidgets import QPushButton

    main, _db, ids = window
    main._checked = {ids[0]}
    main.track_table.model.set_checked([ids[0]])
    main._update_actions()
    primary = next(
        button for button in main.action_area.findChildren(QPushButton)
        if button.objectName() == "primary"
    )
    main.activateWindow()
    primary.setFocus()
    qtbot.wait(20)
    assert primary.hasFocus()

    main._update_actions()

    rebuilt = next(
        button for button in main.action_area.findChildren(QPushButton)
        if button.objectName() == "primary"
    )
    assert rebuilt is not primary
    assert rebuilt.hasFocus()


def test_ctrl_enter_is_the_primary_action_shortcut(window):
    main, _db, _ids = window
    assert main._primary_shortcut.key().toString() == "Ctrl+Return"


def test_a_superseded_link_read_does_not_touch_the_new_job(window):
    from musicdlp.services.library import AddResult

    main, _db, _ids = window
    main._add_generation = 5
    main._add_running = True

    main._on_add_finished(
        AddResult(errors=["Cancelled."]), generation=4
    )

    assert main._add_running is True

    main._on_add_finished(
        AddResult(errors=["Cancelled."]), generation=5
    )
    assert main._add_running is False


def test_a_failed_add_gives_the_link_back(window, monkeypatch):
    from musicdlp.services.library import AddResult

    main, _db, _ids = window
    monkeypatch.setattr(
        "musicdlp.ui.main_window.QMessageBox.warning",
        staticmethod(lambda *args, **kwargs: None),
    )
    main.link_edit.setText("https://www.youtube.com/watch?v=broken")
    main._add_from_input()
    assert main.link_edit.text() == ""

    main._on_add_finished(
        AddResult(errors=["That link isn't a video or playlist."])
    )

    assert main.link_edit.text() == "https://www.youtube.com/watch?v=broken"


def test_the_song_table_does_not_trap_the_tab_key(window, qtbot):
    main, _db, ids = window
    main._load_source(main.db.get_sources()[0].id)
    main.track_table.table.setFocus()
    main.activateWindow()
    qtbot.wait(20)
    assert main.track_table.table.hasFocus()

    qtbot.keyClick(main.track_table.table, Qt.Key_Tab)
    qtbot.wait(20)

    assert not main.track_table.table.hasFocus()


def test_the_theme_switch_stays_visible_at_the_minimum_size(window):
    main, _db, _ids = window
    main.resize(1000, 620)
    main._update_theme_button()

    assert main.theme_button.isVisible()
    assert main.theme_button.text()


def test_the_details_dialog_opens_in_the_title_field(qapp, tmp_path):
    """Focus sat on Save, so Return rewrote the file before it was read."""
    db, dialog = _details_dialog(
        tmp_path, cover=None, thumbnail_url="https://i1.sndcdn.com/x.jpg"
    )
    dialog.show()
    qapp.processEvents()

    assert dialog.update_check.isChecked() is False
    assert dialog.focusWidget() is dialog.title_edit
    dialog.close()
    db.close()


def test_editing_a_field_opts_into_rewriting_the_file(qapp, tmp_path):
    from PIL import Image

    db, dialog = _details_dialog(
        tmp_path, cover=None, thumbnail_url="https://i1.sndcdn.com/x.jpg"
    )
    # A converted song: rewriting is possible but not assumed.
    db.update_track(1, status="converted", mp3_path=str(tmp_path / "a.mp3"))
    dialog = __import__(
        "musicdlp.ui.dialogs.track_details", fromlist=["TrackDetailsDialog"]
    ).TrackDetailsDialog(
        db.get_track(1), album_default="Singles", track_default=1,
        filename_template="{title}",
    )
    assert dialog.update_check.isChecked() is False

    dialog.title_edit.setText("A Better Name")

    assert dialog.update_check.isChecked() is True
    dialog.close()
    db.close()


def test_the_confirmation_says_a_successful_sync_was_a_sync(
    qapp, window, monkeypatch
):
    """A sync-only run on a machine without FFmpeg said "FFmpeg is needed"."""
    main, _db, _ids = window
    monkeypatch.setattr(
        type(main.ctx.ffmpeg), "available", property(lambda self: False)
    )
    main._on_pipeline_finished({
        "downloaded": 0,
        "converted": 0,
        "synced": 1,
        "failed": 0,
        "skipped": 0,
        "errors": [],
        "cancelled": False,
        "stages": ["sync"],
    })

    text = main.status.status_label.text()
    assert "In Spotify" in text
    assert "FFmpeg" not in text


def test_the_details_panel_follows_a_running_job(window):
    """The inspector said "Not downloaded" while the row said "Downloading"."""
    main, db, ids = window
    main._load_source(main.db.get_sources()[0].id)
    main.track_table.table.selectRow(0)
    row = main.track_table.current_row()
    assert row is not None

    db.update_track(row.track.id, status="downloading")
    main._on_track_changed(row.track.id)

    assert "Downloading" in main.inspector.status_label.text()


def test_fixing_covers_can_be_cancelled(qapp, tmp_path):
    import threading

    from musicdlp.ui.main_window import FixCoversSignals, FixCoversTask

    calls = []

    class Library:
        def refresh_cover(self, track_id, **_kwargs):
            calls.append(track_id)
            return "updated"

    signals = FixCoversSignals()
    results = []
    signals.finished.connect(results.append)
    cancel = threading.Event()
    cancel.set()

    task = FixCoversTask(Library(), None, [1, 2, 3], signals, cancel=cancel)
    task.run()

    assert calls == []
    assert results and results[0].get("cancelled") == 1


def test_a_dismissed_setup_dialog_does_not_claim_to_have_saved(qapp, window):
    main, _db, _ids = window

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            from PySide6.QtWidgets import QDialog
            return QDialog.Rejected

    import musicdlp.ui.main_window as module

    original = module.SetupDialog
    module.SetupDialog = FakeDialog
    try:
        main._open_setup()
    finally:
        module.SetupDialog = original

    assert "nothing was changed" in main.status.status_label.text().lower()


def test_the_log_drawer_shows_time_and_level(qapp):
    from musicdlp.ui.widgets.log_drawer import LogDrawer

    drawer = LogDrawer()
    drawer.append("warning", "something happened")

    text = drawer.text.toPlainText()
    assert "WARNING" in text
    assert "something happened" in text
    # HH:MM:SS at the start of the line.
    assert text[:2].isdigit() and text[2] == ":"


def test_the_shortcut_list_matches_the_keys(qapp):
    from musicdlp.ui.dialogs.shortcuts import SHORTCUTS

    text = " ".join(f"{keys} {description}" for keys, description in SHORTCUTS)
    assert "Backspace" in text
    assert "Shift+Enter" not in text


def test_the_paste_button_keeps_its_ampersand(qapp, window):
    main, _db, _ids = window
    assert main.paste_button.text() == "Paste && add"
