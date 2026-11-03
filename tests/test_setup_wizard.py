"""The setup wizard's pages, and a banner that stays dismissed."""

import pytest
from PySide6.QtWidgets import QDialog

from musicdlp.core.config import Settings
from musicdlp.services.ffmpeg import FFmpegService
from musicdlp.ui.dialogs.setup import SetupDialog
from musicdlp.ui.widgets.setup_banner import SetupBanner


class FakeSpotify:
    def __init__(self, registered=None, installed=True) -> None:
        self.registered = registered
        self.installed = installed

    def is_installed(self) -> bool:
        return self.installed

    def source_registered(self, folder):
        return self.registered


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
    assert not dialog.next_button.isHidden()
    assert dialog.footer.primary_button.text() == "Save and close"

    for expected in (1, 2, 3, 4):
        dialog._next()
        assert dialog.stack.currentIndex() == expected
        assert dialog.stack.currentWidget() is dialog.cards[expected][1]
        assert dialog.step_label.text() == f"Step {expected + 1} of 5"

    # The last page has one way forward, not two.
    assert dialog.next_button.isHidden()
    assert dialog.footer.primary_button.text() == "Finish"
    assert dialog.back_button.isEnabled()

    dialog._back()
    assert dialog.stack.currentIndex() == 3
    assert not dialog.next_button.isHidden()
    assert dialog.footer.primary_button.text() == "Save and close"


def test_finish_saves_and_closes(dialog):
    for _ in range(4):
        dialog._next()
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

