"""Settings dialog."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget, QVBoxLayout,
    QWidget,
)

from musicdlp.core.config import Settings
from musicdlp.core.paths import config_path, db_path, log_dir, start_directory
from musicdlp.services.filenames import render_template
from musicdlp.services.ffmpeg import FFmpegService
from musicdlp.services.spotify import SpotifyService
from musicdlp.services.ytdlp import YtDlpService


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: Settings,
        ffmpeg: FFmpegService,
        ytdlp: YtDlpService,
        spotify: SpotifyService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(720, 620)
        self.result_settings: Optional[Settings] = None
        self._ffmpeg = ffmpeg
        self._ytdlp = ytdlp
        self._spotify = spotify
        self.settings = copy.deepcopy(settings)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self._build_general()
        self._build_download()
        self._build_output()
        self._build_spotify()
        self._build_advanced()

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        save_button = buttons.button(QDialogButtonBox.Save)
        if save_button:
            save_button.setObjectName("primary")
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ tabs

    def _build_general(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        self.theme_combo = QComboBox()
        for label, value in [
            ("Follow Windows", "system"),
            ("Dark", "dark"),
            ("Light", "light"),
        ]:
            self.theme_combo.addItem(label, value)
        self._select(self.theme_combo, self.settings.theme)
        form.addRow("Theme", self.theme_combo)

        self.mode_combo = QComboBox()
        for label, value in [
            ("Easy — one button does everything", "easy"),
            ("Pro — separate download / convert / Spotify", "pro"),
        ]:
            self.mode_combo.addItem(label, value)
        self._select(self.mode_combo, self.settings.mode)
        form.addRow("Mode", self.mode_combo)

        self.notifications_check = QCheckBox(
            "Show a Windows notification when a batch finishes"
        )
        self.notifications_check.setChecked(self.settings.notifications)
        form.addRow("Notifications", self.notifications_check)

        self.log_level_combo = QComboBox()
        for value in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.log_level_combo.addItem(value, value)
        self._select(self.log_level_combo, self.settings.log_level)
        form.addRow("Log level", self.log_level_combo)

        self.tabs.addTab(tab, "General")

    def _build_download(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        self.cookies_combo = QComboBox()
        for label, value in [
            ("No cookies", "none"),
            ("Use cookies from a browser", "browser"),
            ("Use a cookies.txt file", "file"),
        ]:
            self.cookies_combo.addItem(label, value)
        self._select(self.cookies_combo, self.settings.cookies_mode)
        self.cookies_combo.currentIndexChanged.connect(self._update_cookies)
        form.addRow("Cookies", self.cookies_combo)
        self._hint(
            form,
            "Some videos and posts only work when you are signed in. "
            "Choosing a browser is usually enough.",
        )

        self.browser_combo = QComboBox()
        for browser in ("chrome", "edge", "firefox", "brave", "opera",
                        "vivaldi", "chromium"):
            self.browser_combo.addItem(browser.title(), browser)
        self._select(self.browser_combo, self.settings.cookies_browser)
        form.addRow("Browser", self.browser_combo)

        cookies_row = QHBoxLayout()
        self.cookies_file_edit = QLineEdit(self.settings.cookies_file)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_cookies)
        cookies_row.addWidget(self.cookies_file_edit, 1)
        cookies_row.addWidget(browse)
        cookies_widget = QWidget()
        cookies_widget.setLayout(cookies_row)
        form.addRow("Cookies file", cookies_widget)

        note = QLabel(
            "Browser cookies are the easiest fix for YouTube sign-in checks "
            "and protected X posts. Chrome may need to be closed while its "
            "cookie database is read."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        form.addRow("", note)

        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        self.retries_spin.setValue(self.settings.max_retries)
        form.addRow("Tries per song", self.retries_spin)
        self._hint(
            form,
            "How many times to retry a song that fails. 3 is a good default.",
        )

        self.sleep_min_spin = QDoubleSpinBox()
        self.sleep_min_spin.setRange(0.0, 60.0)
        self.sleep_min_spin.setSingleStep(0.5)
        self.sleep_min_spin.setValue(self.settings.sleep_min)
        form.addRow("Shortest pause between songs (seconds)", self.sleep_min_spin)

        self.sleep_max_spin = QDoubleSpinBox()
        self.sleep_max_spin.setRange(0.0, 120.0)
        self.sleep_max_spin.setSingleStep(0.5)
        self.sleep_max_spin.setValue(self.settings.sleep_max)
        form.addRow("Longest pause between songs (seconds)", self.sleep_max_spin)
        self._hint(
            form,
            "A small pause stops YouTube and X treating the app as a bot. "
            "Leave these as they are unless downloads keep failing.",
        )

        self._update_cookies()
        self.tabs.addTab(tab, "Download")

    def _build_output(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit(self.settings.output_dir)
        output_browse = QPushButton("Browse…")
        output_browse.clicked.connect(self._browse_output)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(output_browse)
        output_widget = QWidget()
        output_widget.setLayout(output_row)
        form.addRow("Output folder", output_widget)

        self.mp3_check = QCheckBox("MP3")
        self.mp3_check.setChecked("mp3" in self.settings.formats)
        self.m4a_check = QCheckBox("M4A")
        self.m4a_check.setChecked("m4a" in self.settings.formats)
        formats_row = QHBoxLayout()
        formats_row.addWidget(self.mp3_check)
        formats_row.addWidget(self.m4a_check)
        formats_row.addStretch(1)
        formats_widget = QWidget()
        formats_widget.setLayout(formats_row)
        form.addRow("Formats", formats_widget)

        self.mp3_bitrate = QComboBox()
        for value in ("128", "192", "256", "320"):
            self.mp3_bitrate.addItem(f"{value} kbps", value)
        self._select(self.mp3_bitrate, self.settings.mp3_bitrate)
        form.addRow("MP3 quality", self.mp3_bitrate)

        self.m4a_bitrate = QComboBox()
        for value in ("128", "192", "256", "320"):
            self.m4a_bitrate.addItem(f"{value} kbps", value)
        self._select(self.m4a_bitrate, self.settings.m4a_bitrate)
        form.addRow("M4A quality", self.m4a_bitrate)

        self.normalize_check = QCheckBox(
            "Make all songs the same volume"
        )
        self.normalize_check.setChecked(self.settings.normalize)
        form.addRow("Audio", self.normalize_check)
        self._hint(
            form,
            "Keeps quiet and loud songs at a similar level. Recommended.",
        )

        self.keep_raw_check = QCheckBox(
            "Keep the original download after converting"
        )
        self.keep_raw_check.setChecked(self.settings.keep_raw)
        form.addRow("", self.keep_raw_check)

        self.template_edit = QLineEdit(self.settings.filename_template)
        self.template_edit.textChanged.connect(self._update_preview)
        form.addRow("Filename template", self.template_edit)

        self.preview_label = QLabel("")
        self.preview_label.setObjectName("muted")
        form.addRow("Preview", self.preview_label)
        self._update_preview()

        note = QLabel(
            "Placeholders: {title}, {artist}, {album}, {track}, {id}, "
            "{source}. The default keeps the published title."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        form.addRow("", note)

        self.tabs.addTab(tab, "Output")

    def _build_spotify(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Spotify folder"))
        self.spotify_edit = QLineEdit(self.settings.spotify_dir)
        self.spotify_edit.setPlaceholderText(
            r"Paste a full path, e.g. C:\Users\you\MusicDLP\Spotify"
        )
        self.spotify_edit.setClearButtonEnabled(True)
        folder_row.addWidget(self.spotify_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_spotify)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        folder_hint = QLabel(
            "You can paste a path here instead of browsing. If the folder "
            "does not exist yet, it is created when you save."
        )
        folder_hint.setObjectName("mutedSmall")
        folder_hint.setWordWrap(True)
        layout.addWidget(folder_hint)

        form = QFormLayout()
        self.spotify_format = QComboBox()
        self.spotify_format.addItem("MP3 (recommended)", "mp3")
        self.spotify_format.addItem("M4A", "m4a")
        self._select(self.spotify_format, self.settings.spotify_format)
        form.addRow("Format sent to Spotify", self.spotify_format)

        self.copy_mode = QComboBox()
        self.copy_mode.addItem("Copy (keep a copy in the output folder)", "copy")
        self.copy_mode.addItem("Move (remove from the output folder)", "move")
        self._select(self.copy_mode, self.settings.spotify_copy_mode)
        form.addRow("When sending", self.copy_mode)

        self.restart_check = QCheckBox(
            "Restart Spotify after sending so it rescans local files"
        )
        self.restart_check.setChecked(self.settings.spotify_auto_restart)
        form.addRow("", self.restart_check)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(self._open_spotify_folder)
        open_spotify = QPushButton("Open Spotify")
        open_spotify.clicked.connect(lambda: self._spotify.open_spotify())
        restart = QPushButton("Restart Spotify now")
        restart.clicked.connect(self._restart_spotify)
        buttons.addWidget(open_folder)
        buttons.addWidget(open_spotify)
        buttons.addWidget(restart)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        steps = QLabel(SpotifyService.setup_steps(Path(self.settings.spotify_dir)))
        steps.setWordWrap(True)
        steps.setObjectName("muted")
        layout.addWidget(steps)
        layout.addStretch(1)

        self.tabs.addTab(tab, "Spotify")

    def _build_advanced(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        ffmpeg_row = QHBoxLayout()
        self.ffmpeg_edit = QLineEdit(self.settings.ffmpeg_path)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_ffmpeg)
        detect = QPushButton("Detect")
        detect.clicked.connect(self._detect_ffmpeg)
        ffmpeg_row.addWidget(self.ffmpeg_edit, 1)
        ffmpeg_row.addWidget(browse)
        ffmpeg_row.addWidget(detect)
        ffmpeg_widget = QWidget()
        ffmpeg_widget.setLayout(ffmpeg_row)
        form.addRow("FFmpeg path", ffmpeg_widget)

        node = shutil.which("node") or "not found"
        spotify_exe = self._spotify.find_exe()
        diagnostics = (
            f"yt-dlp: {self._ytdlp.version()}\n"
            f"FFmpeg: {self._ffmpeg.version()}\n"
            f"Node: {node}\n"
            f"Spotify: {spotify_exe or 'not found'}\n"
            f"Config: {config_path()}\n"
            f"Database: {db_path()}\n"
            f"Logs: {log_dir()}"
        )
        label = QLabel(diagnostics)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setObjectName("muted")
        form.addRow("Diagnostics", label)

        buttons = QHBoxLayout()
        copy_button = QPushButton("Copy diagnostics")
        copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(diagnostics)
        )
        open_logs = QPushButton("Open log folder")
        open_logs.clicked.connect(lambda: SpotifyService.open_folder(log_dir()))
        buttons.addWidget(copy_button)
        buttons.addWidget(open_logs)
        buttons.addStretch(1)
        form.addRow("", self._wrap(buttons))

        self.tabs.addTab(tab, "Advanced")

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _hint(form: QFormLayout, text: str) -> None:
        """Add a short explanation under the previous field."""
        label = QLabel(text)
        label.setObjectName("mutedSmall")
        label.setWordWrap(True)
        form.addRow("", label)

    @staticmethod
    def _wrap(layout) -> QWidget:
        widget = QWidget()
        widget.setLayout(layout)
        return widget

    def _update_cookies(self) -> None:
        mode = self.cookies_combo.currentData()
        self.browser_combo.setEnabled(mode == "browser")
        self.cookies_file_edit.setEnabled(mode == "file")

    def _update_preview(self) -> None:
        stem = render_template(
            self.template_edit.text(),
            title="Example Song",
            artist="Example Artist",
            album="Example Album",
            track=3,
            media_id="abc123",
            source="Example Playlist",
        )
        self.preview_label.setText(f"{stem}.mp3")

    def _browse_cookies(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select cookies.txt", "", "Text files (*.txt);;All files (*)"
        )
        if filename:
            self.cookies_file_edit.setText(filename)

    def _browse_output(self) -> None:
        folder = self._choose_folder("Choose output folder", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    def _browse_spotify(self) -> None:
        folder = self._choose_folder(
            "Choose Spotify local-files folder", self.spotify_edit.text()
        )
        if folder:
            self.spotify_edit.setText(folder)

    @staticmethod
    def _choose_folder(title: str, current: str) -> str:
        """Open a folder picker from a directory that actually exists.

        Passing a path that has been deleted makes the Windows picker open
        somewhere unexpected, which looks like the folder is missing.
        """
        start = start_directory(current)
        return QFileDialog.getExistingDirectory(
            None, title, str(start), QFileDialog.ShowDirsOnly
        )

    def _browse_ffmpeg(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select ffmpeg.exe", "", "Executables (*.exe);;All files (*)"
        )
        if filename:
            self.ffmpeg_edit.setText(filename)

    def _detect_ffmpeg(self) -> None:
        found = FFmpegService("").ffmpeg
        if found:
            self.ffmpeg_edit.setText(str(found))
        else:
            QMessageBox.warning(
                self, "FFmpeg not found",
                "Install FFmpeg with: winget install Gyan.FFmpeg\n"
                "or place ffmpeg.exe next to the app.",
            )

    def _open_spotify_folder(self) -> None:
        SpotifyService.open_folder(Path(self.spotify_edit.text()).expanduser())

    def _restart_spotify(self) -> None:
        ok, message = self._spotify.restart()
        (QMessageBox.information if ok else QMessageBox.warning)(
            self, "Spotify", message
        )

    # ------------------------------------------------------------------ save

    def _save(self) -> None:
        formats = []
        if self.mp3_check.isChecked():
            formats.append("mp3")
        if self.m4a_check.isChecked():
            formats.append("m4a")
        if not formats:
            QMessageBox.warning(
                self, "No output format",
                "Choose at least one output format (MP3 or M4A).",
            )
            return

        spotify_format = self.spotify_format.currentData()
        if spotify_format not in formats:
            formats.append(spotify_format)

        spotify_dir = self.spotify_edit.text().strip()
        if not spotify_dir:
            QMessageBox.warning(
                self, "No Spotify folder",
                "Choose the folder Spotify should read your songs from.",
            )
            return
        try:
            Path(spotify_dir).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(
                self, "Folder cannot be used",
                f"That folder cannot be created:\n{spotify_dir}\n\n{exc}",
            )
            return

        settings = Settings(
            theme=self.theme_combo.currentData(),
            mode=self.mode_combo.currentData(),
            output_dir=self.output_edit.text().strip(),
            spotify_dir=spotify_dir,
            spotify_format=spotify_format,
            spotify_copy_mode=self.copy_mode.currentData(),
            spotify_auto_restart=self.restart_check.isChecked(),
            formats=formats,
            mp3_bitrate=self.mp3_bitrate.currentData(),
            m4a_bitrate=self.m4a_bitrate.currentData(),
            normalize=self.normalize_check.isChecked(),
            keep_raw=self.keep_raw_check.isChecked(),
            filename_template=self.template_edit.text().strip() or "{title}",
            cookies_mode=self.cookies_combo.currentData(),
            cookies_browser=self.browser_combo.currentData(),
            cookies_file=self.cookies_file_edit.text().strip(),
            max_retries=self.retries_spin.value(),
            sleep_min=self.sleep_min_spin.value(),
            sleep_max=self.sleep_max_spin.value(),
            notifications=self.notifications_check.isChecked(),
            log_level=self.log_level_combo.currentData(),
            ffmpeg_path=self.ffmpeg_edit.text().strip(),
            window_geometry=self.settings.window_geometry,
            last_source_id=self.settings.last_source_id,
            first_run_done=True,
        )
        settings.validate()
        try:
            settings.save()
        except OSError as exc:
            QMessageBox.critical(self, "Could not save settings", str(exc))
            return

        self.result_settings = settings
        self.accept()
