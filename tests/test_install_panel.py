"""The panel installer, run the way a new user runs it.

A offline copy of the installer is easy to get wrong: a hard-coded path, a
folder it never creates, a config line it forgets to register. This runs
the real PowerShell script against a temporary APPDATA and a stub
spicetify.exe, with -SkipApply so it cannot close Spotify or touch the
user's real installation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "spicetify" / "install-panel.ps1"


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(os.name != "nt", reason="the installer is PowerShell")
def test_the_installer_registers_a_new_install_without_touching_the_real_one(
    tmp_path,
):
    powershell = _powershell()
    if powershell is None:
        pytest.skip("PowerShell is not available")

    appdata = tmp_path / "appdata"
    spicetify_root = appdata / "spicetify"
    spicetify_root.mkdir(parents=True)
    (spicetify_root / "config-xpui.ini").write_text(
        "[Setting]\ncustom_apps           = \n", encoding="utf-8"
    )

    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "data" / "config.json").write_text(
        # Deliberately minimal: a config written before the bridge existed
        # has no bridge_token key, and setting it used to throw.
        json.dumps({"first_run_done": True}),
        encoding="utf-8",
    )

    # A stub CLI: Get-Command finds it on PATH. -SkipApply means it is
    # never run, so it only has to exist.
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "spicetify.cmd").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")

    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    env["MUSICDLP_HOME"] = str(home)
    env["USERPROFILE"] = str(tmp_path / "profile")
    env["PATH"] = str(tools) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        [
            powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPT), "-SkipApply",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    dest = spicetify_root / "CustomApps" / "musicdlp"
    panel = (dest / "index.js").read_text(encoding="utf-8")
    assert "var MUSICDLP_CONFIG" in panel
    assert "8317" in panel
    written = json.loads(
        (home / "data" / "config.json").read_text(encoding="utf-8-sig")
    )
    assert written.get("bridge_token")
    assert written["bridge_token"] in panel
    assert (dest / "style.css").is_file()
    assert (dest / "manifest.json").is_file()

    ini = (spicetify_root / "config-xpui.ini").read_text(encoding="utf-8")
    assert "musicdlp" in ini.split("custom_apps", 1)[1].splitlines()[0]


@pytest.mark.skipif(os.name != "nt", reason="the installer is PowerShell")
def test_the_installer_explains_a_missing_config_instead_of_installing(tmp_path):
    powershell = _powershell()
    if powershell is None:
        pytest.skip("PowerShell is not available")

    appdata = tmp_path / "appdata"
    (appdata / "spicetify").mkdir(parents=True)
    (appdata / "spicetify" / "config-xpui.ini").write_text(
        "[Setting]\n", encoding="utf-8"
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "spicetify.cmd").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")

    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    env["MUSICDLP_HOME"] = str(tmp_path / "empty-home")
    env["USERPROFILE"] = str(tmp_path / "profile")
    env["PATH"] = str(tools) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        [
            powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPT), "-SkipApply",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "config.json" in (result.stderr + result.stdout)
    assert "Run the Music DLP app once" in (result.stderr + result.stdout)