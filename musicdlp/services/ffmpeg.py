"""FFmpeg discovery, probing, loudness measurement, and conversion."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from musicdlp.core.errors import AppError, Cancelled, classify_ffmpeg_error

logger = logging.getLogger("musicdlp.ffmpeg")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MIN_OUTPUT_BYTES = 10 * 1024
LOUDNORM = "loudnorm=I=-14:TP=-1:LRA=11"


def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def find_ffmpeg(configured: str = "") -> Optional[Path]:
    """Locate ffmpeg.exe in the configured path, app folder, or PATH."""
    candidates: list[Path] = []
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            candidates.append(configured_path)
        elif configured_path.is_dir():
            candidates.append(configured_path / _exe("ffmpeg"))

    from musicdlp.core.paths import app_root

    root = app_root()
    candidates.extend([
        root / _exe("ffmpeg"),
        root / "ffmpeg" / _exe("ffmpeg"),
        root / "bin" / _exe("ffmpeg"),
    ])

    found = shutil.which("ffmpeg")
    if found:
        candidates.append(Path(found))

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def find_ffprobe(ffmpeg_path: Optional[Path] = None) -> Optional[Path]:
    candidates: list[Path] = []
    if ffmpeg_path:
        candidates.append(Path(ffmpeg_path).with_name(_exe("ffprobe")))
    found = shutil.which("ffprobe")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


class FFmpegService:
    def __init__(self, configured_ffmpeg: str = "") -> None:
        self.ffmpeg = find_ffmpeg(configured_ffmpeg)
        self.ffprobe = find_ffprobe(self.ffmpeg)

    @property
    def available(self) -> bool:
        return self.ffmpeg is not None

    def version(self) -> str:
        if not self.ffmpeg:
            return "not found"
        try:
            result = subprocess.run(
                [str(self.ffmpeg), "-version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return "not working"
        output = result.stdout or result.stderr or ""
        first = output.splitlines()[0].strip() if output else ""
        return first if result.returncode == 0 else "not working"

    # ------------------------------------------------------------------ probe

    def probe(self, path: Path) -> dict:
        if not self.ffprobe:
            return {}
        code, out = self._run(
            [
                str(self.ffprobe), "-v", "error",
                "-show_entries", "format=duration,size,format_name",
                "-of", "json", str(path),
            ],
            timeout=30,
        )
        if code != 0:
            return {}
        try:
            data = json.loads(out)
        except ValueError:
            return {}
        fmt = data.get("format", {})
        try:
            duration = float(fmt.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        return {
            "duration": duration,
            "size": int(fmt.get("size") or 0),
            "format": fmt.get("format_name", ""),
        }

    # --------------------------------------------------------------- convert

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        *,
        fmt: str,
        bitrate: str = "320",
        normalize: bool = True,
        cancel_event=None,
        progress_cb=None,
    ) -> Path:
        if not self.ffmpeg:
            raise AppError(
                "FFmpeg was not found.",
                "Set the FFmpeg path in Settings, place ffmpeg.exe next to "
                "the app, or install it with: winget install Gyan.FFmpeg",
                kind="ffmpeg",
            )

        input_path = Path(input_path)
        output_path = Path(output_path)
        if not input_path.exists():
            raise AppError("The downloaded audio file is missing.",
                           "Download the track again.", kind="file")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_name(
            f"{output_path.stem}.tmp{output_path.suffix}"
        )
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)

        def report(value: float) -> None:
            if progress_cb is not None:
                try:
                    progress_cb(max(0.0, min(100.0, float(value))))
                except Exception:
                    # Progress is cosmetic; a broken callback must not
                    # stop a conversion.
                    logger.debug("Progress callback failed", exc_info=True)

        report(2)

        filters: list[str] = []
        if normalize:
            measured = self.measure_loudness(
                input_path,
                cancel_event=cancel_event,
                progress_cb=(
                    lambda p: report(2 + 23 * p / 100.0)
                    if progress_cb is not None else None
                ),
            )
            if measured:
                filters.append(
                    f"{LOUDNORM}:measured_I={measured['input_i']}"
                    f":measured_TP={measured['input_tp']}"
                    f":measured_LRA={measured['input_lra']}"
                    f":measured_thresh={measured['input_thresh']}"
                    f":offset={measured['target_offset']}:linear=true"
                )
        report(25)

        cmd = [
            str(self.ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-y", "-i", str(input_path), "-vn", "-map_metadata", "-1",
        ]
        if progress_cb is not None:
            cmd += ["-progress", "pipe:1", "-nostats"]
        if filters:
            cmd += ["-af", ",".join(filters)]

        if fmt == "mp3":
            cmd += ["-c:a", "libmp3lame", "-b:a", f"{bitrate}k",
                    "-id3v2_version", "3"]
        elif fmt == "m4a":
            cmd += ["-c:a", "aac", "-b:a", f"{bitrate}k",
                    "-movflags", "+faststart"]
        else:
            raise AppError(f"Unsupported output format: {fmt}", kind="ffmpeg")

        cmd.append(str(temp_path))

        duration = self.probe(input_path).get("duration", 0)
        timeout = max(120.0, float(duration) * 3 + 60)
        try:
            code, stderr = self._run(
                cmd,
                timeout=timeout,
                cancel_event=cancel_event,
                progress_cb=(
                    lambda p: report(25 + 74 * p / 100.0)
                    if progress_cb is not None else None
                ),
                duration=duration,
            )
        except (Cancelled, AppError):
            # A cancelled or timed-out encode leaves a partial .tmp file
            # in the user's output folder on every attempt.
            temp_path.unlink(missing_ok=True)
            raise
        report(100)

        if code != 0:
            temp_path.unlink(missing_ok=True)
            raise classify_ffmpeg_error(stderr)

        if not temp_path.exists() or temp_path.stat().st_size < MIN_OUTPUT_BYTES:
            temp_path.unlink(missing_ok=True)
            raise AppError(
                "FFmpeg produced an empty or truncated file.",
                "Try converting again. If it repeats, the source file may be "
                "corrupt.",
                kind="ffmpeg",
            )

        info = self.probe(temp_path)
        if info and info.get("duration", 0) <= 0:
            temp_path.unlink(missing_ok=True)
            raise AppError(
                "The converted file could not be read back.",
                "Try converting again.",
                kind="ffmpeg",
            )

        os.replace(temp_path, output_path)
        return output_path

    # ---------------------------------------------------------- loudness pass

    def measure_loudness(
        self, input_path: Path, cancel_event=None, progress_cb=None
    ) -> Optional[dict]:
        """First loudnorm pass. Returns None when measurement is unusable."""
        if not self.ffmpeg:
            return None
        duration = self.probe(input_path).get("duration", 0)
        timeout = max(120.0, float(duration) * 2 + 60)
        cmd = [
            str(self.ffmpeg), "-nostdin", "-hide_banner", "-loglevel", "info",
            "-i", str(input_path), "-af", f"{LOUDNORM}:print_format=json",
            "-f", "null", "-",
        ]
        if progress_cb is not None:
            cmd += ["-progress", "pipe:1", "-nostats"]
        try:
            code, stderr = self._run(
                cmd,
                timeout=timeout,
                cancel_event=cancel_event,
                progress_cb=progress_cb,
                duration=duration,
            )
        except Cancelled:
            raise
        except AppError:
            return None

        if code != 0:
            logger.debug("Loudness measurement failed; converting without it")
            return None

        match = re.search(r'\{[^{}]*"input_i"[^{}]*\}', stderr, re.DOTALL)
        if not match:
            return None

        try:
            data = json.loads(match.group(0))
        except ValueError:
            return None

        if str(data.get("input_i", "")).strip() in ("-inf", "inf", "nan", "-nan"):
            return None

        required = ("input_i", "input_tp", "input_lra", "input_thresh",
                    "target_offset")
        if not all(key in data for key in required):
            return None
        return data

    # ---------------------------------------------------------------- runner

    def _run(
        self,
        cmd: list[str],
        timeout: float = 120.0,
        cancel_event=None,
        progress_cb=None,
        duration: float = 0,
    ) -> tuple[int, str]:
        """Run a command, polling so cancellation stays responsive.

        stderr goes to a temp file rather than a pipe: a chatty FFmpeg run can
        fill a pipe and deadlock while we are polling. With ``progress_cb``,
        FFmpeg's ``-progress`` lines are read on a small thread and turned
        into a percentage, so a long conversion is not a frozen 0%.
        """
        with tempfile.TemporaryFile(
            mode="w+", encoding="utf-8", errors="replace"
        ) as err_file:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=(
                    subprocess.PIPE if progress_cb is not None
                    else subprocess.DEVNULL
                ),
                stderr=err_file,
                creationflags=CREATE_NO_WINDOW,
            )
            reader = None
            if progress_cb is not None and proc.stdout is not None:
                reader = threading.Thread(
                    target=self._read_progress,
                    args=(proc.stdout, duration, progress_cb),
                    daemon=True,
                    name="MusicDLP-FFmpegProgress",
                )
                reader.start()
            deadline = time.monotonic() + timeout
            try:
                while proc.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        raise Cancelled()
                    if time.monotonic() > deadline:
                        proc.kill()
                        raise AppError(
                            "FFmpeg took too long and was stopped.",
                            "The file may be unusually long or the disk is "
                            "slow. Try again.",
                            kind="timeout",
                        )
                    time.sleep(0.1)
                code = proc.returncode
            finally:
                if proc.poll() is None:
                    proc.kill()
                if reader is not None:
                    reader.join(timeout=2)
                err_file.seek(0)
                stderr = err_file.read()

        return code, stderr

    @staticmethod
    def _read_progress(stream, duration: float, callback) -> None:
        """Turn FFmpeg's ``-progress`` key=value lines into a percentage."""
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("out_time=") and duration > 0:
                    value = line.split("=", 1)[1]
                    try:
                        hours, minutes, seconds = value.split(":")
                        position = (
                            int(hours) * 3600
                            + int(minutes) * 60
                            + float(seconds)
                        )
                    except (ValueError, TypeError):
                        continue
                    callback(
                        max(0.0, min(99.0, position / duration * 100.0))
                    )
                elif line == "progress=end":
                    callback(100.0)
        except Exception:
            logger.debug("Could not read FFmpeg progress", exc_info=True)
        finally:
            try:
                stream.close()
            except Exception:
                pass
