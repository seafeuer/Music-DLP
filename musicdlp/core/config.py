"""Typed application settings stored as JSON."""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from musicdlp.core.paths import (
    config_path,
    default_output_dir,
    default_spotify_dir,
)

logger = logging.getLogger("musicdlp.config")

THEMES = ("system", "dark", "light")
MODES = ("easy", "pro")
FORMATS = ("mp3", "m4a")
BITRATES = ("128", "192", "256", "320")
COOKIE_MODES = ("none", "browser", "file")
COPY_MODES = ("copy", "move")
BROWSERS = ("chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium")
# "auto" lets the address and key decide; the others are hints.
AI_PROVIDERS = ("auto", "ollama", "openai", "anthropic")
TRANSLATE_STYLES = ("translated", "transliterated", "both")

# A JSON file can hold any type in any field. A hand-edited or
# half-written config is not hypothetical, so every value is coerced to
# the type the field expects before it is used.
_STRING_FIELDS = (
    "theme", "mode", "spotify_format",
    "spotify_copy_mode", "mp3_bitrate", "m4a_bitrate",
    "filename_template", "cookies_mode", "cookies_browser",
    "cookies_file", "ai_provider", "ai_base_url", "ai_model",
    "ai_api_key", "ai_custom_prompt", "gdrive_dir", "translate_language",
    "translate_style", "spotify_dlp_folder", "log_level", "ffmpeg_path",
    "window_geometry", "bridge_token",
)
_BOOL_FIELDS = (
    "spotify_auto_restart", "normalize", "keep_raw", "notifications",
    "smart_titles", "ai_find_artwork", "translate_artist",
    "bridge_enabled", "first_run_done",
)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _writable_text(value):
    """A string that can always be saved as UTF-8.

    A value pasted from another program can contain an unpaired surrogate,
    which JSON will happily hold but UTF-8 cannot encode. Writing it raised
    UnicodeEncodeError, and because only OSError was caught, a settings
    save died with the window and nothing was written.
    """
    if isinstance(value, str):
        return value.encode("utf-8", "replace").decode("utf-8")
    if isinstance(value, dict):
        return {key: _writable_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_writable_text(item) for item in value]
    return value


@dataclass
class Settings:
    """All persisted user preferences."""

    theme: str = "dark"
    mode: str = "easy"

    output_dir: str = ""
    spotify_dir: str = ""
    spotify_format: str = "mp3"
    spotify_copy_mode: str = "copy"
    spotify_auto_restart: bool = True

    formats: list[str] = field(default_factory=lambda: ["mp3"])
    mp3_bitrate: str = "320"
    m4a_bitrate: str = "320"
    normalize: bool = True
    # Keeping the download is the safe default: converting from a
    # kept file never needs the network again. Deleting them is
    # something the user opts into.
    keep_raw: bool = True
    # Bumped when a stored value needs translating; see load().
    settings_version: int = 2
    filename_template: str = "{title}"

    cookies_mode: str = "none"
    cookies_browser: str = "chrome"
    cookies_file: str = ""

    max_retries: int = 3
    sleep_min: float = 1.0
    sleep_max: float = 3.0

    notifications: bool = True
    smart_titles: bool = True

    # AI is optional and only runs when the user asks for it.
    ai_provider: str = "auto"
    ai_base_url: str = "http://localhost:11434"
    ai_model: str = ""
    ai_api_key: str = ""
    ai_find_artwork: bool = True
    # Extra instructions added to the built-in AI prompt.
    ai_custom_prompt: str = ""
    # Where finished songs are copied for phone access. Empty means
    # "find the synced Google Drive folder automatically".
    gdrive_dir: str = ""

    # Translation is a separate action, with its own remembered choices.
    translate_language: str = "Japanese"
    translate_style: str = "both"
    translate_artist: bool = True

    # The local bridge lets the Music DLP panel inside Spotify read the
    # library and start work. It listens on loopback only and needs the
    # token, so another program cannot drive it by accident.
    bridge_enabled: bool = True
    bridge_port: int = 8317
    bridge_token: str = ""
    # Spotify folder that holds the playlists the panel creates.
    spotify_dlp_folder: str = "Music DLP"

    log_level: str = "INFO"

    ffmpeg_path: str = ""
    window_geometry: str = ""
    last_source_id: int | None = None
    first_run_done: bool = False
    # Named filter combinations for the song list: a status and a search
    # text. A large library otherwise needs the same two controls set by
    # hand every time.
    saved_filters: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.output_dir:
            self.output_dir = str(default_output_dir())
        if not self.spotify_dir:
            self.spotify_dir = str(default_spotify_dir())
        self.validate()

    # ------------------------------------------------------------------ load

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = Path(path) if path else config_path()
        if not path.exists():
            return cls()

        try:
            # utf-8-sig tolerates a byte-order mark. Editors on Windows
            # often add one, and a plain utf-8 read then fails and silently
            # discards every setting.
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            if isinstance(exc, ValueError):
                # A damaged file used to be replaced by defaults on the
                # next save, with no copy of what the user had. Keep it
                # where they (or a later version) can recover it from.
                stamp = time.strftime("%Y%m%d-%H%M%S")
                backup = path.with_name(f"{path.name}.bad-{stamp}")
                try:
                    path.replace(backup)
                    logger.warning(
                        "config.json was not valid JSON; kept a copy at %s",
                        backup,
                    )
                except OSError as move_error:
                    logger.warning(
                        "config.json was not valid JSON and could not be "
                        "set aside: %s", move_error,
                    )
            return cls()

        if not isinstance(raw, dict):
            return cls()

        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in raw.items() if k in known}

        # Before 2.7.1 the default was to delete the download after
        # converting, and that default was written into every config file.
        # Keeping the download is the default now, and an old False was
        # never a deliberate choice — there was nothing else to leave it as.
        try:
            stored_version = int(raw.get("settings_version") or 1)
        except (TypeError, ValueError, OverflowError):
            stored_version = 1
        if stored_version < 2:
            values["keep_raw"] = True
            values["settings_version"] = 2

        try:
            return cls(**values)
        except (TypeError, ValueError):
            return cls()

    # ------------------------------------------------------------------ save

    def save(self, path: Path | None = None) -> None:
        """Write the settings, always as valid UTF-8.

        Any failure is reported as OSError, which every caller already
        handles, so a bad value cannot take the window down with it.
        """
        path = Path(path) if path else config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                _writable_text(asdict(self)), indent=2, ensure_ascii=False
            )
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            raise
        except (TypeError, ValueError) as exc:
            raise OSError(
                f"The settings could not be written: {exc}"
            ) from exc

    # -------------------------------------------------------------- validate

    def _coerce_types(self) -> None:
        """Make every field the type its code expects.

        Anything that cannot be coerced falls back to the dataclass
        default, so a damaged config starts the app with defaults rather
        than crashing before there is a window to explain what happened.
        """
        for name in ("output_dir", "spotify_dir"):
            if not isinstance(getattr(self, name), str):
                setattr(self, name, "")
        for name in _STRING_FIELDS:
            value = getattr(self, name)
            if isinstance(value, str):
                continue
            if isinstance(value, (int, float)):
                setattr(self, name, str(value))
            else:
                setattr(self, name, "")
        for name in _BOOL_FIELDS:
            setattr(self, name, _as_bool(getattr(self, name)))

        if not isinstance(self.formats, (list, tuple)):
            self.formats = []
        else:
            self.formats = list(self.formats)

        # A hand-edited config can contain anything here. Keep only entries
        # with a name, and a status/search that are strings.
        if not isinstance(self.saved_filters, (list, tuple)):
            self.saved_filters = []
        else:
            cleaned = []
            for item in self.saved_filters:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                cleaned.append({
                    "name": name,
                    "status": str(item.get("status") or "all"),
                    "search": str(item.get("search") or ""),
                })
            self.saved_filters = cleaned

        self.settings_version = _as_int(self.settings_version, 2)
        self.max_retries = _as_int(self.max_retries, 3)
        self.bridge_port = _as_int(self.bridge_port, 8317)
        if self.last_source_id is not None:
            self.last_source_id = _as_int(self.last_source_id, None)
        self.sleep_min = _as_float(self.sleep_min, 1.0)
        self.sleep_max = _as_float(self.sleep_max, 3.0)

        # A missing or unusable library folder is recovered, not kept as
        # the relative path "123" or an empty string.
        if not self.output_dir.strip():
            self.output_dir = str(default_output_dir())
        if not self.spotify_dir.strip():
            self.spotify_dir = str(default_spotify_dir())

    def validate(self) -> None:
        """Coerce values into supported ranges instead of failing."""
        self._coerce_types()

        if self.theme not in THEMES:
            self.theme = "dark"
        if self.mode not in MODES:
            self.mode = "easy"

        formats = [f for f in (self.formats or []) if f in FORMATS]
        self.formats = formats or ["mp3"]

        if self.mp3_bitrate not in BITRATES:
            self.mp3_bitrate = "320"
        if self.m4a_bitrate not in BITRATES:
            self.m4a_bitrate = "320"

        if self.spotify_format not in FORMATS:
            self.spotify_format = "mp3"
        if self.spotify_copy_mode not in COPY_MODES:
            self.spotify_copy_mode = "copy"

        if self.cookies_mode not in COOKIE_MODES:
            self.cookies_mode = "none"
        if self.cookies_browser not in BROWSERS:
            self.cookies_browser = "chrome"

        try:
            self.max_retries = max(0, min(10, int(self.max_retries)))
        except (TypeError, ValueError):
            self.max_retries = 3
        try:
            self.sleep_min = max(0.0, float(self.sleep_min))
            self.sleep_max = max(self.sleep_min, float(self.sleep_max))
        except (TypeError, ValueError):
            self.sleep_min, self.sleep_max = 1.0, 3.0

        if not self.filename_template.strip():
            self.filename_template = "{title}"
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.log_level = "INFO"
        if self.ai_provider not in AI_PROVIDERS:
            self.ai_provider = "auto"
        self.ai_base_url = (self.ai_base_url or "").strip()
        self.ai_model = (self.ai_model or "").strip()

        self.translate_language = (self.translate_language or "").strip() or "Japanese"
        if self.translate_style not in TRANSLATE_STYLES:
            self.translate_style = "both"

        try:
            self.bridge_port = max(1024, min(65535, int(self.bridge_port)))
        except (TypeError, ValueError):
            self.bridge_port = 8317
        self.bridge_token = (self.bridge_token or "").strip()
        self.spotify_dlp_folder = (
            (self.spotify_dlp_folder or "").strip() or "Music DLP"
        )

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_model and self.ai_base_url)

    def ensure_bridge_token(self) -> str:
        """The bridge token, created and saved the first time it is needed.

        A token per install means another program on the machine cannot
        drive the bridge just by knowing the port.
        """
        if not self.bridge_token:
            self.bridge_token = secrets.token_urlsafe(32)
            try:
                self.save()
            except OSError:
                pass
        return self.bridge_token

    def reset_keeping_token(self) -> "Settings":
        """A fresh set of defaults that the installed panel still trusts.

        Resetting everything would generate a new bridge token on the next
        start, and the panel inside Spotify would answer 403 until it was
        reinstalled. The token (and the fact that the first-run wizard has
        been seen) survive; every other preference goes back to default.
        """
        fresh = Settings()
        fresh.bridge_token = self.bridge_token
        fresh.first_run_done = True
        return fresh

    # ------------------------------------------------------------- accessors

    @property
    def output_path(self) -> Path:
        return Path(self.output_dir).expanduser()

    @property
    def spotify_path(self) -> Path:
        return Path(self.spotify_dir).expanduser()

    @property
    def raw_dir(self) -> Path:
        return self.output_path / "_raw"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
