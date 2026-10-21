"""Closing the window must not undo settings changed outside the app."""

import pytest

from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.ui.main_window import MainWindow, build_context


@pytest.fixture()
def window(qtbot, qapp, tmp_path):
    settings = Settings(
        theme="dark",
        mode="easy",
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
        first_run_done=True,
        notifications=False,
    )
    settings.save()
    db = Database(tmp_path / "library.db")
    import logging

    ctx = build_context(settings, db, logging.getLogger("test"), )
    main = MainWindow(ctx)
    qtbot.addWidget(main)
    yield main, settings, tmp_path
    db.close()


def test_closing_preserves_a_folder_changed_elsewhere(window):
    main, settings, tmp_path = window

    # Someone edits the config while the window is open (for example the
    # user changes the Spotify folder, or another tool repairs it).
    on_disk = Settings.load()
    new_folder = tmp_path / "chosen"
    on_disk.spotify_dir = str(new_folder)
    on_disk.output_dir = str(tmp_path / "chosen-out")
    on_disk.save()

    main._save_window_state()

    reloaded = Settings.load()
    assert reloaded.spotify_dir == str(new_folder)
    assert reloaded.output_dir == str(tmp_path / "chosen-out")
    # The window state this session owns is still saved.
    assert reloaded.window_geometry
