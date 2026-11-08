"""A job interrupted by a restart is offered again, and can keep running
in the tray while the window is closed."""

import json
import logging

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon

from musicdlp.core import pending
from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.paths import pending_job_path
from musicdlp.ui.main_window import MainWindow, build_context
from musicdlp.ui.theme import apply_theme


class FakePipeline:
    """Records what the window asked for without starting real work."""

    def __init__(self):
        self.starts = []
        self.is_running = False

    def start(self, items, stages, *, force=False, only_synced=False,
              retag=False):
        self.starts.append({
            "items": list(items),
            "stages": list(stages),
            "force": force,
            "only_synced": only_synced,
            "retag": retag,
        })
        return True

    def cancel(self):
        pass


@pytest.fixture()
def window(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
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

    logger = logging.getLogger("musicdlp.test.pending")
    logger.setLevel(logging.INFO)
    apply_theme(qapp, settings.theme)
    ctx = build_context(settings, db, logger)
    main = MainWindow(ctx)
    qtbot.addWidget(main)
    main.show()
    qtbot.wait(50)
    yield main, db, ids
    try:
        db.close()
    except Exception:
        pass


# ------------------------------------------------------------ the marker file


def test_a_pending_job_survives_a_round_trip(tmp_path):
    path = tmp_path / "pending-job.json"
    job = pending.new_job([3, 1, 3], ["download", "convert"], retag=True)

    pending.save(path, job)
    loaded = pending.load(path)

    assert loaded is not None
    # Duplicate ids are collapsed; the order the user chose is kept.
    assert loaded.track_ids == [3, 1]
    assert loaded.stages == ["download", "convert"]
    assert loaded.retag is True
    assert loaded.started_at


def test_an_unreadable_marker_is_ignored_instead_of_crashing(tmp_path):
    path = tmp_path / "pending-job.json"
    path.write_text("{not json", encoding="utf-8")

    assert pending.load(path) is None

    path.write_text(json.dumps({"track_ids": ["x"], "stages": ["download"]}),
                    encoding="utf-8")
    assert pending.load(path) is None


def test_clearing_a_marker_that_is_not_there_is_harmless(tmp_path):
    pending.clear(tmp_path / "nothing.json")


# ------------------------------------------------------- the window's side


def test_starting_a_job_records_it_and_finishing_clears_it(window):
    main, _db, ids = window
    main.ctx.pipeline = FakePipeline()

    started, _reason = main._start_pipeline(
        main.db.get_track_rows_by_ids(ids), ["download", "convert"]
    )

    assert started
    job = pending.load(pending_job_path())
    assert job is not None
    assert job.track_ids == ids
    assert job.stages == ["download", "convert"]

    main._on_pipeline_finished({"downloaded": 0})

    assert pending.load(pending_job_path()) is None


def test_a_pending_job_is_offered_and_resumed(window, monkeypatch):
    main, _db, ids = window
    fake = FakePipeline()
    main.ctx.pipeline = fake
    pending.save(
        pending_job_path(),
        pending.new_job(ids, ["convert"], retag=True),
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes
    )

    assert main.resume_pending_job() is True

    assert len(fake.starts) == 1
    assert [item.track.id for item in fake.starts[0]["items"]] == ids
    assert fake.starts[0]["stages"] == ["convert"]
    assert fake.starts[0]["retag"] is True


def test_declining_the_offer_forgets_the_job(window, monkeypatch):
    main, _db, ids = window
    fake = FakePipeline()
    main.ctx.pipeline = fake
    pending.save(pending_job_path(), pending.new_job(ids, ["download"]))
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No
    )

    assert main.resume_pending_job() is False

    assert fake.starts == []
    assert pending.load(pending_job_path()) is None


def test_a_job_whose_songs_are_gone_is_dropped_without_asking(
    window, monkeypatch
):
    main, _db, _ids = window
    fake = FakePipeline()
    main.ctx.pipeline = fake
    pending.save(pending_job_path(), pending.new_job([9999], ["download"]))

    def fail(*args, **kwargs):
        raise AssertionError("the user should not be asked")

    monkeypatch.setattr(QMessageBox, "question", fail)

    assert main.resume_pending_job() is False
    assert fake.starts == []
    assert pending.load(pending_job_path()) is None


# ------------------------------------------------------------------- tray


def test_closing_while_busy_can_keep_the_job_in_the_tray(window, monkeypatch):
    main, _db, _ids = window
    main._add_running = True
    try:
        monkeypatch.setattr(
            QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True)
        )
        monkeypatch.setattr(main, "_ask_close_action", lambda: "tray")
        monkeypatch.setattr(main, "_ensure_window_tray", lambda: True)

        event = QCloseEvent()
        main.closeEvent(event)

        assert event.isAccepted() is False
        assert main.isHidden() is True
    finally:
        main._add_running = False


def test_going_back_from_the_close_prompt_leaves_the_window_open(
    window, monkeypatch
):
    main, _db, _ids = window
    main._add_running = True
    try:
        monkeypatch.setattr(
            QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True)
        )
        monkeypatch.setattr(main, "_ask_close_action", lambda: "back")

        event = QCloseEvent()
        main.closeEvent(event)

        assert event.isAccepted() is False
        assert main.isHidden() is False
    finally:
        main._add_running = False


def test_quitting_from_the_tray_does_not_offer_the_tray_again(
    window, monkeypatch
):
    main, _db, _ids = window
    main._add_running = True
    main._quitting = True
    try:
        monkeypatch.setattr(
            QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True)
        )
        monkeypatch.setattr(
            QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No
        )

        event = QCloseEvent()
        main.closeEvent(event)

        assert event.isAccepted() is False
        assert main.isHidden() is False
    finally:
        main._add_running = False
        main._quitting = False


def test_a_tray_that_cannot_be_created_does_not_cancel_without_asking(
    window, monkeypatch
):
    """Choosing "keep working in the tray" must never silently stop the
    job just because the icon could not be made."""
    main, _db, _ids = window
    main._add_running = True
    asked = []

    def question(*args, **kwargs):
        asked.append(args)
        return QMessageBox.No

    try:
        monkeypatch.setattr(
            QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True)
        )
        monkeypatch.setattr(main, "_ask_close_action", lambda: "tray")
        monkeypatch.setattr(main, "_ensure_window_tray", lambda: False)
        monkeypatch.setattr(QMessageBox, "question", question)
        monkeypatch.setattr(
            main, "_cancel",
            lambda: (_ for _ in ()).throw(AssertionError("job was cancelled")),
        )

        event = QCloseEvent()
        main.closeEvent(event)

        assert asked, "the user should have been asked before cancelling"
        assert event.isAccepted() is False
        assert main.isHidden() is False
    finally:
        main._add_running = False
