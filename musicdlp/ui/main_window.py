"""Main window and application wiring."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSplitter, QStyle, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget,
)

from musicdlp import __version__
from musicdlp.core.config import Settings
from musicdlp.core.db import Database
from musicdlp.core.errors import AppError, Cancelled
from musicdlp.core.logging import LogEmitter, QtLogHandler
from musicdlp.core.models import BatchItem, TrackRow
from musicdlp.core.paths import art_cache_dir, is_writable
from musicdlp.services.ffmpeg import FFmpegService
from musicdlp.services.legacy_import import (
    ImportSummary, find_legacy_db, import_legacy,
)
from musicdlp.services.library import AddResult, LibraryService
from musicdlp.services.pipeline import Pipeline
from musicdlp.services.spotify import SpotifyService
from musicdlp.services.thumbnails import ThumbnailService
from musicdlp.services.urls import split_links
from musicdlp.services.ytdlp import YtDlpService
from musicdlp.ui.dialogs.bulk_add import BulkAddDialog
from musicdlp.ui.dialogs.settings import SettingsDialog
from musicdlp.ui.dialogs.setup import SetupDialog
from musicdlp.ui.dialogs.spotify_setup import SpotifyDialog
from musicdlp.ui.dialogs.track_details import TrackDetailsDialog
from musicdlp.ui.theme import apply_theme
from musicdlp.ui.widgets.inspector import Inspector
from musicdlp.ui.widgets.log_drawer import LogDrawer
from musicdlp.ui.widgets.setup_banner import SetupBanner
from musicdlp.ui.widgets.source_list import SourceListWidget
from musicdlp.ui.widgets.status_bar import StatusBarWidget
from musicdlp.ui.widgets.step_indicator import (
    ACTIVE, DONE, ERROR as STEP_ERROR, WAITING, StepIndicator,
)
from musicdlp.ui.widgets.track_table import TrackTableWidget


@dataclass
class AppContext:
    settings: Settings
    db: Database
    logger: logging.Logger
    ytdlp: YtDlpService
    ffmpeg: FFmpegService
    thumbnails: ThumbnailService
    spotify: SpotifyService
    library: LibraryService
    pipeline: Pipeline

    def rebuild(self, settings: Settings) -> None:
        self.settings = settings
        self.ytdlp = YtDlpService(settings)
        self.ffmpeg = FFmpegService(settings.ffmpeg_path)
        self.thumbnails = ThumbnailService(art_cache_dir())
        self.spotify = SpotifyService()
        self.library = LibraryService(self.db, settings, self.ytdlp)
        self.pipeline.settings = settings
        self.pipeline.ytdlp = self.ytdlp
        self.pipeline.ffmpeg = self.ffmpeg
        self.pipeline.thumbnails = self.thumbnails
        self.pipeline.spotify = self.spotify


def build_context(settings: Settings, db: Database, logger: logging.Logger) -> AppContext:
    ytdlp = YtDlpService(settings)
    ffmpeg = FFmpegService(settings.ffmpeg_path)
    thumbnails = ThumbnailService(art_cache_dir())
    spotify = SpotifyService()
    library = LibraryService(db, settings, ytdlp)
    pipeline = Pipeline(db, settings, ytdlp, ffmpeg, thumbnails, spotify)
    return AppContext(
        settings=settings, db=db, logger=logger, ytdlp=ytdlp, ffmpeg=ffmpeg,
        thumbnails=thumbnails, spotify=spotify, library=library,
        pipeline=pipeline,
    )


class LibrarySignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)


class AddLinksTask(QRunnable):
    def __init__(
        self,
        library: LibraryService,
        urls: list[str],
        cancel_event: threading.Event,
        signals: LibrarySignals,
    ) -> None:
        super().__init__()
        self.library = library
        self.urls = urls
        self.cancel_event = cancel_event
        self.signals = signals

    def run(self) -> None:
        try:
            result = self.library.add_links(
                self.urls,
                cancel_event=self.cancel_event,
                progress=lambda current, total, url: self.signals.progress.emit(
                    current, total, url
                ),
            )
            self.signals.finished.emit(result)
        except Cancelled:
            self.signals.finished.emit(AddResult(errors=["Cancelled."]))
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class RefreshSourceTask(QRunnable):
    def __init__(
        self,
        library: LibraryService,
        source_id: int,
        cancel_event: threading.Event,
        signals: LibrarySignals,
    ) -> None:
        super().__init__()
        self.library = library
        self.source_id = source_id
        self.cancel_event = cancel_event
        self.signals = signals

    def run(self) -> None:
        try:
            result = self.library.refresh_source(
                self.source_id, cancel_event=self.cancel_event
            )
            self.signals.finished.emit(result)
        except Cancelled:
            self.signals.finished.emit(AddResult(errors=["Cancelled."]))
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.settings = ctx.settings
        self.db = ctx.db
        self.logger = ctx.logger

        self.setWindowTitle(f"Music DLP {__version__}")
        self.setMinimumSize(1040, 640)
        self.resize(1280, 820)
        self._restore_geometry()

        self._checked_by_source: dict[int, set[int]] = {}
        self._current_source_id: Optional[int] = None
        self._add_running = False
        self._add_cancel = threading.Event()
        self._active_signals: Optional[LibrarySignals] = None
        self._tray: Optional[QSystemTrayIcon] = None

        self._build_ui()
        self._build_menus()
        self._connect_signals()
        self._install_log_handler()

        self.setAcceptDrops(True)
        self._apply_mode()
        self._refresh_sources(self.settings.last_source_id)
        self._update_spotify_state()
        self._update_actions()
        self._refresh_setup_banner()

        if not ctx.ffmpeg.available:
            self.logger.warning(
                "FFmpeg not found; conversion is disabled until it is located."
            )
            self.status.set_idle(
                "FFmpeg not found — downloads work, conversion is disabled."
            )

        self.logger.info(
            "Main window ready (%d source(s))", len(self.db.get_sources())
        )

    # ------------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        layout.addWidget(self._build_toolbar())

        self.setup_banner = SetupBanner()
        self.setup_banner.action_clicked.connect(self._open_setup)
        layout.addWidget(self.setup_banner)

        self.steps = StepIndicator()
        self.steps.step_clicked.connect(self._on_step_clicked)
        layout.addWidget(self.steps)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        self.sidebar = SourceListWidget()
        self.track_table = TrackTableWidget()
        self.inspector = Inspector(self.ctx.thumbnails)
        self.inspector.setMinimumWidth(300)
        inspector_scroll = QScrollArea()
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setFrameShape(QScrollArea.NoFrame)
        inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inspector_scroll.setWidget(self.inspector)
        inspector_scroll.setMinimumWidth(320)
        self.inspector_scroll = inspector_scroll

        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.track_table)
        self.splitter.addWidget(inspector_scroll)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setSizes([260, 760, 340])
        layout.addWidget(self.splitter, 1)

        self.log_drawer = LogDrawer()
        self.log_drawer.hide()
        layout.addWidget(self.log_drawer)

        self.status = StatusBarWidget()
        layout.addWidget(self.status)

        self.setCentralWidget(central)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        brand = QLabel("Music DLP")
        brand.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(brand)

        self.link_edit = QLineEdit()
        self.link_edit.setPlaceholderText(
            "Paste a YouTube playlist, video, X post, or other link…"
        )
        self.link_edit.returnPressed.connect(self._add_from_input)
        layout.addWidget(self.link_edit, 1)

        self.add_button = QPushButton("Add")
        self.add_button.setObjectName("primary")
        self.add_button.clicked.connect(self._add_from_input)
        layout.addWidget(self.add_button)

        self.paste_button = QPushButton("Paste")
        self.paste_button.clicked.connect(self._paste_links)
        layout.addWidget(self.paste_button)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Easy", "easy")
        self.mode_combo.addItem("Pro", "pro")
        index = self.mode_combo.findData(self.settings.mode)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(self.mode_combo)

        self.action_area = QWidget()
        self.action_layout = QHBoxLayout(self.action_area)
        self.action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_layout.setSpacing(6)
        layout.addWidget(self.action_area)

        self.more_button = QToolButton()
        self.more_button.setText("More")
        self.more_button.setPopupMode(QToolButton.InstantPopup)
        layout.addWidget(self.more_button)

        return bar

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.add_action = QAction("Add link…", self)
        self.add_action.setShortcut("Ctrl+N")
        self.add_action.triggered.connect(lambda: self.link_edit.setFocus())
        file_menu.addAction(self.add_action)

        bulk_action = QAction("Add multiple links…", self)
        bulk_action.setShortcut("Ctrl+Shift+V")
        bulk_action.triggered.connect(self._bulk_add)
        file_menu.addAction(bulk_action)

        refresh_action = QAction("Refresh current source", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self._refresh_current)
        file_menu.addAction(refresh_action)

        import_action = QAction("Import old library…", self)
        import_action.triggered.connect(self._import_legacy)
        file_menu.addAction(import_action)

        file_menu.addSeparator()
        open_output_action = QAction("Open output folder", self)
        open_output_action.triggered.connect(self._open_output)
        file_menu.addAction(open_output_action)

        self.settings_action = QAction("Settings…", self)
        self.settings_action.setShortcut("Ctrl+,")
        self.settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(self.settings_action)

        file_menu.addSeparator()
        quit_action = QAction("Exit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        select_all = QAction("Select all tracks", self)
        select_all.setShortcut("Ctrl+A")
        select_all.triggered.connect(self.track_table.model.check_all)
        edit_menu.addAction(select_all)

        select_new = QAction("Select not-downloaded tracks", self)
        select_new.triggered.connect(
            lambda: self.track_table.model.check_status("new")
        )
        edit_menu.addAction(select_new)

        select_failed = QAction("Select failed tracks", self)
        select_failed.triggered.connect(
            lambda: self.track_table.model.check_status("error")
        )
        edit_menu.addAction(select_failed)

        clear_selection = QAction("Clear selection", self)
        clear_selection.triggered.connect(self.track_table.model.clear_checks)
        edit_menu.addAction(clear_selection)

        view_menu = self.menuBar().addMenu("&View")
        self.logs_action = QAction("Show activity log", self)
        self.logs_action.setShortcut("Ctrl+L")
        self.logs_action.setCheckable(True)
        self.logs_action.toggled.connect(self._toggle_logs)
        view_menu.addAction(self.logs_action)

        self.inspector_action = QAction("Show details panel", self)
        self.inspector_action.setCheckable(True)
        self.inspector_action.setChecked(self.settings.mode == "pro")
        self.inspector_action.toggled.connect(
            lambda visible: self.inspector_scroll.setVisible(visible)
        )
        view_menu.addAction(self.inspector_action)

        help_menu = self.menuBar().addMenu("&Help")
        setup_action = QAction("Set up Music DLP…", self)
        setup_action.triggered.connect(self._open_setup)
        help_menu.addAction(setup_action)

        spotify_action = QAction("Spotify folder…", self)
        spotify_action.triggered.connect(self._open_spotify)
        help_menu.addAction(spotify_action)

        about_action = QAction("About Music DLP", self)
        about_action.triggered.connect(self._about)
        help_menu.addAction(about_action)

        more_menu = self.more_button.menu()
        if more_menu is None:
            from PySide6.QtWidgets import QMenu
            more_menu = QMenu(self.more_button)
            self.more_button.setMenu(more_menu)
        more_menu.addAction(setup_action)
        more_menu.addAction(self.settings_action)
        more_menu.addAction(spotify_action)
        more_menu.addSeparator()
        more_menu.addAction(import_action)
        more_menu.addAction(open_output_action)
        more_menu.addSeparator()
        more_menu.addAction(self.logs_action)

    def _connect_signals(self) -> None:
        self.sidebar.source_selected.connect(self._load_source)
        self.sidebar.refresh_requested.connect(self._refresh_source)
        self.sidebar.delete_requested.connect(self._remove_source)
        self.sidebar.add_requested.connect(lambda: self.link_edit.setFocus())
        self.sidebar.open_url_requested.connect(self._open_url)

        self.track_table.checked_changed.connect(self._on_checked_changed)
        self.track_table.edit_details_requested.connect(self._open_details)
        self.track_table.retry_requested.connect(self._retry_track)
        self.track_table.reconvert_requested.connect(self._reconvert_track)
        self.track_table.remove_requested.connect(self._remove_track)
        self.track_table.open_source_requested.connect(self._open_url)
        self.track_table.table.selectionModel().selectionChanged.connect(
            self._on_selection_changed
        )

        self.inspector.edit_details_requested.connect(self._open_details)
        self.inspector.reconvert_requested.connect(self._reconvert_track)
        self.inspector.retry_requested.connect(self._retry_track)
        self.inspector.open_source_requested.connect(self._open_url)
        self.inspector.reveal_requested.connect(self._reveal_track)

        self.status.cancel_clicked.connect(self._cancel)
        self.status.open_output.connect(self._open_output)
        self.status.spotify_clicked.connect(self._open_spotify)
        self.status.log_clicked.connect(lambda: self.logs_action.toggle())
        self.log_drawer.closed.connect(lambda: self.logs_action.setChecked(False))
        self.log_drawer.open_log_file.connect(self._open_log_folder)

        pipeline = self.ctx.pipeline
        pipeline.track_changed.connect(self._on_track_changed)
        pipeline.progress.connect(self._on_progress)
        pipeline.message.connect(self._on_pipeline_message)
        pipeline.finished.connect(self._on_pipeline_finished)
        pipeline.failed.connect(self._on_pipeline_failed)

    def _install_log_handler(self) -> None:
        self.log_emitter = LogEmitter(self)
        self.log_handler = QtLogHandler(self.log_emitter)
        self.logger.addHandler(self.log_handler)
        self.log_emitter.message.connect(self._on_log)

    # --------------------------------------------------------------- sources

    def _refresh_sources(self, selected_id: Optional[int] = None) -> None:
        sources = self.db.get_sources()
        counts = {source.id: self.db.status_counts(source.id) for source in sources}
        target = selected_id
        if target is None:
            target = self._current_source_id or self.settings.last_source_id
        if target is not None and not any(s.id == target for s in sources):
            target = None
        if target is None and sources:
            target = sources[0].id

        self.sidebar.set_sources(sources, counts, target)
        if target is None:
            self._current_source_id = None
            self.track_table.set_source(None, [])
            self.inspector.set_row(None)
        else:
            self._load_source(target)

    def _load_source(self, source_id: int) -> None:
        source = self.db.get_source(source_id)
        if source is None:
            return
        self._current_source_id = source_id
        self.settings.last_source_id = source_id
        rows = self.db.get_tracks_for_source(source_id)
        checked = self._checked_by_source.get(source_id)
        if checked is None:
            checked = {row.track.id for row in rows if row.track.status == "new"}
        self._checked_by_source[source_id] = set(checked)
        self.track_table.set_source(source, rows, checked)
        self.inspector.set_row(None)
        self._update_actions()

    def _reload_current_source(self) -> None:
        if self._current_source_id is not None:
            self._load_source(self._current_source_id)

    def _on_selection_changed(self, *_args) -> None:
        row = self.track_table.current_row()
        if self.settings.mode == "pro":
            self.inspector.set_row(row)

    def _on_checked_changed(self) -> None:
        if self._current_source_id is not None:
            self._checked_by_source[self._current_source_id] = (
                self.track_table.model.checked_ids()
            )
        self._update_actions()
        self._update_steps()

    # ---------------------------------------------------------------- adding

    def _add_from_input(self) -> None:
        text = self.link_edit.text().strip()
        if not text:
            self.link_edit.setFocus()
            return
        self.link_edit.clear()
        self._add_links_text(text)

    def _paste_links(self) -> None:
        text = QApplication.clipboard().text()
        if not text.strip():
            self.status.set_idle("Clipboard is empty.")
            return
        self._add_links_text(text)

    def _bulk_add(self) -> None:
        dialog = BulkAddDialog(self)
        if dialog.exec() == QDialog.Accepted and dialog.links:
            self._add_links_text("\n".join(dialog.links))

    def _add_links_text(self, text: str) -> None:
        if self._is_busy():
            self.status.set_idle("Wait for the current job to finish.")
            return
        links = split_links(text)
        if not links:
            self.status.set_idle(
                "No usable links found. Paste a full YouTube, X, or other URL."
            )
            return

        self._add_running = True
        self._add_cancel = threading.Event()
        self._set_controls_enabled(False)
        self.status.set_busy(f"Reading {len(links)} link(s)…")

        signals = LibrarySignals()
        signals.progress.connect(self._on_add_progress)
        signals.finished.connect(self._on_add_finished)
        signals.failed.connect(self._on_add_failed)
        self._active_signals = signals

        task = AddLinksTask(self.ctx.library, links, self._add_cancel, signals)
        QThreadPool.globalInstance().start(task)

    def _on_add_progress(self, current: int, total: int, url: str) -> None:
        self.status.set_add_progress(current, total, url)

    def _on_add_finished(self, result: AddResult) -> None:
        self._add_running = False
        self._set_controls_enabled(True)

        if result.sources:
            self._refresh_sources(result.sources[0])
            summary = (
                f"Added {len(result.sources)} source(s), "
                f"{result.tracks_total} track(s)"
            )
            if result.tracks_new:
                summary += f", {result.tracks_new} new"
            if result.tracks_owned:
                summary += f", {result.tracks_owned} already downloaded"
            if result.errors:
                summary += f" · {len(result.errors)} link(s) failed (see Logs)"
            self.status.set_idle(summary)
            self.logger.info(summary)
        else:
            self.status.set_idle("Could not add the link.")
            if result.errors:
                self.logger.error(result.errors[0])
                QMessageBox.warning(
                    self, "Could not add link", result.errors[0]
                )
        for error in result.errors:
            self.logger.warning(error)

    def _on_add_failed(self, message: str) -> None:
        self._add_running = False
        self._set_controls_enabled(True)
        self.status.set_idle("Could not read the link.")
        self.logger.error(message)
        QMessageBox.critical(self, "Could not read link", message)

    # ------------------------------------------------------------- refreshing

    def _refresh_current(self) -> None:
        if self._current_source_id is not None:
            self._refresh_source(self._current_source_id)

    def _refresh_source(self, source_id: int) -> None:
        if self._is_busy():
            self.status.set_idle("Wait for the current job to finish.")
            return
        self._add_running = True
        self._add_cancel = threading.Event()
        self._set_controls_enabled(False)
        self.status.set_busy("Refreshing source…")

        signals = LibrarySignals()
        signals.finished.connect(self._on_refresh_finished)
        signals.failed.connect(self._on_add_failed)
        self._active_signals = signals
        QThreadPool.globalInstance().start(
            RefreshSourceTask(
                self.ctx.library, source_id, self._add_cancel, signals
            )
        )

    def _on_refresh_finished(self, result: AddResult) -> None:
        self._add_running = False
        self._set_controls_enabled(True)
        self._refresh_sources(result.sources[0] if result.sources else None)
        if result.errors:
            self.status.set_idle(
                f"Refresh finished with {len(result.errors)} problem(s) — see Logs."
            )
            for error in result.errors:
                self.logger.warning(error)
        else:
            self.status.set_idle(
                f"Refreshed: {result.tracks_total} tracks, "
                f"{result.tracks_new} new."
            )

    # --------------------------------------------------------------- actions

    def _update_actions(self) -> None:
        while self.action_layout.count():
            item = self.action_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        count = self.track_table.checked_count()
        busy = self._is_busy()
        ffmpeg_ok = self.ctx.ffmpeg.available

        if self.settings.mode == "easy":
            label = f"Download to Spotify ({count})" if ffmpeg_ok else (
                f"Download audio ({count})"
            )
            button = QPushButton(label)
            button.setObjectName("primary")
            button.setEnabled(count > 0 and not busy)
            button.clicked.connect(self._start_easy)
            self.action_layout.addWidget(button)
        else:
            download = QPushButton(f"Download ({count})")
            download.setEnabled(count > 0 and not busy)
            download.clicked.connect(lambda: self._start_stage("download"))
            self.action_layout.addWidget(download)

            convert = QPushButton(f"Convert ({count})")
            convert.setEnabled(count > 0 and not busy and ffmpeg_ok)
            convert.setToolTip(
                "" if ffmpeg_ok else "FFmpeg is required for conversion."
            )
            convert.clicked.connect(lambda: self._start_stage("convert"))
            self.action_layout.addWidget(convert)

            sync = QPushButton(f"Spotify ({count})")
            sync.setEnabled(count > 0 and not busy)
            sync.clicked.connect(lambda: self._start_stage("sync"))
            self.action_layout.addWidget(sync)

        self.add_button.setEnabled(not busy)
        self.paste_button.setEnabled(not busy)
        self.mode_combo.setEnabled(not busy)
        self.settings_action.setEnabled(not busy)
        self.sidebar.set_busy(busy)
        self._update_steps()

    def _update_steps(self) -> None:
        """Reflect the current selection in the pipeline strip."""
        if self._is_busy():
            return

        count = self.track_table.checked_count()
        ffmpeg_ok = self.ctx.ffmpeg.available
        plural = "" if count == 1 else "s"
        if count:
            detail = f"{count} song{plural} selected"
        elif self._current_source_id is None:
            detail = "Start by adding a link above"
        else:
            detail = "Tick the songs you want"

        enabled = (count > 0, count > 0 and ffmpeg_ok, count > 0)
        self.steps.set_clickable(self.settings.mode == "pro")
        self.steps.reset(detail, enabled=enabled)

    def _on_step_clicked(self, index: int) -> None:
        """In Pro mode, each step is also a button."""
        if self.settings.mode != "pro" or self._is_busy():
            return
        stage = ("download", "convert", "sync")[index]
        self._start_stage(stage)

    def _set_stage_active(self, index: int, detail: str) -> None:
        for earlier in range(index):
            self.steps.set_step(earlier, DONE, "Done")
        self.steps.set_step(index, ACTIVE, detail)

    def _finish_steps(self, summary: dict) -> None:
        if summary.get("cancelled"):
            self.steps.reset("Cancelled", enabled=(True, True, True))
            return
        stages = summary.get("stages") or []
        index_by_stage = {"download": 0, "convert": 1, "sync": 2}
        last_index = -1
        for stage in stages:
            index = index_by_stage.get(stage)
            if index is not None:
                last_index = max(last_index, index)
        for stage in stages:
            index = index_by_stage.get(stage)
            if index is None:
                continue
            if summary.get("failed") and index == last_index:
                self.steps.set_step(index, STEP_ERROR, "Finished with errors")
            else:
                self.steps.set_step(index, DONE, "Done")

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.add_button.setEnabled(enabled)
        self.paste_button.setEnabled(enabled)
        self.mode_combo.setEnabled(enabled)
        self.settings_action.setEnabled(enabled)
        self.sidebar.set_busy(not enabled)
        self._update_actions()

    def _is_busy(self) -> bool:
        return self._add_running or self.ctx.pipeline.is_running

    def _start_easy(self) -> None:
        rows = self.track_table.checked_rows()
        if not rows:
            self.status.set_idle("Select at least one track.")
            return
        if self.ctx.ffmpeg.available:
            stages = ["download", "convert", "sync"]
        else:
            stages = ["download"]
            self.logger.warning(
                "FFmpeg is missing; downloading audio without conversion."
            )
        self._start_pipeline(rows, stages)

    def _start_stage(self, stage: str) -> None:
        rows = self.track_table.checked_rows()
        if not rows:
            self.status.set_idle("Select at least one track.")
            return
        if stage == "convert" and not self.ctx.ffmpeg.available:
            QMessageBox.warning(
                self, "FFmpeg required",
                "Locate FFmpeg in Settings before converting.",
            )
            return
        self._start_pipeline(rows, [stage])

    def _start_pipeline(
        self,
        rows: list[TrackRow],
        stages: list[str],
        *,
        force: bool = False,
    ) -> None:
        if self._is_busy():
            self.status.set_idle("A job is already running.")
            return
        items = [
            BatchItem(source=row.source, track=row.track, position=row.position)
            for row in rows
        ]
        if not self.ctx.pipeline.start(items, stages, force=force):
            self.status.set_idle("Could not start the job.")
            return

        self.track_table.clear_progress()
        self.status.set_busy(f"Starting {' → '.join(stages)}…")
        index_by_stage = {"download": 0, "convert": 1, "sync": 2}
        for stage in stages:
            index = index_by_stage.get(stage)
            if index is not None:
                self.steps.set_step(index, WAITING, "Waiting")
        self._set_stage_active(index_by_stage[stages[0]], "Starting…")
        self.logger.info(
            "Starting %s for %d track(s)", " → ".join(stages), len(items)
        )
        self._update_actions()

    # -------------------------------------------------------------- pipeline

    def _on_track_changed(self, track_id: int) -> None:
        track = self.db.get_track(track_id)
        if track is not None:
            self.track_table.update_track(track)

    def _on_progress(self, data: dict) -> None:
        self.status.set_progress(data)
        track_id = data.get("track_id")
        if track_id is not None:
            self.track_table.set_progress(
                int(track_id), float(data.get("item_percent") or 0)
            )

        index_by_stage = {"download": 0, "convert": 1, "sync": 2}
        index = index_by_stage.get(data.get("stage"))
        if index is not None:
            title = data.get("title") or ""
            percent = float(data.get("item_percent") or 0)
            self._set_stage_active(index, f"{title} — {percent:.0f}%")

    def _on_pipeline_message(self, level: str, message: str) -> None:
        self.logger.log(
            getattr(logging, level.upper(), logging.INFO), message
        )
        if level.upper() in ("WARNING", "ERROR") and not self.log_drawer.isVisible():
            self.logs_action.setChecked(True)

    def _on_pipeline_finished(self, summary: dict) -> None:
        self.track_table.clear_progress()
        self._reload_current_source()
        self._refresh_sources(self._current_source_id)
        self._update_actions()
        self._finish_steps(summary)
        self._refresh_setup_banner()

        parts = []
        if summary.get("downloaded"):
            parts.append(f"{summary['downloaded']} downloaded")
        if summary.get("converted"):
            parts.append(f"{summary['converted']} converted")
        if summary.get("synced"):
            parts.append(f"{summary['synced']} sent to Spotify")
        if summary.get("failed"):
            parts.append(f"{summary['failed']} failed")
        message = ", ".join(parts) if parts else "Finished."

        if summary.get("cancelled"):
            self.status.set_idle("Job cancelled.")
        elif summary.get("failed"):
            self.status.set_idle(f"{message} — see Logs for details.")
            self.logs_action.setChecked(True)
        else:
            self.status.set_idle(message)

        for error in summary.get("errors", []):
            self.logger.warning(error)

        if summary.get("synced"):
            self._notify(
                "Sent to Spotify",
                f"{summary['synced']} track(s) copied. If they are not in "
                "Spotify yet, restart it from the Spotify button.",
            )
        elif not summary.get("cancelled") and not summary.get("failed"):
            self._notify("Music DLP", message)

    def _on_pipeline_failed(self, title: str, message: str) -> None:
        self._update_actions()
        self.status.set_idle(title)
        self.logger.error("%s: %s", title, message)
        QMessageBox.critical(self, title, message)

    def _cancel(self) -> None:
        if self.ctx.pipeline.is_running:
            self.ctx.pipeline.cancel()
            self.status.set_busy("Cancelling…")
        elif self._add_running:
            self._add_cancel.set()
            self.status.set_busy("Cancelling…")

    # ------------------------------------------------------------- track ops

    def _open_details(self, track_id: int) -> None:
        """Edit one song's title, artist, album, track number, and picture."""
        if self._is_busy():
            self.status.set_idle("Wait for the current job to finish.")
            return

        row = self.track_table.model.row_for_id(track_id)
        if row is None:
            return

        album_default = (
            row.source.title if row.source.kind == "playlist" else "Singles"
        )
        dialog = TrackDetailsDialog(
            row.track,
            album_default=album_default,
            track_default=row.position,
            filename_template=self.settings.filename_template,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted or not dialog.result_values:
            return

        values = dialog.result_values
        self.ctx.library.set_metadata(
            track_id,
            title=values["title"],
            artist=values["artist"],
            album=values["album"],
            track_number=values["track_number"],
        )
        if values["cover_changed"]:
            try:
                if values["cover_path"]:
                    self.ctx.library.set_cover(track_id, Path(values["cover_path"]))
                else:
                    self.ctx.library.restore_video_cover(track_id)
            except AppError as exc:
                QMessageBox.warning(self, "Picture not used", exc.user_text())

        self._refresh_sources(self._current_source_id)
        updated = self.db.get_track(track_id)

        if values["update_file"] and updated is not None:
            stages = ["convert"]
            if updated.synced_at:
                stages.append("sync")
            row = self.track_table.model.row_for_id(track_id)
            if row is not None:
                self._start_pipeline([row], stages, force=True)
                self.status.set_busy("Updating the song with your new details…")
                return

        self.status.set_idle(
            "Details saved. They are written into the file when you convert it."
        )

    def _apply_metadata(
        self,
        track_id: int,
        title: str,
        artist: str,
        album: str,
        track_number: int,
    ) -> None:
        self.ctx.library.set_metadata(
            track_id,
            title=title,
            artist=artist,
            album=album,
            track_number=track_number or None,
        )
        self._reload_current_source()
        row = self.track_table.model.row_for_id(track_id)
        self.inspector.set_row(row)
        self.status.set_idle(
            "Metadata saved. Use Re-convert to write it into the files."
        )

    def _retry_track(self, track_id: int) -> None:
        row = self.track_table.model.row_for_id(track_id)
        if row is None:
            return
        track = row.track
        has_raw = bool(track.raw_path and Path(track.raw_path).exists())
        has_output = any(Path(p).exists() for p in track.output_paths)

        if track.sync_error and has_output:
            stages = ["sync"]
            force = False
        elif has_raw:
            stages = ["convert", "sync"] if self.ctx.ffmpeg.available else ["sync"]
            force = True
        elif has_output:
            stages = ["sync"]
            force = False
        else:
            stages = (
                ["download", "convert", "sync"]
                if self.ctx.ffmpeg.available else ["download"]
            )
            force = True
        self._start_pipeline([row], stages, force=force)

    def _reconvert_track(self, track_id: int) -> None:
        row = self.track_table.model.row_for_id(track_id)
        if row is None:
            return
        if not self.ctx.ffmpeg.available:
            QMessageBox.warning(
                self, "FFmpeg required",
                "Locate FFmpeg in Settings before converting.",
            )
            return
        raw = row.track.raw_path
        stages = ["convert"] if raw and Path(raw).exists() else [
            "download", "convert"
        ]
        self._start_pipeline([row], stages, force=True)

    def _remove_track(self, track_id: int) -> None:
        reply = QMessageBox.question(
            self,
            "Remove track",
            "Remove this track from the library?\n\n"
            "Yes — also delete downloaded and converted files.\n"
            "No — keep the files on disk.\n"
            "Cancel — do nothing.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Cancel:
            return
        self.ctx.library.delete_track(
            track_id, delete_files=(reply == QMessageBox.Yes)
        )
        self._refresh_sources(self._current_source_id)

    def _remove_source(self, source_id: int) -> None:
        source = self.db.get_source(source_id)
        if source is None:
            return
        reply = QMessageBox.question(
            self,
            "Remove source",
            f"Remove “{source.title}” from the library?\n\n"
            "Yes — also delete its files that are not used by other sources.\n"
            "No — keep the files on disk.\n"
            "Cancel — do nothing.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply == QMessageBox.Cancel:
            return
        self.ctx.library.delete_source(
            source_id, delete_files=(reply == QMessageBox.Yes)
        )
        if self._current_source_id == source_id:
            self._current_source_id = None
        self._refresh_sources()

    def _reveal_track(self, track_id: int) -> None:
        track = self.db.get_track(track_id)
        if track is None:
            return
        path = track.mp3_path or track.m4a_path or track.raw_path
        if path and Path(path).exists():
            self._reveal_path(Path(path))
        else:
            self._open_output()

    def _reveal_path(self, path: Path) -> None:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            SpotifyService.open_folder(path.parent)

    def _open_url(self, url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    def _open_output(self) -> None:
        SpotifyService.open_folder(self.settings.output_path)

    def _open_log_folder(self) -> None:
        from musicdlp.core.paths import log_dir
        SpotifyService.open_folder(log_dir())

    # -------------------------------------------------------------- settings

    def _open_setup(self) -> None:
        """Open the guided setup checklist."""
        if self._is_busy():
            self.status.set_idle("Wait for the current job to finish.")
            return
        dialog = SetupDialog(
            self.settings, self.ctx.ffmpeg, self.ctx.spotify, self
        )
        dialog.exec()

        # The dialog may have changed the FFmpeg path or the Spotify folder.
        self.settings = Settings.load()
        self.ctx.rebuild(self.settings)
        self.inspector.thumbnails = self.ctx.thumbnails
        self._sync_mode_combo()
        self._update_spotify_state()
        self._refresh_setup_banner()
        self._update_actions()
        self.status.set_idle("Setup saved.")

    def _refresh_setup_banner(self) -> None:
        """Show one clear prompt for whatever is still missing."""
        if not self.ctx.ffmpeg.available:
            self.setup_banner.show_prompt(
                "ffmpeg",
                "Music DLP needs a free helper called FFmpeg to turn videos "
                "into MP3 files. It is not installed yet.",
                "Set up FFmpeg",
            )
            return

        if not self.ctx.spotify.is_installed():
            self.setup_banner.show_prompt(
                "spotify",
                "The Spotify app was not found on this computer. You can still "
                "download songs, but they cannot appear in Spotify until it is "
                "installed.",
                "Set up Spotify",
            )
            return

        folder = self.settings.spotify_path
        if not is_writable(folder):
            self.setup_banner.show_prompt(
                "folder",
                "Music DLP cannot save songs into the Spotify folder. Choose a "
                "folder it is allowed to write to.",
                "Choose folder",
            )
            return

        registered = self.ctx.spotify.source_registered(folder)
        if registered is False and self.ctx.spotify.folder_has_files(folder):
            self.setup_banner.show_prompt(
                "source",
                "Spotify does not know about your songs folder yet, so songs "
                "will not appear in Spotify. It takes about a minute to fix.",
                "Show me how",
            )
            return

        self.setup_banner.clear_prompt()

    def _open_settings(self) -> None:
        if self._is_busy():
            self.status.set_idle("Wait for the current job to finish.")
            return
        dialog = SettingsDialog(
            self.settings,
            self.ctx.ffmpeg,
            self.ctx.ytdlp,
            self.ctx.spotify,
            self,
        )
        if dialog.exec() != QDialog.Accepted or dialog.result_settings is None:
            return

        self.ctx.rebuild(dialog.result_settings)
        self.settings = self.ctx.settings
        self.inspector.thumbnails = self.ctx.thumbnails
        apply_theme(QApplication.instance(), self.settings.theme)
        self._sync_mode_combo()
        self._apply_mode()
        self.status.set_output_path(self.settings.output_path)
        self._update_spotify_state()
        self._reload_current_source()
        self._update_actions()
        self.status.set_idle("Settings saved.")

    def _sync_mode_combo(self) -> None:
        index = self.mode_combo.findData(self.settings.mode)
        if index >= 0:
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(index)
            self.mode_combo.blockSignals(False)

    def _on_mode_changed(self, _index: int) -> None:
        mode = self.mode_combo.currentData()
        if mode == self.settings.mode:
            return
        self.settings.mode = mode
        try:
            self.settings.save()
        except OSError:
            pass
        self._apply_mode()
        self._update_actions()

    def _apply_mode(self) -> None:
        self.track_table.set_mode(self.settings.mode)
        show_inspector = self.settings.mode == "pro"
        self.inspector_scroll.setVisible(show_inspector)
        self.inspector_action.setChecked(show_inspector)
        # Switching to Pro should show the highlighted song immediately.
        if show_inspector:
            self.inspector.set_row(self.track_table.current_row())

    def _open_spotify(self) -> None:
        dialog = SpotifyDialog(self.settings.spotify_dir, self.ctx.spotify, self)
        if dialog.exec() == QDialog.Accepted and dialog.selected_dir:
            self.settings.spotify_dir = dialog.selected_dir
            try:
                self.settings.save()
            except OSError:
                pass
            self._update_spotify_state()
            self.status.set_idle("Spotify folder saved.")

    def _update_spotify_state(self) -> None:
        exe = self.ctx.spotify.find_exe()
        if exe:
            self.status.set_spotify_state("Spotify", str(exe))
        else:
            self.status.set_spotify_state("Spotify: not found", "Spotify was not found.")
        self.status.set_output_path(self.settings.output_path)

    # ---------------------------------------------------------------- legacy

    def _import_legacy(self) -> None:
        path = find_legacy_db()
        if path is None:
            filename, _ = QFileDialog.getOpenFileName(
                self, "Select old music_dlp.db", "",
                "Databases (*.db);;All files (*)",
            )
            if not filename:
                return
            path = Path(filename)

        reply = QMessageBox.question(
            self,
            "Import old library",
            f"Import sources and tracks from:\n{path}\n\n"
            "Existing files are referenced in place; nothing is moved.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            summary = import_legacy(self.db, path)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        QApplication.restoreOverrideCursor()

        self._refresh_sources()
        message = (
            f"Imported {summary.tracks} track(s) in "
            f"{summary.sources} source(s)."
        )
        if summary.errors:
            message += f"\n{len(summary.errors)} row(s) could not be imported."
        QMessageBox.information(self, "Import complete", message)

    # ------------------------------------------------------------------ misc

    def _toggle_logs(self, visible: bool) -> None:
        self.log_drawer.setVisible(visible)
        if not visible:
            self.logs_action.setChecked(False)

    def _on_log(self, level: str, message: str) -> None:
        self.log_drawer.append(level, message)

    def _notify(self, title: str, message: str) -> None:
        if not self.settings.notifications:
            return
        if self.isActiveWindow():
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(
            self.style().standardIcon(QStyle.SP_MediaVolume), self
        )
        tray.show()
        tray.showMessage(title, message, QSystemTrayIcon.Information, 6000)
        self._tray = tray
        QTimer.singleShot(8000, tray.hide)

    def _about(self) -> None:
        from musicdlp import __version__
        QMessageBox.about(
            self,
            "About Music DLP",
            f"Music DLP {__version__}\n\n"
            "Downloads audio from YouTube, X/Twitter, and other yt-dlp sites, "
            "converts it to tagged MP3/M4A, and delivers it to Spotify Local "
            "Files.\n\n"
            f"yt-dlp {self.ctx.ytdlp.version()}",
        )

    # ----------------------------------------------------------- window state

    def _restore_geometry(self) -> None:
        if not self.settings.window_geometry:
            return
        try:
            from PySide6.QtCore import QByteArray
            data = QByteArray.fromBase64(
                self.settings.window_geometry.encode("ascii")
            )
            self.restoreGeometry(data)
        except Exception:
            pass

    def _save_window_state(self) -> None:
        try:
            data = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self.settings.window_geometry = data
            self.settings.last_source_id = self._current_source_id
            self.settings.save()
        except OSError:
            pass

    # ---------------------------------------------------------------- events

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        text = ""
        if event.mimeData().hasUrls():
            text = "\n".join(
                url.toString() for url in event.mimeData().urls()
            )
        if not text and event.mimeData().hasText():
            text = event.mimeData().text()
        if text:
            self._add_links_text(text)
            event.acceptProposedAction()
        else:
            event.ignore()

    def closeEvent(self, event) -> None:
        if self._is_busy():
            reply = QMessageBox.question(
                self,
                "Job running",
                "A download or conversion is still running. Cancel it and exit?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self._cancel()
            self.ctx.pipeline.wait(10)
            self._add_cancel.set()

        self._save_window_state()
        self.logger.removeHandler(self.log_handler)
        self.db.close()
        event.accept()
