"""A plain-language setup checklist for dependencies and Spotify."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from musicdlp.ui.dialogs.footer import DialogFooter
from musicdlp.ui.dialogs.sizing import fit_dialog

from musicdlp.core.config import Settings
from musicdlp.core.paths import is_writable, start_directory
from musicdlp.services import deps
from musicdlp.services.ffmpeg import FFmpegService
from musicdlp.services.spotify import SpotifyService
from musicdlp.ui.theme import set_tone

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
        fit_dialog(self, 720, 500)
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

        # One step per page. Scrolling a tall list put the five Local
        # Files instructions, which are the point of the wizard, below the
        # fold with no hint that anything followed.
        self.step_label = QLabel("")
        self.step_label.setObjectName("muted")
        outer.addWidget(self.step_label)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.cards: list[tuple[str, QWidget]] = []

        # Step 1: FFmpeg ------------------------------------------------
        self.ffmpeg_card, ffmpeg_layout = self._card(
            "A helper called FFmpeg",
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
            "The Spotify desktop app",
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
            "Where your songs are kept",
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
            "Show the folder to Spotify (one time)",
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
            "folder from step 3 and click Select Folder. "
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

        # Step 5: done. The picture fix lives here because it is the one
        # thing people hit after setup, and it needs somewhere to live.
        self.tags_card, tags_layout = self._card(
            "You're all set",
            "Close this window, then paste a link into Music DLP to download "
            "your first song. New songs appear in Spotify under “Your Library "
            "→ Local Files”.",
        )

        picture_help = QLabel(
            "Songs appearing without their picture? Spotify is showing an old "
            "cached list. These two buttons fix that; they do not re-download "
            "anything."
        )
        picture_help.setObjectName("muted")
        picture_help.setWordWrap(True)
        tags_layout.addWidget(picture_help)

        tags_buttons = QHBoxLayout()
        rewrite = QPushButton("Update song details and pictures")
        rewrite.clicked.connect(self._rewrite_tags)
        reset_index = QPushButton("Make Spotify re-read the folder")
        reset_index.clicked.connect(self._reset_spotify_index)
        tags_buttons.addWidget(rewrite)
        tags_buttons.addWidget(reset_index)
        tags_buttons.addStretch(1)
        tags_layout.addLayout(tags_buttons)

        self.tags_status = QLabel("")
        self.tags_status.setWordWrap(True)
        tags_layout.addWidget(self.tags_status)

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

        # Push each step's content to the top so short steps do not float
        # in the middle of a tall page.
        for _title, card in self.cards:
            card.layout().addStretch(1)

        self.footer = DialogFooter(self, cancel_text="Close")
        self.back_button = self.footer.add_action("Back", self._back)
        self.next_button = self.footer.add_action("Next", self._next)
        self.footer.set_primary("Save and close", self._save)
        outer.addWidget(self.footer)
        self._show_step(0)

        self.refresh()

    # ------------------------------------------------------------------ UI

    def _card(self, title: str, body: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        # Pages are not boxed: the dialog keeps one height for all steps,
        # and a bordered card around a short page looked like missing
        # content. On the dialog background the empty space reads as
        # breathing room.
        card.setObjectName("plainPage")
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

        self.cards.append((title, card))
        self.stack.addWidget(card)
        # The caller adds the rest of this step; the trailing stretch is
        # added once everything is in place (see __init__).
        return card, layout

    def _mark(self, label: QLabel, ok: bool, text: str) -> None:
        label.setText(("✓  " if ok else "•  ") + text)
        label.setStyleSheet("font-weight: 600;")
        set_tone(label, "success" if ok else "warning")

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
                "1–5 on this page, then click “Check again”.",
            )
        else:
            self.tell_status.setText(
                "Spotify cannot confirm this automatically. If you completed "
                "steps 1–5, you are done."
            )
            self.tell_status.setObjectName("muted")
        if not quiet:
            self.tell_status.setVisible(True)

    # ----------------------------------------------------------------- steps

    def _show_step(self, index: int) -> None:
        index = max(0, min(index, len(self.cards) - 1))
        self.stack.setCurrentIndex(index)
        self._step_index = index
        # The card heading already names the step, so this line only has
        # to say where the user is.
        self.step_label.setText(f"Step {index + 1} of {len(self.cards)}")
        self.back_button.setEnabled(index > 0)
        last = index == len(self.cards) - 1
        # On the last page the primary button is the finish line. Showing
        # both "Finish" and "Save and close" gave the page two ways out
        # that did the same thing.
        self.next_button.setVisible(not last)
        self.footer.set_primary(
            "Finish" if last else "Save and close", self._save
        )

    def _back(self) -> None:
        self._show_step(getattr(self, "_step_index", 0) - 1)

    def _next(self) -> None:
        index = getattr(self, "_step_index", 0)
        if index >= len(self.cards) - 1:
            self._save()
            return
        self._show_step(index + 1)

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

    def _rewrite_tags(self) -> None:
        from musicdlp.core.db import Database
        from musicdlp.core.maintenance import rewrite_tags
        from musicdlp.core.paths import db_path

        db = Database(db_path())
        try:
            notes = rewrite_tags(db, self.settings)
        except Exception as exc:
            notes = [f"Could not update the songs: {exc}"]
        finally:
            db.close()

        self.tags_status.setText("\n".join(notes) or "No songs to update yet.")
        set_tone(self.tags_status, "success")

    def _reset_spotify_index(self) -> None:
        from musicdlp.core.maintenance import refresh_spotify_index

        was_running = self.spotify.is_running()
        if was_running:
            self.spotify.restart()  # closes Spotify first
        ok, message = refresh_spotify_index()
        if ok:
            self.spotify.start()
            message += " Spotify has been restarted."
        self.tags_status.setText(message)
        set_tone(self.tags_status, "success" if ok else "warning")

    def _open_spotify(self) -> None:
        if not self.spotify.open_spotify():
            self._mark(
                self.spotify_status, False,
                "Spotify could not be opened. Is it installed?",
            )

    def _restart_spotify(self) -> None:
        ok, message = self.spotify.restart()
        self.tell_status.setText(message)
        set_tone(self.tell_status, "success" if ok else "warning")
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
            set_tone(self.folder_edit, "danger")
            return
        self.folder_edit.setProperty("tone", "")
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
