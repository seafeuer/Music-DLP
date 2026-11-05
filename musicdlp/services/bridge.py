"""A small local HTTP API so the Music DLP panel inside Spotify can work.

The panel runs in Spotify's renderer, where it can reach Spotify's own
playlist APIs but not the library on disk. This bridge is the other half:
it exposes the library and the existing services over loopback so the panel
can browse, start downloads, and ask for a sync plan.

Only the panel is meant to call it. It listens on 127.0.0.1, requires the
token from the config file, and answers only the Spotify renderer's origin.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject, Signal

from musicdlp import __version__
from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.models import BatchItem, TrackRow, display_status
from musicdlp.core.paths import is_isolated
from musicdlp.services.spotify_sync import (
    LocalTrack,
    build_plan,
    folder_tracks_from_rows,
)

logger = logging.getLogger("musicdlp.bridge")

# Spotify's desktop renderer. The panel's fetch() runs with this origin.
ALLOWED_ORIGINS = ("https://xpui.app.spotify.com",)

MAX_BODY_BYTES = 2 * 1024 * 1024


class BridgeError(Exception):
    """A request the bridge refuses, with the status to answer with."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


@dataclass
class Job:
    """A call that outlives the request that started it."""

    id: str
    kind: str
    status: str = "running"
    result: Any = None
    error: str = ""
    # Monotonic time the job stopped running, used to forget it later.
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }


# How long a finished job's answer stays available. The panel gives up
# waiting after 12 minutes, so an hour is plenty for a slow tab to come back
# and read a result, while a long-running desktop session no longer grows the
# registry without bound.
FINISHED_JOB_TTL = 3600.0


@dataclass
class JobRegistry:
    """Runs slow work on a thread and keeps the answer for the panel."""

    jobs: dict[str, Job] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    ttl_seconds: float = FINISHED_JOB_TTL

    def _prune_locked(self) -> None:
        """Forget finished jobs older than the TTL. Caller holds the lock."""
        cutoff = time.monotonic() - self.ttl_seconds
        stale = [
            job_id for job_id, job in self.jobs.items()
            if job.finished_at is not None and job.finished_at < cutoff
        ]
        for job_id in stale:
            del self.jobs[job_id]

    def start(self, kind: str, work: Callable[[], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._prune_locked()
            self.jobs[job.id] = job

        def run() -> None:
            try:
                job.result = work()
                job.status = "done"
            except Exception as exc:  # noqa: BLE001 - reported to the panel
                logger.exception("Bridge job %s failed", kind)
                job.error = str(exc)
                job.status = "error"
            finally:
                job.finished_at = time.monotonic()

        threading.Thread(
            target=run, name=f"MusicDLP-Bridge-{kind}", daemon=True
        ).start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            self._prune_locked()
            return self.jobs.get(job_id)

    def has_running(self) -> bool:
        """True while any bridge job is still working."""
        with self._lock:
            self._prune_locked()
            return any(job.status == "running" for job in self.jobs.values())


# The panel carries its bridge settings in this one line. The installer
# writes it, and the app rewrites it at startup so the token can never go
# stale - an older build saving config.json drops the token key, and the
# next launch generates a new one.
PANEL_CONFIG_RE = re.compile(r"var MUSICDLP_CONFIG = \{.*?\};")

# The panel source (rewritten for the next `spicetify apply`) and the copy
# Spotify actually loads.
PANEL_TARGETS = (
    ("spicetify", "CustomApps", "musicdlp", "index.js"),
    ("Spotify", "Apps", "xpui", "spicetify-routes-musicdlp.js"),
)


def sync_panel_config(
    port: int, token: str, dlp_folder: str, *, appdata: Optional[Path] = None
) -> list[Path]:
    """Write the live bridge settings into the installed panel.

    Returns the files that were updated. Missing files are skipped: the
    panel simply is not installed, which is not an error.

    An isolated run (``MUSICDLP_HOME``) never does this: its config is a
    copy or a fresh one, so its token is not the token the installed panel
    should carry, and writing it would leave the user's Spotify panel
    unable to reach the real app. A caller that names a folder explicitly
    is writing somewhere it chose, which is how the tests aim it at a
    temporary directory.
    """
    if appdata is None and is_isolated():
        logger.debug("Isolated run: the installed panel is left untouched")
        return []

    root = Path(appdata) if appdata else Path(os.environ.get("APPDATA") or "")
    if not str(root) or not root.exists():
        return []

    payload = json.dumps(
        {"port": int(port), "token": token, "dlpFolder": dlp_folder},
        separators=(",", ":"),
    )
    replacement = f"var MUSICDLP_CONFIG = {payload};"

    updated: list[Path] = []
    for parts in PANEL_TARGETS:
        path = root.joinpath(*parts)
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            new_text, count = PANEL_CONFIG_RE.subn(replacement, text, count=1)
            if not count or new_text == text:
                continue
            path.write_text(new_text, encoding="utf-8")
            updated.append(path)
        except OSError as exc:
            logger.warning("Could not update the panel config in %s: %s", path, exc)

    if updated:
        logger.info("Panel bridge settings refreshed in %d file(s)", len(updated))
    return updated


def _jsonable(value: Any) -> Any:
    """Turn service return values into something json.dumps accepts."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: _jsonable(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    return str(value)


def _track_dict(track) -> dict:
    paths = [Path(path) for path in track.output_paths]
    file_missing = bool(paths) and not any(path.exists() for path in paths)
    return {
        "id": track.id,
        "title": track.display_title,
        "artist": track.display_artist,
        "album": track.display_album,
        "track_number": track.display_track,
        "duration": track.duration,
        "status": track.status,
        # The status a person should see: a converted song whose file was
        # deleted, or one with an unsaved edit, is not simply "Converted".
        "display_status": display_status(track, file_missing),
        "url": track.url,
        "thumbnail_url": track.thumbnail_url,
        "has_file": bool(track.output_paths),
        "has_raw": bool(
            track.raw_path and Path(track.raw_path).exists()
        ),
        "file_missing": file_missing,
        "needs_update": bool(track.needs_update),
        "sync_error": track.sync_error or "",
        "smart_title": track.smart_title or "",
        "smart_artist": track.smart_artist or "",
        "smart_confidence": track.smart_confidence,
        "has_suggestion": track.has_suggestion,
        "cover_source": track.cover_source or "",
    }


def _source_dict(source) -> dict:
    return {
        "id": source.id,
        "title": source.title,
        "kind": source.kind,
        "platform": source.platform,
        "folder": source.folder or "",
        "item_count": source.item_count,
        "thumbnail_url": source.thumbnail_url,
        "is_folder": source.is_folder,
    }


class Bridge(QObject):
    """Owns the HTTP server and the Qt side of starting work.

    The server runs on its own threads, but the pipeline may only be
    started from the GUI thread. ``start_pipeline`` carries that request
    across; the window connects it to its own handler.
    """

    # A job request for the GUI thread. It carries a request id that is
    # answered through ``report_start``, so the panel can be told honestly
    # whether the desktop accepted it.
    start_pipeline = Signal(object)

    def __init__(
        self,
        settings: Settings,
        db: Database,
        library,
        pipeline,
        logger_: Optional[logging.Logger] = None,
        *,
        port: Optional[int] = None,
        thumbnails=None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.db = db
        self.library = library
        self.pipeline = pipeline
        self.thumbnails = thumbnails
        self.log = logger_ or logger
        self.port = int(port if port is not None else settings.bridge_port)
        self.jobs = JobRegistry()
        # Resolved once: the handler runs on request threads, and writing
        # the config from there would race with the GUI.
        self.token = settings.ensure_bridge_token()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._progress: dict = {}
        self._start_waiters: dict[str, dict] = {}
        self._start_lock = threading.Lock()

    # ------------------------------------------------------------- lifecycle

    def start(self) -> bool:
        """Start listening. Returns False when the port is already taken."""
        if self._server is not None:
            return True

        handler = _make_handler(self)
        try:
            server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        except OSError as exc:
            self.log.warning("Music DLP bridge could not start: %s", exc)
            return False

        server.daemon_threads = True
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="MusicDLP-Bridge",
            daemon=True,
        )
        self._thread.start()
        self.log.info("Music DLP bridge listening on 127.0.0.1:%d", self.port)
        # The panel needs this port and token. Writing them here means the
        # two can never drift, whatever happened to config.json since the
        # panel was installed.
        sync_panel_config(self.port, self.token, self.settings.spotify_dlp_folder)
        return True

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        self._thread = None

    @property
    def running(self) -> bool:
        return self._server is not None

    # ---------------------------------------------------------------- helpers

    def record_progress(self, data: dict) -> None:
        """Keep the latest pipeline progress for the panel to poll."""
        self._progress = dict(data or {})

    def _rows_for(self, track_ids: list[int]) -> list[TrackRow]:
        rows = self.db.get_track_rows_by_ids(track_ids)
        found = {row.track.id for row in rows}
        missing = [tid for tid in track_ids if tid not in found]
        if missing:
            raise BridgeError(f"Unknown song ids: {missing}")
        return rows

    # ----------------------------------------------------------- mutations

    def _guard_idle(self) -> None:
        """Refuse a change while the library is already being written.

        The desktop blocks its own edit actions while a job runs; the
        bridge had no such guard, so a metadata save could land between a
        convert stage's read and its final write and then be erased, or
        two writers could touch the same file at once. Refusing with 409
        is honest: nothing was changed and the panel can retry.
        """
        if self.pipeline.is_running:
            raise BridgeError(
                "Music DLP is working on a job. Try again when it finishes.",
                HTTPStatus.CONFLICT,
            )
        if self.jobs.has_running():
            raise BridgeError(
                "Another Music DLP request is still running. "
                "Try again in a moment.",
                HTTPStatus.CONFLICT,
            )

    def report_start(self, request_id: str, started: bool, reason: str = "") -> None:
        """Answer a queued ``start_pipeline`` request from the GUI thread."""
        with self._start_lock:
            waiter = self._start_waiters.get(request_id)
        if waiter is None:
            return
        waiter["started"] = bool(started)
        waiter["reason"] = reason or ""
        waiter["event"].set()

    def _start_pipeline_now(
        self,
        rows: list[TrackRow],
        stages: list[str],
        *,
        force: bool = False,
        retag: bool = False,
        only_synced: bool = False,
    ) -> tuple[bool, str]:
        """Ask the GUI thread to start a job and wait for its answer.

        The pipeline may only be started from the GUI thread. Emitting the
        signal queues the call; the waiter turns "the window refused"
        into a real answer instead of the unconditional success the panel
        used to be told.
        """
        request_id = uuid.uuid4().hex[:12]
        waiter = {"event": threading.Event(), "started": False, "reason": ""}
        with self._start_lock:
            self._start_waiters[request_id] = waiter
        try:
            self.start_pipeline.emit(
                {
                    "rows": rows,
                    "stages": stages,
                    "force": force,
                    "retag": retag,
                    "only_synced": only_synced,
                    "request_id": request_id,
                }
            )
            if not waiter["event"].wait(5.0):
                return False, "Music DLP did not answer. Open its window and try again."
            return waiter["started"], waiter["reason"]
        finally:
            with self._start_lock:
                self._start_waiters.pop(request_id, None)

    # ------------------------------------------------------------- the routes

    def handle(self, method: str, path: str, query: dict, body: dict) -> Any:
        """Dispatch one request. Raises BridgeError for anything refused."""
        parts = [part for part in path.split("/") if part]
        # Everything lives under /api.
        if not parts or parts[0] != "api":
            raise BridgeError("Not found", HTTPStatus.NOT_FOUND)
        route = parts[1:]

        if method == "GET" and route == ["health"]:
            return {
                "ok": True,
                "app": "Music DLP",
                "version": __version__,
                "pipeline_running": bool(self.pipeline.is_running),
                "dlp_folder": self.settings.spotify_dlp_folder,
            }

        if method == "GET" and route == ["status"]:
            return {
                "pipeline_running": bool(self.pipeline.is_running),
                "progress": self._progress,
            }

        if method == "GET" and route == ["folders"]:
            folders = self.db.get_folder_sources()
            return [
                {
                    **_source_dict(folder),
                    "tracks": len(self.db.get_tracks_for_folder(folder.id)),
                }
                for folder in folders
            ]

        if method == "GET" and route == ["sources"]:
            return [_source_dict(source) for source in self.db.get_sources()]

        if method == "GET" and route == ["tracks"]:
            folder_id = query.get("folder")
            if folder_id:
                try:
                    rows = self.db.get_tracks_for_folder(int(folder_id))
                except ValueError as exc:
                    raise BridgeError("folder must be a number") from exc
            else:
                rows = self.db.get_all_track_rows()
            return [_track_dict(row.track) for row in rows]

        if method == "POST" and route == ["links"]:
            return self._add_links(body)

        if method == "POST" and route == ["jobs"]:
            return self._start_job(body)

        if method == "GET" and len(route) == 2 and route[0] == "jobs":
            job = self.jobs.get(route[1])
            if job is None:
                raise BridgeError("Unknown job", HTTPStatus.NOT_FOUND)
            return job.to_dict()

        if method == "POST" and route == ["sync", "plan"]:
            return self._sync_plan(body)

        if method == "POST" and route == ["metadata"]:
            return self._set_metadata(body)

        if method == "POST" and route == ["cover"]:
            return self._cover(body)

        if method == "POST" and route == ["cover", "bulk"]:
            return self._cover_bulk(body)

        if method == "POST" and route == ["lookup"]:
            return self._lookup(body)

        if method == "POST" and route == ["translate"]:
            return self._translate(body)

        if method == "POST" and route == ["ai", "names"]:
            return self._ai_names(body)

        if method == "POST" and route == ["gdrive"]:
            return self._gdrive(body)

        if method == "POST" and route == ["rewrite"]:
            return self._rewrite(body)

        raise BridgeError("Not found", HTTPStatus.NOT_FOUND)

    # ------------------------------------------------------------ route bodies

    def _add_links(self, body: dict) -> dict:
        self._guard_idle()
        urls = [str(url).strip() for url in (body.get("urls") or []) if url]
        if not urls:
            raise BridgeError("No links given")

        def work() -> dict:
            result = self.library.add_links(urls)
            return {
                "sources": list(result.sources),
                "tracks_total": result.tracks_total,
                "tracks_new": result.tracks_new,
                "errors": list(result.errors),
            }

        return self.jobs.start("links", work).to_dict()

    def _start_job(self, body: dict) -> dict:
        self._guard_idle()
        track_ids = [int(value) for value in (body.get("track_ids") or [])]
        stages = [str(stage) for stage in (body.get("stages") or [])]
        if not track_ids:
            raise BridgeError("No songs given")
        if not stages:
            raise BridgeError("No stages given")
        valid = tuple(getattr(self.pipeline, "STAGES", ("download", "convert", "sync")))
        unknown = [stage for stage in stages if stage not in valid]
        if unknown:
            # The panel used to be told "started" for a stage that does not
            # exist, and of the old job for the rest of its polling.
            raise BridgeError(
                "Unknown step(s): " + ", ".join(unknown), HTTPStatus.BAD_REQUEST
            )

        rows = self._rows_for(track_ids)
        force = bool(body.get("force"))
        retag = bool(body.get("retag"))
        started, reason = self._start_pipeline_now(
            rows, stages, force=force, retag=retag
        )
        return {
            "started": started,
            "reason": reason,
            "tracks": len(rows),
            "stages": stages,
            "retag": retag,
        }

    def _sync_plan(self, body: dict) -> dict:
        folder_id = body.get("folder_id")
        if folder_id is None:
            raise BridgeError("folder_id is required")
        try:
            folder_id = int(folder_id)
        except (TypeError, ValueError) as exc:
            raise BridgeError("folder_id must be a number") from exc

        folder = self.db.get_source(folder_id)
        if folder is None or not folder.is_folder:
            # An id the desktop no longer has used to plan the removal of
            # every song in the playlist.
            raise BridgeError("Unknown folder", HTTPStatus.NOT_FOUND)
        rows = self.db.get_tracks_for_folder(folder_id)

        local = [
            LocalTrack.from_panel(item)
            for item in (body.get("local_tracks") or [])
            if isinstance(item, dict)
        ]
        playlist = [str(uri) for uri in (body.get("playlist_uris") or []) if uri]

        plan = build_plan(
            folder_tracks_from_rows(rows),
            local,
            playlist,
            local_files_complete=bool(body.get("local_files_complete")),
        )
        return {
            "to_add": plan.to_add,
            "to_remove": plan.to_remove,
            "unmatched": [
                {"id": track.track_id, "title": track.title, "artist": track.artist}
                for track in plan.unmatched
            ],
            "folder_track_count": plan.folder_track_count,
            "safe_to_remove": plan.safe_to_remove,
            "reason": plan.remove_blocked_reason,
            "duplicate_tags": plan.duplicate_tags,
            "summary": plan.summary(),
        }

    def _set_metadata(self, body: dict) -> dict:
        self._guard_idle()
        track_id = body.get("track_id")
        if track_id is None:
            raise BridgeError("track_id is required")
        track_id = int(track_id)
        if self.db.get_track(track_id) is None:
            raise BridgeError("Unknown song", HTTPStatus.NOT_FOUND)

        # Only the fields the caller owns are written. A review dialog that
        # changes a title must not erase the album and track number that a
        # different dialog saved earlier.
        fields: dict = {}
        if "title" in body:
            fields["title"] = str(body.get("title") or "")
        if "artist" in body:
            fields["artist"] = str(body.get("artist") or "")
        if "album" in body:
            fields["album"] = str(body.get("album") or "")
        if "track_number" in body:
            raw_number = body.get("track_number")
            fields["track_number"] = (
                None if raw_number in (None, "") else int(raw_number)
            )
        if not fields:
            raise BridgeError("No song details were given")

        self.library.set_metadata(track_id, **fields)
        track = self.db.get_track(track_id)
        return {"ok": True, "track": _track_dict(track) if track else None}

    def _cover(self, body: dict) -> dict:
        self._guard_idle()
        track_id = body.get("track_id")
        if track_id is None:
            raise BridgeError("track_id is required")
        track_id = int(track_id)
        if self.db.get_track(track_id) is None:
            raise BridgeError("Unknown song", HTTPStatus.NOT_FOUND)

        action = str(body.get("action") or "lookup")
        if action == "video":
            status = self.library.apply_video_cover(track_id, self.thumbnails)
            return {"ok": True, "action": action, "result": status}
        if action == "lookup":
            status = self.library.refresh_cover(track_id, thumbnails=self.thumbnails)
            return {"ok": True, "action": action, "result": status}
        raise BridgeError(f"Unknown cover action: {action}")

    def _cover_bulk(self, body: dict) -> dict:
        """Look the cover up again for every ticked song.

        Only the picture inside the file is replaced, so no audio is
        decoded or downloaded.
        """
        self._guard_idle()
        track_ids = [int(value) for value in (body.get("track_ids") or [])]
        if not track_ids:
            raise BridgeError("No songs given")
        rows = self._rows_for(track_ids)

        def work() -> dict:
            counts: dict[str, int] = {}
            errors: list[str] = []
            for row in rows:
                try:
                    # The upload's own picture is the fallback when no
                    # catalogue entry fits; without it those songs were
                    # reported "unchanged" instead of getting their still.
                    status = self.library.refresh_cover(
                        row.track.id, thumbnails=self.thumbnails
                    )
                    counts[status] = counts.get(status, 0) + 1
                except Exception as exc:  # noqa: BLE001 - one song must not stop the rest
                    errors.append(f"{row.display_title}: {exc}")
            return {"counts": counts, "errors": errors}

        return self.jobs.start("covers", work).to_dict()

    def _lookup(self, body: dict) -> dict:
        """Ask MusicBrainz for album and track number, without saving."""
        from musicdlp.services import metadata_lookup

        track_ids = [int(value) for value in (body.get("track_ids") or [])]
        if not track_ids:
            raise BridgeError("No songs given")
        rows = self._rows_for(track_ids)

        def work() -> dict:
            results = []
            for row in rows:
                track = row.track
                try:
                    found = metadata_lookup.lookup(
                        track.display_artist, track.display_title
                    )
                except Exception as exc:  # noqa: BLE001
                    results.append({"track_id": track.id, "error": str(exc)})
                    continue
                if found is None:
                    results.append(
                        {
                            "track_id": track.id,
                            "artist": "",
                            "album": "",
                            "track_number": None,
                            "year": "",
                            "error": "No match found",
                        }
                    )
                    continue
                results.append(
                    {
                        "track_id": track.id,
                        "artist": found.artist,
                        "album": found.album,
                        "track_number": found.track,
                        "year": found.year,
                        "error": "",
                    }
                )
            return {"results": results}

        return self.jobs.start("lookup", work).to_dict()

    def _translate(self, body: dict) -> dict:
        from musicdlp.services import translate as translate_service
        from musicdlp.services.ai import AISettings

        track_ids = [int(value) for value in (body.get("track_ids") or [])]
        if not track_ids:
            raise BridgeError("No songs given")
        language = str(body.get("language") or self.settings.translate_language)
        style = str(body.get("style") or self.settings.translate_style)
        # The panel may leave the artist choice out; the desktop setting is
        # the one the user made, so it wins over a hard-coded default.
        if "translate_artist" in body:
            translate_artist = bool(body.get("translate_artist"))
        else:
            translate_artist = bool(self.settings.translate_artist)

        rows = self._rows_for(track_ids)
        ai_settings = AISettings(
            provider=self.settings.ai_provider,
            base_url=self.settings.ai_base_url,
            model=self.settings.ai_model,
            api_key=self.settings.ai_api_key,
            custom_prompt=self.settings.ai_custom_prompt,
        )
        items = [
            (row.track.id, row.track.title or "", row.track.display_artist)
            for row in rows
        ]
        current = {row.track.id: row.track.display_title for row in rows}

        def work() -> dict:
            results, error = translate_service.translate_many(
                ai_settings,
                items,
                language=language,
                translate_artist=translate_artist,
                current_titles=current,
            )
            out = []
            for track_id, translation in results:
                out.append(
                    {
                        "track_id": track_id,
                        "title": translation.title,
                        "title_latin": translation.title_latin,
                        "artist": translation.artist,
                        "artist_latin": translation.artist_latin,
                        "confidence": translation.confidence,
                        "already_target": translation.already_target,
                        "reason": translation.reason,
                        "combined_title": translate_service.combine(
                            translation.title, translation.title_latin, style
                        ),
                        "combined_artist": translate_service.combine(
                            translation.artist, translation.artist_latin, style
                        ),
                    }
                )
            return {
                "translations": out,
                "error": str(error) if error else "",
            }

        return self.jobs.start("translate", work).to_dict()

    def _ai_names(self, body: dict) -> dict:
        from musicdlp.services import ai as ai_service

        track_ids = [int(value) for value in (body.get("track_ids") or [])]
        if not track_ids:
            raise BridgeError("No songs given")
        rows = self._rows_for(track_ids)
        settings = ai_service.AISettings(
            provider=self.settings.ai_provider,
            base_url=self.settings.ai_base_url,
            model=self.settings.ai_model,
            api_key=self.settings.ai_api_key,
            custom_prompt=self.settings.ai_custom_prompt,
        )

        def work() -> dict:
            results = []
            for row in rows:
                track = row.track
                try:
                    suggestion = ai_service.suggest_names(
                        settings,
                        title=track.title or "",
                        channel=track.uploader or "",
                        description=track.description or "",
                    )
                    results.append(
                        {
                            "track_id": track.id,
                            "title": suggestion.title,
                            "artist": suggestion.artist,
                            "album": suggestion.album,
                            "confidence": suggestion.confidence,
                            "error": "",
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - one song must not stop the rest
                    results.append(
                        {
                            "track_id": track.id,
                            "title": "",
                            "artist": "",
                            "album": "",
                            "confidence": 0.0,
                            "error": str(exc),
                        }
                    )
            return {"suggestions": results}

        return self.jobs.start("ai-names", work).to_dict()

    def _gdrive(self, body: dict) -> dict:
        from musicdlp.services import gdrive

        self._guard_idle()
        track_ids = [int(value) for value in (body.get("track_ids") or [])]
        if not track_ids:
            raise BridgeError("No songs given")
        rows = self._rows_for(track_ids)
        paths = []
        for row in rows:
            for path in row.track.output_paths:
                paths.append(path)

        def work() -> dict:
            if not paths:
                return {"sent": 0, "errors": ["No converted files to send"]}
            sent, errors = gdrive.send(paths, self.settings.gdrive_dir)
            target = gdrive.destination(self.settings.gdrive_dir)
            return {
                "sent": sent,
                "errors": list(errors),
                "destination": str(target) if target else "",
            }

        return self.jobs.start("gdrive", work).to_dict()

    def _rewrite(self, body: dict) -> dict:
        """Rewrite the tags and names of the songs the panel selected.

        This is the desktop's "Update files" action, so it goes through the
        pipeline rather than retagging in place: the file is renamed, its
        tags are rewritten, the Spotify copy is replaced under the same
        rules as any other job, and "needs update" is cleared only once
        that work is done.
        """
        self._guard_idle()
        track_ids = [int(value) for value in (body.get("track_ids") or [])]
        if not track_ids:
            raise BridgeError("No songs given")
        rows = self._rows_for(track_ids)

        pending = [row for row in rows if row.track.needs_update]
        if not pending:
            return {
                "started": False,
                "reason": (
                    "Nothing to rewrite — the ticked songs' files already "
                    "match their details."
                ),
                "tracks": 0,
            }

        stages = ["convert"]
        needs_download = [
            row for row in pending
            if not any(Path(path).exists() for path in row.track.output_paths)
            and not (
                row.track.raw_path and Path(row.track.raw_path).exists()
            )
        ]
        if needs_download:
            stages = ["download", "convert"]
        if any(row.track.synced_at for row in pending):
            stages.append("sync")

        started, reason = self._start_pipeline_now(
            pending,
            stages,
            force=True,
            retag=True,
            only_synced=True,
        )
        return {"started": started, "reason": reason, "tracks": len(pending)}


def _make_handler(bridge: Bridge):
    """Build a request handler bound to one bridge instance."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "MusicDLPBridge"
        protocol_version = "HTTP/1.1"

        # -- plumbing ---------------------------------------------------

        def log_message(self, fmt: str, *args: Any) -> None:
            bridge.log.debug("bridge: " + fmt, *args)

        def _origin(self) -> str:
            return self.headers.get("Origin", "")

        def _cors(self) -> None:
            origin = self._origin()
            if origin in ALLOWED_ORIGINS:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header(
                    "Access-Control-Allow-Headers", "Authorization, Content-Type"
                )
                self.send_header(
                    "Access-Control-Allow-Methods", "GET, POST, OPTIONS"
                )

        def _send(self, status: HTTPStatus, payload: Any) -> None:
            body = json.dumps(_jsonable(payload)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            origin = self._origin()
            # A browser always sends Origin. When it is present it must be
            # the Spotify renderer; anything else is another site.
            if origin and origin not in ALLOWED_ORIGINS:
                return False
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return False
            return header[7:].strip() == bridge.token

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > MAX_BODY_BYTES:
                raise BridgeError("Request too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise BridgeError("Body is not valid JSON") from exc
            if not isinstance(data, dict):
                raise BridgeError("Body must be a JSON object")
            return data

        # -- methods ----------------------------------------------------

        def do_OPTIONS(self) -> None:  # noqa: N802 - http.server naming
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            try:
                if not self._authorised():
                    raise BridgeError("Unauthorised", HTTPStatus.FORBIDDEN)
                parsed = urlparse(self.path)
                query = {
                    key: values[0] for key, values in parse_qs(parsed.query).items()
                }
                body = self._read_body() if method == "POST" else {}
                result = bridge.handle(method, parsed.path, query, body)
                self._send(HTTPStatus.OK, result)
            except BridgeError as exc:
                self._send(exc.status, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - never kill the server
                bridge.log.exception("Bridge request failed")
                self._send(
                    HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)}
                )

    return Handler
