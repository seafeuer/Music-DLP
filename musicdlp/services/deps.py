"""Dependency detection and one-click installation helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from musicdlp.services.ffmpeg import find_ffmpeg

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

ProgressCallback = Callable[[str], None]


def winget_path() -> Optional[Path]:
    found = shutil.which("winget")
    if found:
        return Path(found)
    candidate = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft" / "WindowsApps" / "winget.exe"
    )
    return candidate if candidate.exists() else None


def has_winget() -> bool:
    return winget_path() is not None


def node_available() -> bool:
    return shutil.which("node") is not None


def _winget_ffmpeg_candidates() -> list[Path]:
    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if packages.exists():
            for package in packages.glob("Gyan.FFmpeg*"):
                candidates.extend(package.rglob("ffmpeg.exe"))
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / "ffmpeg" / "bin" / "ffmpeg.exe")
    return candidates


def locate_installed_ffmpeg() -> Optional[Path]:
    """Find FFmpeg after an install, including places not yet on PATH."""
    found = find_ffmpeg("")
    if found:
        return found
    for candidate in _winget_ffmpeg_candidates():
        if candidate.exists():
            return candidate
    return None


def install_ffmpeg(
    progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[bool, str, Optional[Path]]:
    """Install FFmpeg with winget.

    Returns (success, message, ffmpeg_path).
    """
    existing = locate_installed_ffmpeg()
    if existing is not None:
        return True, f"FFmpeg is already installed at:\n{existing}", existing

    winget = winget_path()
    if winget is None:
        return (
            False,
            "The automatic installer (winget) is not available on this PC.\n"
            "Use \"Download FFmpeg\" instead.",
            None,
        )

    command = [
        str(winget), "install", "--id", "Gyan.FFmpeg", "--exact",
        "--accept-package-agreements", "--accept-source-agreements",
        "--silent", "--disable-interactivity",
    ]
    if progress:
        progress("Downloading FFmpeg. This can take a few minutes…")

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        return False, f"Could not start the installer:\n{exc}", None

    output: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        output.append(line)
        if progress:
            progress(line)
        if cancel_event is not None and cancel_event.is_set():
            process.kill()
            return False, "Installation cancelled.", None

    process.wait()
    found = locate_installed_ffmpeg()
    if found is not None:
        return True, f"FFmpeg installed at:\n{found}", found

    tail = "\n".join(output[-8:])
    return (
        False,
        "FFmpeg could not be installed automatically.\n\n"
        f"{tail}\n\n"
        "Use \"Download FFmpeg\" to install it yourself.",
        None,
    )
