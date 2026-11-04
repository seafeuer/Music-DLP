"""The setup wizard's pages, and a banner that stays dismissed."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from musicdlp.core.config import Settings
from musicdlp.services.ffmpeg import FFmpegService
from musicdlp.ui.dialogs.setup import SetupDialog
from musicdlp.ui.widgets.setup_banner import SetupBanner


class FakeSpotify:
    def __init__(self, registered=None, installed=True, has_files=False) -> None:
        self.registered = registered
        self.installed = installed
        self.has_files = has_files

    def is_installed(self) -> bool:
        return self.installed

    def source_registered(self, folder):
        return self.registered

    def folder_has_files(self, folder):
        return self.has_files


@pytest.fixture()
def dialog(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    settings = Settings(
        theme="dark",
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
        ffmpeg_path=str(tmp_path / "missing-ffmpeg.exe"),
        first_run_done=True,
        notifications=False,
    )
    wizard = SetupDialog(
        settings, FFmpegService(settings.ffmpeg_path), FakeSpotify()
    )
    qtbot.addWidget(wizard)
    wizard.show()
    qtbot.wait(30)
    return wizard


def test_setup_is_a_five_step_wizard(dialog):
    assert len(dialog.cards) == 5
    assert dialog.stack.currentIndex() == 0
    assert dialog.step_label.text() == "Step 1 of 5"
    assert not dialog.back_button.isEnabled()
    assert dialog.footer.primary_button.text() == "Next"
    assert dialog.footer.primary_button.isDefault()

    for expected in (1, 2, 3, 4):
        dialog.footer.primary_button.click()
        assert dialog.stack.currentIndex() == expected
        assert dialog.stack.currentWidget() is dialog.cards[expected][1]
        assert dialog.step_label.text() == f"Step {expected + 1} of 5"

    # The last page has one way forward, and it finishes.
    assert dialog.footer.primary_button.text() == "Finish"
    assert dialog.back_button.isEnabled()

    dialog._back()
    assert dialog.stack.currentIndex() == 3
    assert dialog.footer.primary_button.text() == "Next"


def test_return_advances_instead_of_closing(dialog, qtbot):
    """Enter used to press "Save and close" and skip the whole wizard."""
    dialog.activateWindow()
    qtbot.keyClick(dialog, Qt.Key_Return)
    assert dialog.stack.currentIndex() == 1
    assert dialog.isVisible()
    assert not dialog.result()


def test_finish_saves_and_closes(dialog):
    for _ in range(4):
        dialog.footer.primary_button.click()
    assert dialog.footer.primary_button.text() == "Finish"
    dialog.footer.primary_button.click()
    assert dialog.result() == QDialog.Accepted
    assert dialog.saved is True


def test_banner_dismiss_reports_its_kind_and_hides(qtbot):
    banner = SetupBanner()
    qtbot.addWidget(banner)
    kinds = []
    banner.dismissed.connect(kinds.append)

    banner.show_prompt("ffmpeg", "Needs FFmpeg", "Set up FFmpeg")
    assert banner.isVisible()
    assert banner.kind == "ffmpeg"

    banner.close_button.click()
    assert kinds == ["ffmpeg"]
    assert not banner.isVisible()
    assert banner.kind is None

    banner.show_prompt("source", "Spotify may not know", "Show me how")
    assert banner.kind == "source"



def _wizard(tmp_path, monkeypatch, spotify):
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    settings = Settings(
        theme="dark",
        output_dir=str(tmp_path / "out"),
        spotify_dir=str(tmp_path / "spotify"),
        ffmpeg_path=str(tmp_path / "missing-ffmpeg.exe"),
        first_run_done=True,
        notifications=False,
    )
    return SetupDialog(
        settings, FFmpegService(settings.ffmpeg_path), spotify
    )


def test_repair_tools_wait_until_there_are_songs(qtbot, tmp_path, monkeypatch):
    wizard = _wizard(tmp_path, monkeypatch, FakeSpotify(has_files=False))
    qtbot.addWidget(wizard)
    wizard.show()
    assert wizard.picture_box.isHidden()

    with_songs = _wizard(tmp_path, monkeypatch, FakeSpotify(has_files=True))
    qtbot.addWidget(with_songs)
    with_songs.show()
    assert not with_songs.picture_box.isHidden()


def test_install_button_is_hidden_when_ffmpeg_is_ready(
    qtbot, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        FFmpegService, "available", property(lambda self: True)
    )
    wizard = _wizard(tmp_path, monkeypatch, FakeSpotify())
    qtbot.addWidget(wizard)
    wizard.show()

    assert wizard.install_button.isHidden()
