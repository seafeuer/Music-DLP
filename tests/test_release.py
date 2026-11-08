"""The release steps are code, not a checklist in someone's head."""

from pathlib import Path

import pytest

import release


def test_the_version_is_rewritten_in_the_package():
    text = '"""Music DLP."""\n\n__version__ = "2.11.0"\nAPP_NAME = "MusicDLP"\n'

    assert '2.12.0' in release.bump_init(text, "2.12.0")
    assert '2.11.0' not in release.bump_init(text, "2.12.0")


def test_the_version_is_rewritten_in_pyproject():
    text = '[project]\nname = "musicdlp"\nversion = "2.11.0"\n'

    updated = release.bump_pyproject(text, "2.12.0")

    assert 'version = "2.12.0"' in updated
    assert 'version = "2.11.0"' not in updated


def test_the_readme_status_line_follows_the_version():
    text = "**Version 2.11.0 — working.** The base app is verified.\n"

    updated = release.bump_readme(text, "2.12.0")

    assert "**Version 2.12.0 — working.**" in updated


def test_the_changelog_gets_a_dated_heading_at_the_top():
    text = "# Changelog\n\n## 2.11.0 — 2026-11-08\n\n- Old.\n"

    updated = release.bump_changelog(text, "2.12.0", today="2026-12-01")

    assert updated.index("## 2.12.0 — 2026-12-01") < updated.index("## 2.11.0")
    assert "## 2.11.0 — 2026-11-08" in updated


def test_an_unreleased_heading_is_promoted_not_duplicated():
    text = "# Changelog\n\n## Unreleased\n\n- New work.\n"

    updated = release.bump_changelog(text, "2.12.0", today="2026-12-01")

    assert "## Unreleased" not in updated
    assert "## 2.12.0 — 2026-12-01" in updated
    assert "- New work." in updated


def test_bumping_a_missing_marker_refuses_instead_of_releasing(monkeypatch):
    with pytest.raises(SystemExit):
        release.bump_init("no version here", "2.12.0")
    with pytest.raises(SystemExit):
        release.bump_pyproject("[project]\n", "2.12.0")


def test_checksums_cover_every_file_and_leave_themselves_out(tmp_path):
    app = tmp_path / "MusicDLP"
    (app / "ffmpeg").mkdir(parents=True)
    (app / "MusicDLP.exe").write_bytes(b"exe")
    (app / "ffmpeg" / "ffmpeg.exe").write_bytes(b"bin")

    target = release.write_checksums(app)
    first = target.read_text(encoding="utf-8")
    second = release.write_checksums(app).read_text(encoding="utf-8")

    assert "MusicDLP.exe" in first
    assert "ffmpeg/ffmpeg.exe" in first
    assert "SHA256SUMS.txt" not in first
    assert first == second
    assert release.sha256(app / "MusicDLP.exe") in first

def test_the_release_archive_has_one_top_level_folder(tmp_path, monkeypatch):
    import zipfile

    app = tmp_path / "dist" / "MusicDLP"
    app.mkdir(parents=True)
    (app / "MusicDLP.exe").write_bytes(b"exe")
    (app / "spicetify").mkdir()
    (app / "spicetify" / "install-panel.ps1").write_text("Write-Host", encoding="utf-8")

    target = release.write_archive(app, "2.12.0")

    assert target.name == "MusicDLP-2.12.0.zip"
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
    assert "MusicDLP/MusicDLP.exe" in names
    assert "MusicDLP/spicetify/install-panel.ps1" in names
    assert not any(name.startswith("dist/") for name in names)


def test_a_failed_build_restores_the_version_files(tmp_path, monkeypatch):
    import sys

    init = tmp_path / "__init__.py"
    init.write_text('__version__ = "2.11.0"\n', encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "2.11.0"\n', encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("**Version 2.11.0 — working.**\n", encoding="utf-8")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 2.11.0\n\n- Old.\n", encoding="utf-8")

    monkeypatch.setattr(release, "INIT", init)
    monkeypatch.setattr(release, "PYPROJECT", pyproject)
    monkeypatch.setattr(release, "README", readme)
    monkeypatch.setattr(release, "CHANGELOG", changelog)
    monkeypatch.setattr(release, "run_tests", lambda: None)
    monkeypatch.setattr(
        release, "build",
        lambda: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(sys, "argv", ["release.py", "--version", "9.9.9"])

    with pytest.raises(OSError):
        release.main()

    assert '2.11.0' in init.read_text(encoding="utf-8")
    assert '9.9.9' not in init.read_text(encoding="utf-8")
    assert '9.9.9' not in changelog.read_text(encoding="utf-8")
