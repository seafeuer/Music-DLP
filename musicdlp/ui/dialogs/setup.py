"""A plain-language setup checklist for dependencies and Spotify."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from musicdlp.core.config import Settings
from musicdlp.core.paths import is_writable, start_directory
from musicdlp.services import deps
from musicdlp.services.ffmpeg import FFmpegService
from musicdlp.services.spotify import SpotifyService

SPOTIFY_DOWNLOAD_URL = "https://www.spotify.com/download/windows/"


class _InstallSignals(QObject):
    progress = Signal(str)
    finished = Signal(bool, str)


class _InstallTask(QRunnable):
    def __init__(self, signals: _InstallSignals) -> None:
        super().__init__()
        self.signals = signals
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            ok, message, _path = deps.install_ffmpeg(
                progress=lambda line: self.signals.progress.emit(line),
                cancel_event=self.cancel,
            )
        except Exception as exc:
            ok, message = False, str(exc)
        self.signals.finished.emit(ok, message)


class SetupDialog(QDialog):
    """Guides the user through every dependency, in order."""

    def __init__(
        self,
        settings: Settings,
        ffmpeg: FFmpegService,
        spotify: SpotifyService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set up Music DLP")
        self.resize(720, 680)
        self.settings = settings
        self.ffmpeg = ffmpeg
        self.spotify = spotify
        self.saved = False
        self._install_task: Optional[_InstallTask] = None

        outer = QVBoxLayout(self)

        title = QLabel("Set up Music DLP")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        outer.addWidget(title)

        subtitle = QLabel(
            "There are a few things to set up once. Green ticks mean you are "
            "ready. You can reopen this any time from the Help menu."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        self.content = QVBoxLayout(inner)
        self.content.setSpacing(12)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # Step 1: FFmpeg ------------------------------------------------
        self.ffmpeg_card, ffmpeg_layout = self._card(
            "1. A helper called FFmpeg",
            "Music DLP uses FFmpeg to turn a video into an MP3 file. "
            "Without it, songs can be downloaded but not converted.",
        )
        self.ffmpeg_status = QLabel("")
        ffmpeg_layout.addWidget(self.ffmpeg_status)

        ffmpeg_buttons = QHBoxLayout()
        self.install_button = QPushButton("Install it for me")
        self.install_button.setObjectName("primary")
        self.install_button.clicked.connect(self._install_ffmpeg)
        self.locate_button = QPushButton("I already have it…")
        self.locate_button.clicked.connect(self._locate_ffmpeg)
        self.download_button = QPushButton("Download FFmpeg")
        self.download_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl("https://www.gyan.dev/ffmpeg/builds/")
            )
        )
        ffmpeg_buttons.addWidget(self.install_button)
        ffmpeg_buttons.addWidget(self.locate_button)
        ffmpeg_buttons.addWidget(self.download_button)
        ffmpeg_buttons.addStretch(1)
        ffmpeg_layout.addLayout(ffmpeg_buttons)

        self.ffmpeg_progress = QLabel("")
        self.ffmpeg_progress.setObjectName("mutedSmall")
        self.ffmpeg_progress.setWordWrap(True)
        self.ffmpeg_progress.hide()
        ffmpeg_layout.addWidget(self.ffmpeg_progress)

        # Step 2: Spotify installed -------------------------------------
        self.spotify_card, spotify_layout = self._card(
            "2. The Spotify desktop app",
            "Songs are shown in Spotify on this computer. The Spotify web "
            "player in a browser cannot see local files, so you need the "
            "Spotify app installed.",
        )
        self.spotify_status = QLabel("")
        spotify_layout.addWidget(self.spotify_status)

        spotify_buttons = QHBoxLayout()
        self.open_spotify_button = QPushButton("Open Spotify")
        self.open_spotify_button.clicked.connect(self._open_spotify)
        get_spotify = QPushButton("Get Spotify")
        get_spotify.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(SPOTIFY_DOWNLOAD_URL))
        )
        spotify_buttons.addWidget(self.open_spotify_button)
        spotify_buttons.addWidget(get_spotify)
        spotify_buttons.addStretch(1)
        spotify_layout.addLayout(spotify_buttons)

        # Step 3: Folder -------------------------------------------------
        self.folder_card, folder_layout = self._card(
            "3. Where your songs are kept",
            "A folder is simply a place on your computer that holds files, "
            "like a drawer. Music DLP saves your songs in this drawer and "
            "Spotify will read them from here.",
        )
        self.folder_edit = QLineEdit(self.settings.spotify_dir)
        self.folder_edit.setPlaceholderText(
            r"Paste a path here, e.g. C:\Users\you\MusicDLP\Spotify"
        )
        self.folder_edit.editingFinished.connect(self._folder_typed)
        folder_layout.addWidget(self.folder_edit)

        folder_buttons = QHBoxLayout()
        change = QPushButton("Change…")
        change.clicked.connect(self._change_folder)
        open_folder = QPushButton("Open this folder")
        open_folder.clicked.connect(self._open_folder)
        copy_path = QPushButton("Copy folder path")
        copy_path.clicked.connect(self._copy_path)
        folder_buttons.addWidget(change)
        folder_buttons.addWidget(open_folder)
        folder_buttons.addWidget(copy_path)
        folder_buttons.addStretch(1)
        folder_layout.addLayout(folder_buttons)

        # Step 4: Tell Spotify -------------------------------------------
        self.tell_card, tell_layout = self._card(
            "4. Show the folder to Spotify (one time)",
            "Spotify does not let other apps add songs for it, so this part "
            "is done by you in Spotify. It takes about a minute.",
        )

        steps = QLabel(
            "1.  Click “Open Spotify” below.\n"
            "2.  In Spotify, click the arrow next to your name at the top "
            "right, then choose Settings.\n"
            "3.  Scroll down to “Local files” and switch on “Show local "
            "files”.\n"
            "4.  Click “Add a source”. A folder picker opens. Go to the "
            "folder shown in step 3 above and click Select Folder. "
            "(Use “Copy folder path” if you would rather paste it.)\n"
            "5.  Come back here and click “Restart Spotify” so it reads the "
            "songs."
        )
        steps.setWordWrap(True)
        tell_layout.addWidget(steps)

        tell_buttons = QHBoxLayout()
        open_settings = QPushButton("Open Spotify")
        open_settings.clicked.connect(self._open_spotify)
        restart = QPushButton("Restart Spotify")
        restart.clicked.connect(self._restart_spotify)
        check = QPushButton("Check again")
        check.clicked.connect(self._check_spotify_source)
        tell_buttons.addWidget(open_settings)
        tell_buttons.addWidget(restart)
        tell_buttons.addWidget(check)
        tell_buttons.addStretch(1)
        tell_layout.addLayout(tell_buttons)

        self.tell_status = QLabel("")
        self.tell_status.setWordWrap(True)
        tell_layout.addWidget(self.tell_status)

        note = QLabel(
            "After this, new songs appear in Spotify under “Your Library → "
            "Local Files”.\n\n"
            "If Spotify also lists songs you did not add, it is showing your "
            "Music and Downloads folders, which it does by default. Switch "
            "those off in the same Local files list so only Music DLP songs "
            "appear."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        tell_layout.addWidget(note)

        self.content.addStretch(1)

        buttons = QDialogButtonBox()
        save = buttons.addButton("Save and close", QDialogButtonBox.AcceptRole)
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        outer.addWidget(buttons)

        self.refresh()

    # ------------------------------------------------------------------ UI

    def _card(self, title: str, body: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("panel")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        heading = QLabel(title)
        heading.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(heading)

        text = QLabel(body)
        text.setObjectName("muted")
        text.setWordWrap(True)
        layout.addWidget(text)

        self.content.addWidget(card)
        return card, layout

    def _mark(self, label: QLabel, ok: bool, text: str) -> None:
        label.setText(("✓  " if ok else "•  ") + text)
        label.setStyleSheet(
            "color: #16a34a; font-weight: 600;" if ok
            else "color: #d97706; font-weight: 600;"
        )

    def refresh(self) -> None:
        self.ffmpeg = FFmpegService(self.settings.ffmpeg_path)
        if self.ffmpeg.available:
            self._mark(self.ffmpeg_status, True, "Ready — FFmpeg is installed.")
            self.install_button.setText("Reinstall")
        else:
            self._mark(
                self.ffmpeg_status, False,
                "Not installed yet. Music DLP cannot convert songs without it.",
            )
            self.install_button.setText("Install it for me")

        if self.spotify.is_installed():
            self._mark(self.spotify_status, True, "Ready — Spotify is installed.")
        else:
            self._mark(
                self.spotify_status, False,
                "Spotify was not found on this computer.",
            )
        self.open_spotify_button.setEnabled(self.spotify.is_installed())

        self.folder_edit.setText(self.settings.spotify_dir)
        folder = Path(self.settings.spotify_dir)
        if is_writable(folder):
            self.folder_card.setToolTip("")
        else:
            self.folder_card.setToolTip("This folder cannot be written to.")

        self._check_spotify_source(quiet=True)

    def _check_spotify_source(self, quiet: bool = False) -> None:
        folder = Path(self.folder_edit.text())
        registered = self.spotify.source_registered(folder)
        if registered is True:
            self._mark(
                self.tell_status, True,
                "Spotify already has this folder. You are all set.",
            )
        elif registered is False:
            self._mark(
                self.tell_status, False,
                "Spotify does not seem to have this folder yet. Follow steps "
                "1–5 above, then click “Check again”.",
            )
        else:
            self.tell_status.setText(
                "Spotify cannot confirm this automatically. If you completed "
                "steps 1–5, you are done."
            )
            self.tell_status.setObjectName("muted")
        if not quiet:
            self.tell_status.setVisible(True)

    # ------------------------------------------------------------- actions

    def _install_ffmpeg(self) -> None:
        self.install_button.setEnabled(False)
        self.ffmpeg_progress.show()
        self.ffmpeg_progress.setText("Starting the installer…")

        signals = _InstallSignals()
        signals.progress.connect(self._on_install_progress)
        signals.finished.connect(self._on_install_finished)
        task = _InstallTask(signals)
        self._install_task = task
        QThreadPool.globalInstance().start(task)

    def _on_install_progress(self, line: str) -> None:
        self.ffmpeg_progress.setText(line[-300:])

    def _on_install_finished(self, ok: bool, message: str) -> None:
        self.install_button.setEnabled(True)
        self.ffmpeg_progress.setText(message)
        if ok:
            found = deps.locate_installed_ffmpeg()
            if found:
                self.settings.ffmpeg_path = str(found)
                self.settings.save()
        self.refresh()

    def _locate_ffmpeg(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Find ffmpeg.exe",
            "",
            "Programs (*.exe);;All files (*)",
        )
        if filename:
            self.settings.ffmpeg_path = filename
            self.settings.save()
            self.refresh()

    def _open_spotify(self) -> None:
        if not self.spotify.open_spotify():
            self._mark(
                self.spotify_status, False,
                "Spotify could not be opened. Is it installed?",
            )

    def _restart_spotify(self) -> None:
        ok, message = self.spotify.restart()
        self.tell_status.setText(message)
        self.tell_status.setStyleSheet(
            "color: #16a34a;" if ok else "color: #d97706;"
        )
        self.tell_status.setVisible(True)

    def _change_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose where to keep your songs",
            str(start_directory(self.folder_edit.text())),
            QFileDialog.ShowDirsOnly,
        )
        if not folder:
            return
        self.folder_edit.setText(folder)
        self._folder_typed()

    def _folder_typed(self) -> None:
        """Accept a pasted path, creating the folder if needed."""
        text = self.folder_edit.text().strip()
        if not text:
            return
        path = Path(text).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.folder_edit.setStyleSheet("border: 1px solid #dc2626;")
            return
        self.folder_edit.setStyleSheet("")
        self.settings.spotify_dir = str(path)
        self.settings.save()
        self.saved = True
        self.refresh()

    def _open_folder(self) -> None:
        SpotifyService.open_folder(Path(self.folder_edit.text()))

    def _copy_path(self) -> None:
        QApplication.clipboard().setText(self.folder_edit.text())

    def _save(self) -> None:
        self.saved = True
        self.accept()

    # -------------------------------------------------------------- close

    def closeEvent(self, event) -> None:
        if self._install_task is not None:
            self._install_task.cancel.set()
        super().closeEvent(event)
