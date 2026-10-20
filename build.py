"""Build a standalone Windows app with PyInstaller.

Usage:
    python build.py            # build dist/MusicDLP/
    python build.py --clean    # remove build artifacts first
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
NAME = "MusicDLP"


def clean() -> None:
    for folder in (DIST, BUILD):
        if folder.exists():
            print(f"Removing {folder}")
            shutil.rmtree(folder)


def build() -> Path:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run: pip install pyinstaller")
        raise SystemExit(1)

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--name", NAME,
        "--collect-all", "yt_dlp",
        "--collect-submodules", "musicdlp",
        str(ROOT / "main.py"),
    ]
    print("Running:", " ".join(command))
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit("Build failed")

    app_dir = DIST / NAME
    ffmpeg_dir = app_dir / "ffmpeg"
    ffmpeg_dir.mkdir(exist_ok=True)
    (ffmpeg_dir / "PUT_FFMPEG_HERE.txt").write_text(
        "Copy ffmpeg.exe (and ffprobe.exe) into this folder, or install\n"
        "FFmpeg system-wide with: winget install Gyan.FFmpeg\n",
        encoding="utf-8",
    )

    for name in ("README.md", "INSTALL.txt"):
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, app_dir / name)

    print(f"\nBuilt: {app_dir}")
    return app_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Music DLP")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    if args.clean:
        clean()
    build()


if __name__ == "__main__":
    main()
