"""Application bootstrap: Qt app, single instance, first run, main window."""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
from typing import Optional

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QStyle

from musicdlp import APP_NAME, __version__
from musicdlp.core import backup
from musicdlp.core.config import Settings
from musicdlp.core.db import (
    SCHEMA_VERSION, Database, quarantine_database, schema_version_on_disk,
)
from musicdlp.core.logging import setup_logging
from musicdlp.core.maintenance import repair_library
from musicdlp.core.paths import db_path, ensure_app_dirs, log_dir
from musicdlp.services.legacy_import import find_legacy_db, import_legacy
from musicdlp.services.bridge import Bridge
from musicdlp.ui.dialogs.setup import SetupDialog
from musicdlp.ui.main_window import MainWindow, build_context
from musicdlp.ui.theme import apply_theme


def _instance_key() -> str:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return f"MusicDLP-{user}"


def _try_existing_instance() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(_instance_key())
    if socket.waitForConnected(250):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(250)
        socket.disconnectFromServer()
        return True
    return False


def _listen_for_show(window: MainWindow) -> Optional[QLocalServer]:
    key = _instance_key()
    QLocalServer.removeServer(key)
    server = QLocalServer()
    if not server.listen(key):
        return None

    def handle() -> None:
        connection = server.nextPendingConnection()
        if connection is not None:
            connection.waitForReadyRead(100)
            connection.readAll()
            connection.disconnectFromServer()
        window.showNormal()
        window.raise_()
        window.activateWindow()

    server.newConnection.connect(handle)
    return server


def _install_excepthook(logger: logging.Logger) -> None:
    def hook(exc_type, exc, tb) -> None:
        logger.critical(
            "Unhandled exception", exc_info=(exc_type, exc, tb)
        )
        try:
            QMessageBox.critical(
                None,
                "Music DLP error",
                f"{exc_type.__name__}: {exc}\n\n"
                "The details were written to the log file.",
            )
        except Exception:
            pass

    sys.excepthook = hook

    # A worker thread that dies would otherwise vanish without a trace,
    # which is exactly how a crash looks in a windowed build with no
    # console. Record it so the log explains what happened.
    def thread_hook(args) -> None:
        logger.critical(
            "Unhandled exception in thread %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = thread_hook


def _open_database(logger: logging.Logger) -> Database:
    """Open the library, moving a damaged file aside instead of dying.

    A corrupt library.db used to stop every launch: the exception was
    shown once and the same file failed again next time, with no hint
    about which file to remove. The damaged file is kept next to the new
    one so a support question can still look at it.
    """
    path = db_path()
    try:
        return Database(path)
    except sqlite3.DatabaseError as exc:
        logger.exception("Library database could not be opened: %s", path)
        backup = quarantine_database(path)
        if backup is None:
            QMessageBox.critical(
                None,
                "Library cannot be opened",
                f"Music DLP could not open its library:\n{path}\n\n{exc}\n\n"
                "Close Music DLP and move or delete that file, then start "
                "again.",
            )
            raise
        QMessageBox.warning(
            None,
            "Library was damaged",
            f"The library file could not be read:\n{path}\n\n"
            f"It was moved to:\n{backup}\n\n"
            "Music DLP will start with an empty library. The damaged file "
            "has been kept.",
        )
        return Database(path)


def main() -> int:
    ensure_app_dirs()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MusicDLP")
    app.setWindowIcon(app.style().standardIcon(QStyle.SP_MediaVolume))

    if _try_existing_instance():
        return 0

    # Logging and the crash dialog come first: a damaged config file must
    # not take the app down before there is anywhere to report it.
    logger = setup_logging(log_dir(), "INFO")
    _install_excepthook(logger)

    # A restore the user asked for last time is applied before the
    # database or the settings are opened, so nothing is holding the files
    # being replaced. It also restores the settings snapshot, which is why
    # this comes before Settings.load().
    try:
        restored = backup.apply_pending_restore()
        if restored:
            logger.info(restored)
    except Exception as exc:  # noqa: BLE001 - never stop the app
        logger.warning("Could not apply a restore: %s", exc)

    try:
        settings = Settings.load()
    except Exception:
        logger.exception("Could not read the settings; using defaults")
        settings = Settings()
    logger.setLevel(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    logger.info("Starting Music DLP %s", __version__)
    apply_theme(app, settings.theme)

    # An upgrade rewrites the library file before anyone sees it. Take a
    # copy of the old one first, so a bad migration has something to
    # fall back to.
    try:
        on_disk = schema_version_on_disk(db_path())
        if on_disk is not None and on_disk < SCHEMA_VERSION:
            backup.create_backup("before-migration")
    except Exception as exc:  # noqa: BLE001 - a backup must not block start
        logger.warning("Could not back up before the upgrade: %s", exc)

    db = _open_database(logger)
    # One automatic snapshot a day is the safety net for a mistaken delete
    # when the user never used "Back up now".
    try:
        backup.maybe_daily_backup()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not take the daily backup: %s", exc)
    ctx = build_context(settings, db, logger)

    # Recover files that were left in an old or temporary folder.
    try:
        notes = repair_library(db, settings)
        for note in notes:
            logger.info(note)
        ctx.rebuild(settings)
    except Exception as exc:
        logger.warning("Library repair skipped: %s", exc)

    if not settings.first_run_done:
        wizard = SetupDialog(settings, ctx.ffmpeg, ctx.spotify)
        wizard.exec()
        settings.first_run_done = True
        settings.validate()
        try:
            settings.save()
        except OSError:
            pass
        ctx.rebuild(settings)
        apply_theme(app, settings.theme)

    if not db.get_sources():
        legacy = find_legacy_db()
        if legacy is not None:
            reply = QMessageBox.question(
                None,
                "Import old library?",
                f"An old Music DLP library was found at:\n{legacy}\n\n"
                "Import its sources and tracks? Existing files are referenced "
                "in place and nothing is moved.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                try:
                    summary = import_legacy(db, legacy)
                    logger.info(
                        "Imported %d tracks from %s",
                        summary.tracks, legacy,
                    )
                except Exception as exc:
                    logger.error("Legacy import failed: %s", exc)

    window = MainWindow(ctx)
    server = _listen_for_show(window)
    window.show()
    # A batch that was interrupted by a crash, a power cut or a forced
    # shutdown is offered again instead of disappearing silently.
    try:
        window.resume_pending_job()
    except Exception as exc:
        logger.warning("Could not resume the last job: %s", exc)

    # The panel inside Spotify talks to the library through this. It is
    # optional: a port already in use only means the panel stays offline.
    bridge = None
    if settings.bridge_enabled:
        try:
            bridge = Bridge(
                ctx.settings,
                ctx.db,
                ctx.library,
                ctx.pipeline,
                logger,
                thumbnails=ctx.thumbnails,
            )
            bridge.start_pipeline.connect(window.start_pipeline_for_bridge)
            bridge.sync_finished.connect(window._on_bridge_sync_finished)
            # The window needs the bridge back to answer the request.
            window.bridge = bridge
            ctx.pipeline.progress.connect(bridge.record_progress)
            if not bridge.start():
                bridge = None
        except Exception as exc:
            logger.warning("Music DLP bridge unavailable: %s", exc)
            bridge = None

    try:
        code = app.exec()
    finally:
        if bridge is not None:
            try:
                bridge.stop()
            except Exception:
                pass
        try:
            db.close()
        except Exception:
            pass
        if server is not None:
            server.close()

    logger.info("Music DLP closed")
    return code
