"""Application bootstrap: Qt app, single instance, first run, main window."""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QStyle

from musicdlp import APP_NAME, __version__
from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.logging import setup_logging
from musicdlp.core.paths import db_path, ensure_app_dirs, log_dir
from musicdlp.services.legacy_import import find_legacy_db, import_legacy
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


def main() -> int:
    ensure_app_dirs()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MusicDLP")
    app.setWindowIcon(app.style().standardIcon(QStyle.SP_MediaVolume))

    if _try_existing_instance():
        return 0

    settings = Settings.load()
    logger = setup_logging(log_dir(), settings.log_level)
    logger.info("Starting Music DLP %s", __version__)
    _install_excepthook(logger)
    apply_theme(app, settings.theme)

    db = Database(db_path())
    ctx = build_context(settings, db, logger)

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
                f"An old Music-DLP library was found at:\n{legacy}\n\n"
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

    try:
        code = app.exec()
    finally:
        try:
            db.close()
        except Exception:
            pass
        if server is not None:
            server.close()

    logger.info("Music DLP closed")
    return code
