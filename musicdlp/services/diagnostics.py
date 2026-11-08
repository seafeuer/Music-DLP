"""One report that explains why a song did not arrive.

The pieces were already available across Settings, the log and several
dialogs. This module gathers the ones that answer "why is this not
working?" in one place, with a status a person can scan. It performs no
network requests: the AI connection is tested only when the dialog's
button is pressed.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from musicdlp import __version__
from musicdlp.core.config import Settings
from musicdlp.core.paths import (
    config_path, db_path, default_output_dir, is_writable, log_dir,
)
from musicdlp.services.bridge import panel_installed

OK = "ok"
WARN = "warn"
FAIL = "fail"
UNKNOWN = "unknown"

_ICONS = {OK: "✓", WARN: "!", FAIL: "✗", UNKNOWN: "?"}


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""

    @property
    def icon(self) -> str:
        return _ICONS.get(self.status, "?")


def _version_of(service) -> str:
    try:
        value = service.version()
    except Exception as exc:  # noqa: BLE001 - a check must never crash
        return f"could not be read ({exc})"
    return str(value or "unknown")


def run_checks(
    settings: Settings,
    ffmpeg,
    ytdlp,
    spotify,
    bridge=None,
    *,
    appdata: Optional[Path] = None,
) -> list[Check]:
    """Collect the state of everything a download needs.

    ``bridge`` is the live bridge object, or None when it is switched off.
    ``appdata`` aims the panel check at a test folder; a real run passes
    nothing and the usual Spotify data folder is read.
    """
    checks: list[Check] = []

    ffmpeg_ok = bool(getattr(ffmpeg, "available", False))
    checks.append(
        Check(
            "FFmpeg",
            OK if ffmpeg_ok else FAIL,
            (
                f"{_version_of(ffmpeg)} — conversion and tagging work."
                if ffmpeg_ok else
                "Not found. Install it with “winget install Gyan.FFmpeg”, "
                "or set its folder in Settings → Advanced."
            ),
        )
    )

    checks.append(
        Check("yt-dlp", OK, _version_of(ytdlp))
    )

    output = settings.output_path
    output_writable = is_writable(output)
    checks.append(
        Check(
            "Songs folder",
            OK if output_writable else FAIL,
            f"{output}"
            + ("" if output_writable else " — cannot be written to."),
        )
    )
    spotify_dir = settings.spotify_path
    spotify_writable = is_writable(spotify_dir)
    checks.append(
        Check(
            "Spotify folder",
            OK if spotify_writable else FAIL,
            f"{spotify_dir}"
            + ("" if spotify_writable else " — cannot be written to."),
        )
    )

    exe = None
    try:
        exe = spotify.find_exe()
    except Exception:  # noqa: BLE001
        exe = None
    checks.append(
        Check(
            "Spotify desktop",
            OK if exe else WARN,
            str(exe) if exe else
            "Not found. Spotify is only needed for Local Files delivery.",
        )
    )

    registered = None
    try:
        registered = spotify.source_registered(spotify_dir)
    except Exception:  # noqa: BLE001
        registered = None
    if registered is True:
        checks.append(
            Check("Local Files folder", OK, "Spotify has this folder as a source.")
        )
    elif registered is False:
        checks.append(
            Check(
                "Local Files folder", WARN,
                "Spotify does not list this folder yet. Settings → "
                "Show local files → Add a source.",
            )
        )
    else:
        checks.append(
            Check(
                "Local Files folder", UNKNOWN,
                "Spotify's source list could not be read. Check Settings → "
                "Show local files if songs do not appear.",
            )
        )

    if settings.cookies_mode == "none":
        cookie_check = Check(
            "Cookies", OK,
            "Not used. That is fine unless YouTube asks you to sign in.",
        )
    elif settings.cookies_mode == "browser":
        cookie_check = Check(
            "Cookies", OK, f"Read from {settings.cookies_browser.title()}."
        )
    else:
        cookie_file = Path(settings.cookies_file).expanduser() if settings.cookies_file else None
        cookie_check = Check(
            "Cookies",
            OK if cookie_file is not None and cookie_file.is_file() else WARN,
            (
                str(cookie_file) if cookie_file is not None
                else "No cookies.txt file was chosen."
            ),
        )
    checks.append(cookie_check)

    if bridge is not None and getattr(bridge, "running", False):
        checks.append(
            Check(
                "Spotify panel bridge", OK,
                f"Listening on 127.0.0.1:{bridge.port}.",
            )
        )
    elif settings.bridge_enabled:
        checks.append(
            Check(
                "Spotify panel bridge", FAIL,
                "Switched on but not listening. Another program may be "
                "using the port; close it and restart Music DLP.",
            )
        )
    else:
        checks.append(
            Check("Spotify panel bridge", WARN, "Switched off in Settings.")
        )

    installed = panel_installed(appdata)
    checks.append(
        Check(
            "Panel installed",
            OK if installed else WARN,
            (
                "The Music DLP panel is in Spicetify."
                if installed else
                "Not installed. Run spicetify\\install-panel.ps1."
            ),
        )
    )

    if settings.ai_configured:
        checks.append(
            Check(
                "AI", OK,
                f"{settings.ai_model} at {settings.ai_base_url} "
                "(connection is tested only when asked).",
            )
        )
    else:
        checks.append(
            Check(
                "AI", WARN,
                "No model chosen. AI naming and translation are optional.",
            )
        )

    try:
        free = shutil.disk_usage(output).free // (1024 ** 3)
        disk_status = OK if free >= 1 else WARN
        disk_detail = f"{free} GB free for downloads."
    except OSError:
        disk_status = UNKNOWN
        disk_detail = "The free space could not be read."
    checks.append(Check("Disk space", disk_status, disk_detail))

    checks.append(Check("Version", OK, __version__))
    checks.append(Check("Settings", OK, str(config_path())))
    checks.append(Check("Library", OK, str(db_path())))
    checks.append(Check("Logs", OK, str(log_dir())))
    if not str(default_output_dir()) == str(output):
        checks.append(
            Check(
                "Default songs folder", UNKNOWN,
                f"Music DLP's default is {default_output_dir()}; this "
                f"library uses {output}.",
            )
        )
    return checks


def report(checks: list[Check]) -> str:
    """The text the Copy report button puts on the clipboard."""
    lines = [f"Music DLP {__version__} diagnostics", ""]
    for check in checks:
        detail = f" — {check.detail}" if check.detail else ""
        lines.append(f"[{check.icon}] {check.name}{detail}")
    return "\n".join(lines)