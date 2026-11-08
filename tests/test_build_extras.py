"""What a release build puts next to the executable."""

from pathlib import Path

from build import copy_extras


def _fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("# readme", encoding="utf-8")
    (root / "INSTALL.txt").write_text("install", encoding="utf-8")
    panel = root / "spicetify" / "musicdlp-panel"
    panel.mkdir(parents=True)
    (panel / "index.js").write_text("var x = 1;", encoding="utf-8")
    (root / "spicetify" / "install-panel.ps1").write_text(
        "# installer", encoding="utf-8"
    )
    return root


def test_the_release_carries_the_panel_and_its_installer(tmp_path):
    """A download must be able to install the panel without the repo."""
    root = _fake_root(tmp_path)
    app_dir = tmp_path / "MusicDLP"
    app_dir.mkdir()

    copied = copy_extras(app_dir, root)

    assert "spicetify" in copied
    assert (app_dir / "spicetify" / "install-panel.ps1").is_file()
    assert (app_dir / "spicetify" / "musicdlp-panel" / "index.js").is_file()
    assert (app_dir / "README.md").is_file()
    assert (app_dir / "INSTALL.txt").is_file()


def test_a_missing_panel_folder_does_not_fail_the_build(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("# readme", encoding="utf-8")
    app_dir = tmp_path / "MusicDLP"
    app_dir.mkdir()

    copied = copy_extras(app_dir, root)

    assert copied == ["README.md"]
    assert not (app_dir / "spicetify").exists()


def test_an_old_panel_copy_is_replaced_not_merged(tmp_path):
    root = _fake_root(tmp_path)
    app_dir = tmp_path / "MusicDLP"
    stale = app_dir / "spicetify" / "musicdlp-panel"
    stale.mkdir(parents=True)
    (stale / "removed.js").write_text("old", encoding="utf-8")

    copy_extras(app_dir, root)

    assert not (stale / "removed.js").exists()
    assert (stale / "index.js").is_file()