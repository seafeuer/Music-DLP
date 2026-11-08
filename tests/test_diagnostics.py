"""The diagnostics report names the thing that is actually wrong."""

from pathlib import Path

from musicdlp.core.config import Settings
from musicdlp.services.diagnostics import (
    FAIL, OK, UNKNOWN, WARN, Check, report, run_checks,
)


class FakeFFmpeg:
    def __init__(self, available=True):
        self.available = available

    def version(self):
        return "6.1.1" if self.available else "not found"


class FakeYtDlp:
    def version(self):
        return "2026.01.01"


class FakeSpotify:
    def __init__(self, exe=True, registered=True):
        self.exe = exe
        self.registered = registered

    def find_exe(self):
        return Path("C:/Spotify/Spotify.exe") if self.exe else None

    def source_registered(self, folder):
        return self.registered


def _settings(tmp_path, **values):
    defaults = {
        "output_dir": str(tmp_path / "Music"),
        "spotify_dir": str(tmp_path / "Spotify"),
    }
    defaults.update(values)
    return Settings(**defaults)


def _by_name(checks):
    return {check.name: check for check in checks}


def test_missing_ffmpeg_is_a_failure_with_the_fix(tmp_path):
    checks = _by_name(run_checks(
        _settings(tmp_path), FakeFFmpeg(False), FakeYtDlp(), FakeSpotify(),
        None, appdata=tmp_path,
    ))

    assert checks["FFmpeg"].status == FAIL
    assert "winget" in checks["FFmpeg"].detail


def test_a_working_setup_reports_the_folders_and_versions(tmp_path):
    checks = _by_name(run_checks(
        _settings(tmp_path), FakeFFmpeg(), FakeYtDlp(), FakeSpotify(),
        None, appdata=tmp_path,
    ))

    assert checks["FFmpeg"].status == OK
    assert checks["yt-dlp"].detail == "2026.01.01"
    assert checks["Songs folder"].status == OK
    assert checks["Spotify folder"].status == OK


def test_an_unregistered_spotify_folder_is_explained_not_hidden(tmp_path):
    checks = _by_name(run_checks(
        _settings(tmp_path), FakeFFmpeg(), FakeYtDlp(),
        FakeSpotify(registered=False), None, appdata=tmp_path,
    ))

    assert checks["Local Files folder"].status == WARN
    assert "Add a source" in checks["Local Files folder"].detail


def test_an_unknown_spotify_folder_is_not_called_a_failure(tmp_path):
    checks = _by_name(run_checks(
        _settings(tmp_path), FakeFFmpeg(), FakeYtDlp(),
        FakeSpotify(registered=None), None, appdata=tmp_path,
    ))

    assert checks["Local Files folder"].status == UNKNOWN


def test_a_cookies_file_that_is_gone_is_a_warning(tmp_path):
    checks = _by_name(run_checks(
        _settings(
            tmp_path, cookies_mode="file",
            cookies_file=str(tmp_path / "missing.txt"),
        ),
        FakeFFmpeg(), FakeYtDlp(), FakeSpotify(), None, appdata=tmp_path,
    ))

    assert checks["Cookies"].status == WARN
    assert "missing.txt" in checks["Cookies"].detail


def test_the_bridge_says_where_it_listens_or_why_it_is_off(tmp_path):
    class Bridge:
        running = True
        port = 8317

    checks = _by_name(run_checks(
        _settings(tmp_path), FakeFFmpeg(), FakeYtDlp(), FakeSpotify(),
        Bridge(), appdata=tmp_path,
    ))
    assert checks["Spotify panel bridge"].status == OK
    assert "8317" in checks["Spotify panel bridge"].detail

    checks = _by_name(run_checks(
        _settings(tmp_path), FakeFFmpeg(), FakeYtDlp(), FakeSpotify(),
        None, appdata=tmp_path,
    ))
    assert checks["Spotify panel bridge"].status == FAIL
    assert "not listening" in checks["Spotify panel bridge"].detail


def test_the_panel_check_finds_an_installed_panel(tmp_path):
    target = tmp_path / "spicetify" / "CustomApps" / "musicdlp" / "index.js"
    target.parent.mkdir(parents=True)
    target.write_text("// panel", encoding="utf-8")

    checks = _by_name(run_checks(
        _settings(tmp_path), FakeFFmpeg(), FakeYtDlp(), FakeSpotify(),
        None, appdata=tmp_path,
    ))

    assert checks["Panel installed"].status == OK


def test_the_report_has_one_readable_line_per_check():
    text = report([
        Check("FFmpeg", OK, "6.1.1"),
        Check("Cookies", WARN, "No file chosen"),
        Check("AI", FAIL, "No model"),
    ])

    assert text.startswith("Music DLP")
    assert "[✓] FFmpeg — 6.1.1" in text
    assert "[!] Cookies — No file chosen" in text
    assert "[✗] AI — No model" in text