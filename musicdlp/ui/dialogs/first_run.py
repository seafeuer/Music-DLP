"""First-run setup wizard."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget, QWizard,
    QWizardPage,
)

from musicdlp.core.config import Settings
from musicdlp.services.ffmpeg import FFmpegService
from musicdlp.services.spotify import SpotifyService


class FirstRunWizard(QWizard):
    def __init__(
        self,
        settings: Settings,
        ffmpeg: FFmpegService,
        spotify: SpotifyService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Music-DLP")
        self.resize(680, 520)
        self.settings = copy.deepcopy(settings)
        self.ffmpeg = ffmpeg
        self.spotify = spotify

        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.HaveCustomButton1, True)
        self.setButtonText(QWizard.CustomButton1, "Skip setup")
        self.customButtonClicked.connect(self._on_custom_button)

        self.addPage(self._welcome_page())
        self.addPage(self._ffmpeg_page())
        self.addPage(self._folders_page())
        self.addPage(self._cookies_page())

    # ----------------------------------------------------------------- pages

    def _welcome_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Download audio and put it in Spotify")
        layout = QVBoxLayout(page)
        text = QLabel(
            "Paste a YouTube playlist, a YouTube video, an X/Twitter post, "
            "or another link supported by yt-dlp. Music-DLP downloads the "
            "audio, converts it to MP3, tags it with cover art, and can copy "
            "it into your Spotify Local Files folder.\n\n"
            "Titles are kept as published. You can edit them per track."
        )
        text.setWordWrap(True)
        layout.addWidget(text)

        layout.addSpacing(12)
        layout.addWidget(QLabel("How would you like to work?"))
        self.easy_radio = QRadioButton(
            "Easy — one button downloads, converts, and sends to Spotify"
        )
        self.pro_radio = QRadioButton(
            "Pro — separate download, convert, and Spotify steps"
        )
        if self.settings.mode == "pro":
            self.pro_radio.setChecked(True)
        else:
            self.easy_radio.setChecked(True)
        layout.addWidget(self.easy_radio)
        layout.addWidget(self.pro_radio)
        layout.addStretch(1)
        return page

    def _ffmpeg_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("FFmpeg")
        layout = QVBoxLayout(page)

        self.ffmpeg_status = QLabel()
        self.ffmpeg_status.setWordWrap(True)
        layout.addWidget(self.ffmpeg_status)

        row = QHBoxLayout()
        locate = QPushButton("Locate ffmpeg.exe…")
        locate.clicked.connect(self._locate_ffmpeg)
        download = QPushButton("Open download page")
        download.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://ffmpeg.org/download.html"))
        )
        row.addWidget(locate)
        row.addWidget(download)
        row.addStretch(1)
        layout.addLayout(row)

        note = QLabel(
            "FFmpeg is needed to convert audio and embed tags. Downloads still "
            "work without it, but conversion will be disabled."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        layout.addStretch(1)
        self._update_ffmpeg_status()
        return page

    def _folders_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Folders")
        layout = QVBoxLayout(page)
        form = QFormLayout()

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit(self.settings.output_dir)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_output)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse)
        output_widget = QWidget()
        output_widget.setLayout(output_row)
        form.addRow("Output folder", output_widget)

        spotify_row = QHBoxLayout()
        self.spotify_edit = QLineEdit(self.settings.spotify_dir)
        spotify_browse = QPushButton("Browse…")
        spotify_browse.clicked.connect(self._browse_spotify)
        spotify_row.addWidget(self.spotify_edit, 1)
        spotify_row.addWidget(spotify_browse)
        spotify_widget = QWidget()
        spotify_widget.setLayout(spotify_row)
        form.addRow("Spotify folder", spotify_widget)
        layout.addLayout(form)

        steps = QLabel(SpotifyService.setup_steps(Path(self.settings.spotify_dir)))
        steps.setWordWrap(True)
        steps.setObjectName("muted")
        layout.addWidget(steps)
        layout.addStretch(1)
        return page

    def _cookies_page(self) -> QWizardPage:
        page = QWizardPage()
        page.setTitle("Cookies (optional)")
        layout = QVBoxLayout(page)
        form = QFormLayout()

        self.cookies_combo = QComboBox()
        self.cookies_combo.addItem("No cookies", "none")
        self.cookies_combo.addItem("Use cookies from a browser", "browser")
        self.cookies_combo.addItem("Use a cookies.txt file", "file")
        index = self.cookies_combo.findData(self.settings.cookies_mode)
        if index >= 0:
            self.cookies_combo.setCurrentIndex(index)
        form.addRow("Cookies", self.cookies_combo)

        self.browser_combo = QComboBox()
        for browser in ("chrome", "edge", "firefox", "brave", "opera",
                        "vivaldi", "chromium"):
            self.browser_combo.addItem(browser.title(), browser)
        index = self.browser_combo.findData(self.settings.cookies_browser)
        if index >= 0:
            self.browser_combo.setCurrentIndex(index)
        form.addRow("Browser", self.browser_combo)

        file_row = QHBoxLayout()
        self.cookies_file_edit = QLineEdit(self.settings.cookies_file)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_cookies)
        file_row.addWidget(self.cookies_file_edit, 1)
        file_row.addWidget(browse)
        file_widget = QWidget()
        file_widget.setLayout(file_row)
        form.addRow("cookies.txt", file_widget)
        layout.addLayout(form)

        note = QLabel(
            "Cookies fix YouTube sign-in checks and protected X/Twitter posts. "
            "You can change this later in Settings."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    # --------------------------------------------------------------- actions

    def _update_ffmpeg_status(self) -> None:
        if self.ffmpeg.available:
            self.ffmpeg_status.setText(f"Found: {self.ffmpeg.ffmpeg}")
        else:
            self.ffmpeg_status.setText(
                "FFmpeg was not found. Locate ffmpeg.exe, or install it with "
                "winget install Gyan.FFmpeg."
            )

    def _locate_ffmpeg(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select ffmpeg.exe", "", "Executables (*.exe);;All files (*)"
        )
        if filename:
            self.settings.ffmpeg_path = filename
            self.ffmpeg = FFmpegService(filename)
            self._update_ffmpeg_status()

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose output folder", self.output_edit.text()
        )
        if folder:
            self.output_edit.setText(folder)

    def _browse_spotify(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose Spotify folder", self.spotify_edit.text()
        )
        if folder:
            self.spotify_edit.setText(folder)

    def _browse_cookies(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Select cookies.txt", "", "Text files (*.txt);;All files (*)"
        )
        if filename:
            self.cookies_file_edit.setText(filename)

    def _on_custom_button(self, button: int) -> None:
        if button == QWizard.CustomButton1:
            self.settings.first_run_done = True
            self.settings.save()
            self.accept()

    def accept(self) -> None:
        self.settings.mode = "pro" if self.pro_radio.isChecked() else "easy"
        self.settings.output_dir = self.output_edit.text().strip()
        self.settings.spotify_dir = self.spotify_edit.text().strip()
        self.settings.cookies_mode = self.cookies_combo.currentData()
        self.settings.cookies_browser = self.browser_combo.currentData()
        self.settings.cookies_file = self.cookies_file_edit.text().strip()
        self.settings.first_run_done = True
        self.settings.validate()
        try:
            self.settings.save()
        except OSError:
            pass
        super().accept()
