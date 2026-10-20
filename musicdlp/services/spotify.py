"""Spotify Desktop Local Files delivery.

Spotify has no public API for uploading local files or adding
``spotify:local:`` tracks to a playlist. What an external app can do reliably
is put correctly tagged files into a folder the user has added under
Spotify Settings -> Local Files, and restart Spotify so it rescans.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("musicdlp.spotify")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class SyncResult:
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.copied + self.skipped + self.failed


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    programfiles = os.environ.get("PROGRAMFILES")
    programfiles_x86 = os.environ.get("PROGRAMFILES(X86)")

    if appdata:
        paths.append(Path(appdata) / "Spotify" / "Spotify.exe")
    if localappdata:
        paths.append(Path(localappdata) / "Microsoft" / "WindowsApps" / "Spotify.exe")
    if programfiles:
        paths.append(Path(programfiles) / "Spotify" / "Spotify.exe")
    if programfiles_x86:
        paths.append(Path(programfiles_x86) / "Spotify" / "Spotify.exe")

    found = shutil.which("spotify")
    if found:
        paths.append(Path(found))
    return paths


class SpotifyService:
    def find_exe(self) -> Optional[Path]:
        for path in _candidate_paths():
            if path.exists():
                return path
        return None

    def is_installed(self) -> bool:
        return self.find_exe() is not None

    def is_running(self) -> bool:
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq Spotify.exe", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=CREATE_NO_WINDOW,
                )
                return "spotify.exe" in (result.stdout or "").lower()
            except (OSError, subprocess.SubprocessError):
                return False
        try:
            result = subprocess.run(
                ["pgrep", "-f", "Spotify"], capture_output=True, timeout=10
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def start(self) -> bool:
        try:
            if os.name == "nt":
                os.startfile("spotify:")  # type: ignore[attr-defined]
                return True
            exe = self.find_exe()
            if exe:
                subprocess.Popen([str(exe)])
                return True
        except OSError as exc:
            logger.warning("Could not start Spotify via URI: %s", exc)

        exe = self.find_exe()
        if exe:
            try:
                subprocess.Popen([str(exe)])
                return True
            except OSError as exc:
                logger.error("Could not start Spotify: %s", exc)
        return False

    def restart(self, force: bool = False) -> tuple[bool, str]:
        """Restart Spotify to force a Local Files rescan."""
        if not self.is_running():
            started = self.start()
            return (
                started,
                "Spotify was not running; started it."
                if started else "Spotify is not installed or could not start.",
            )

        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/IM", "Spotify.exe"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            subprocess.run(["pkill", "-f", "Spotify"], capture_output=True)

        for _ in range(40):  # up to 8 seconds
            if not self.is_running():
                break
            time.sleep(0.2)
        else:
            if not force:
                return False, (
                    "Spotify did not close. Close it manually, then click "
                    "Restart Spotify again."
                )
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/IM", "Spotify.exe", "/F"],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                )
            else:
                subprocess.run(["pkill", "-9", "-f", "Spotify"], capture_output=True)
            time.sleep(1.0)

        started = self.start()
        return (
            started,
            "Spotify restarted; local files will rescan shortly."
            if started else "Spotify closed but could not be started again.",
        )

    def sync(
        self,
        files: Iterable[Path],
        destination: Path,
        mode: str = "copy",
    ) -> SyncResult:
        """Copy or move converted files into the Spotify source folder."""
        result = SyncResult()
        destination = Path(destination)
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result.failed += 1
            result.errors.append(f"Cannot create {destination}: {exc}")
            return result

        for source in files:
            source = Path(source)
            if not source.exists():
                result.failed += 1
                result.errors.append(f"Missing output: {source.name}")
                continue

            target = destination / source.name
            try:
                if target.exists() and self._same_file(source, target):
                    result.skipped += 1
                    continue
                if mode == "move":
                    shutil.move(str(source), str(target))
                else:
                    shutil.copy2(source, target)
                result.copied += 1
            except OSError as exc:
                result.failed += 1
                result.errors.append(f"{source.name}: {exc}")

        return result

    @staticmethod
    def _same_file(source: Path, target: Path) -> bool:
        try:
            if source.stat().st_size != target.stat().st_size:
                return False
            return abs(source.stat().st_mtime - target.stat().st_mtime) < 2
        except OSError:
            return False

    @staticmethod
    def open_folder(path: Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif os.uname().sysname == "Darwin":  # type: ignore[attr-defined]
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def open_spotify(self) -> bool:
        return self.start()

    def source_registered(self, folder: Path) -> Optional[bool]:
        """Best-effort check whether Spotify has this folder as a source.

        Spotify keeps its watched folders in a binary file. The format is
        not public, so this only looks for the folder path as text. It is
        used to decide whether to show a hint, never to block the user.

        Returns True, False, or None when the answer cannot be determined.
        """
        if os.name != "nt":
            return None
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return None

        candidates = [
            Path(appdata) / "Spotify" / "watch-sources.bnk",
            Path(appdata) / "Spotify" / "Users",
        ]
        payload = b""
        for candidate in candidates:
            try:
                if candidate.is_file():
                    payload += candidate.read_bytes()
                elif candidate.is_dir():
                    for item in candidate.glob("*.bnk"):
                        payload += item.read_bytes()
            except OSError:
                continue

        if not payload:
            return None

        folder = Path(folder)
        for variant in {
            str(folder),
            str(folder).replace("\\", "/"),
            str(folder.resolve()) if folder.exists() else str(folder),
        }:
            if variant.encode("utf-8", errors="ignore") in payload:
                return True
            if variant.encode("utf-16-le", errors="ignore") in payload:
                return True
        return False

    def folder_has_files(self, folder: Path) -> bool:
        try:
            return any(Path(folder).glob("*.mp3")) or any(
                Path(folder).glob("*.m4a")
            )
        except OSError:
            return False

    @staticmethod
    def setup_steps(destination: Path) -> str:
        return (
            "In Spotify Desktop:\n"
            "1. Click your profile picture -> Settings.\n"
            "2. Under Local Files, turn on \"Show local files\".\n"
            "3. Click \"Add a source\" and choose:\n"
            f"   {destination}\n"
            "4. Restart Spotify (the button above does this for you).\n"
            "5. Open Your Library -> Local Files to see the songs.\n\n"
            "Seeing songs you did not add? Spotify also lists your Music and "
            "Downloads folders by default. Switch those folders off in the "
            "same Local Files list to keep only Music DLP songs.\n\n"
            "Spotify does not allow other apps to add local songs to a "
            "playlist; drag them into a playlist inside Spotify if you want "
            "one."
        )
