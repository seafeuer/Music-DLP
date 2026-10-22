"""Build a standalone Windows app with PyInstaller.

Usage:
    python build.py              # build dist/MusicDLP/ (a folder)
    python build.py --onefile    # also build dist/MusicDLP.exe (one file)
    python build.py --clean      # remove build artifacts first

Both forms include Python and every library the app needs. FFmpeg is a
separate program: put ffmpeg.exe in the ffmpeg\\ folder next to the app, or
install it system-wide. When a local ffmpeg.exe can be found, the folder
build copies it in so the result works with nothing else installed.
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


def _command(onefile: bool) -> list[str]:
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--name", NAME,
        "--collect-all", "yt_dlp",
        "--collect-submodules", "musicdlp",
    ]
    if onefile:
        command.append("--onefile")
    command.append(str(ROOT / "main.py"))
    return command


def _local_ffmpeg() -> tuple[Path | None, Path | None]:
    """A real ffmpeg.exe and ffprobe.exe on this machine, if any."""
    try:
        sys.path.insert(0, str(ROOT))
        from musicdlp.services.ffmpeg import find_ffmpeg, find_ffprobe

        ffmpeg = find_ffmpeg()
        probe = find_ffprobe(ffmpeg)
        return ffmpeg, probe
    except Exception as exc:  # noqa: BLE001 - a build must not fail on this
        print(f"Could not look for FFmpeg: {exc}")
        return None, None


MIN_REAL_FFMPEG = 5_000_000


def _is_real(path: Path | None) -> bool:
    """Whether this is an actual FFmpeg, not a package-manager shim.

    Chocolatey puts a ~400 KB launcher on PATH that forwards to the real
    binary elsewhere. Bundling the launcher gives a folder that does not
    work on another computer.
    """
    try:
        return path is not None and path.is_file() and \
            path.stat().st_size >= MIN_REAL_FFMPEG
    except OSError:
        return False


def _real_ffmpeg(found: Path | None) -> tuple[Path | None, Path | None]:
    """The real ffmpeg.exe and ffprobe.exe, looking past a shim if needed."""
    if _is_real(found):
        probe = found.with_name(found.name.replace("ffmpeg", "ffprobe"))
        return found, probe if probe.exists() else None

    # Package managers keep the real binaries under their own tree.
    roots = [
        Path(r"C:\ProgramData\chocolatey\lib"),
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages",
        Path(r"C:\ffmpeg"),
    ]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for candidate in root.rglob("ffmpeg.exe"):
                if _is_real(candidate):
                    probe = candidate.with_name("ffprobe.exe")
                    return candidate, probe if probe.exists() else None
        except OSError:
            continue
    return None, None


def _add_ffmpeg(app_dir: Path) -> None:
    ffmpeg_dir = app_dir / "ffmpeg"
    ffmpeg_dir.mkdir(exist_ok=True)
    ffmpeg, probe = _local_ffmpeg()
    if ffmpeg is not None and _is_real(ffmpeg):
        shutil.copy2(ffmpeg, ffmpeg_dir / ffmpeg.name)
        print(f"Bundled {ffmpeg.name} ({ffmpeg.stat().st_size // 1_000_000} MB)")
        if probe is not None and probe.exists():
            shutil.copy2(probe, ffmpeg_dir / probe.name)
            print(f"Bundled {probe.name}")
    else:
        print("No real ffmpeg.exe found to bundle; leaving instructions.")
        (ffmpeg_dir / "PUT_FFMPEG_HERE.txt").write_text(
            "Copy ffmpeg.exe (and ffprobe.exe) into this folder, or install\n"
            "FFmpeg system-wide with: winget install Gyan.FFmpeg\n",
            encoding="utf-8",
        )


def build(onefile: bool = False) -> Path:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Run: pip install pyinstaller")
        raise SystemExit(1)

    command = _command(onefile)
    print("Running:", " ".join(command))
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit("Build failed")

    app_dir = DIST / NAME
    if onefile:
        print(f"\nBuilt: {DIST / (NAME + '.exe')}")
        return DIST / (NAME + ".exe")

    _add_ffmpeg(app_dir)
    for name in ("README.md", "INSTALL.txt"):
        source = ROOT / name
        if source.exists():
            shutil.copy2(source, app_dir / name)

    print(f"\nBuilt: {app_dir}")
    return app_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Music DLP")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument(
        "--onefile", action="store_true",
        help="build a single MusicDLP.exe instead of a folder",
    )
    args = parser.parse_args()
    if args.clean:
        clean()
    build(onefile=args.onefile)


if __name__ == "__main__":
    main()
