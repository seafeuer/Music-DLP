"""Settings dialog."""

from __future__ import annotations

import copy
import shutil
import threading
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from musicdlp.core.config import Settings
from musicdlp.core.paths import config_path, db_path, log_dir, start_directory
from musicdlp.services.ai import (
    AIError, AISettings, friendly_error, list_models, test_connection,
)
from musicdlp.services.filenames import render_template
from musicdlp.services.ffmpeg import FFmpegService
from musicdlp.services import gdrive
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
        start_tab: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(760, 660)
        self.result_settings: Optional[Settings] = None
        self._ffmpeg = ffmpeg
        self._ytdlp = ytdlp
        self._spotify = spotify
        self.settings = copy.deepcopy(settings)
        self._start_tab = start_tab

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self._build_general()
        self._build_download()
        self._build_output()
        self._build_ai()
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
            "Delete the original download after converting"
        )
        self.keep_raw_check.setToolTip(
            "Off by default. Keeping the download means a song can be "
            "converted again later without downloading it a second time."
        )
        # The stored setting is "keep", the box asks the opposite question.
        self.keep_raw_check.setChecked(not self.settings.keep_raw)
        form.addRow("", self.keep_raw_check)

        self.smart_titles_check = QCheckBox(
            "Work out song and artist names from the upload title"
        )
        self.smart_titles_check.setChecked(self.settings.smart_titles)
        form.addRow("Smart titles", self.smart_titles_check)
        self._hint(
            form,
            "Turns “DRAKE - AT THE GATES FT LIL UZI VERT (CDQ)” into the "
            "song “At The Gates” by “Drake, Lil Uzi Vert”. Only used when "
            "the app is confident; anything unsure is offered for review.",
        )

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

        # ---- Google Drive, for listening on a phone ----------------------
        drive_box = QVBoxLayout()
        drive_title = QLabel("Google Drive (for your phone)")
        drive_title.setObjectName("sectionTitle")
        drive_box.addWidget(drive_title)

        drive_row = QHBoxLayout()
        self.gdrive_edit = QLineEdit(self.settings.gdrive_dir)
        self.gdrive_edit.setPlaceholderText(
            "Leave empty to use the Google Drive folder on this computer"
        )
        self.gdrive_edit.setClearButtonEnabled(True)
        drive_row.addWidget(self.gdrive_edit, 1)
        drive_browse = QPushButton("Browse…")
        drive_browse.clicked.connect(self._browse_gdrive)
        drive_row.addWidget(drive_browse)
        drive_find = QPushButton("Find it")
        drive_find.clicked.connect(self._find_gdrive)
        drive_row.addWidget(drive_find)
        drive_box.addLayout(drive_row)

        self.gdrive_status = QLabel("")
        self.gdrive_status.setWordWrap(True)
        self.gdrive_status.setObjectName("mutedSmall")
        drive_box.addWidget(self.gdrive_status)
        self.gdrive_edit.textChanged.connect(self._update_gdrive_status)

        drive_hint = QLabel(
            "Songs sent here are uploaded by the Google Drive app and appear "
            "in the Drive app on your phone. Nothing is shared or made public."
        )
        drive_hint.setObjectName("mutedSmall")
        drive_hint.setWordWrap(True)
        drive_box.addWidget(drive_hint)
        layout.addLayout(drive_box)
        self._update_gdrive_status()

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

    def _build_ai(self) -> None:
        tab = QWidget()
        form = QFormLayout(tab)

        intro = QLabel(
            "AI is optional. It is used only when you press “Ask AI” to name "
            "songs the app could not work out by itself. Any address and key "
            "will do: the first time a server is used, Music DLP works out "
            "which of the common APIs it speaks."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        form.addRow(intro)

        self.ai_provider_combo = QComboBox()
        self.ai_provider_combo.addItem(
            "Detect automatically (any address)", ("auto", "")
        )
        self.ai_provider_combo.addItem(
            "Ollama on this computer", ("ollama", "http://localhost:11434")
        )
        self.ai_provider_combo.addItem(
            "Ollama cloud (needs an API key)", ("ollama", "https://ollama.com")
        )
        self.ai_provider_combo.addItem(
            "Another compatible API (needs a key)", ("openai", "")
        )
        self.ai_provider_combo.addItem(
            "Anthropic-style API (needs a key)", ("anthropic", "")
        )
        index = self._ai_provider_index()
        self.ai_provider_combo.setCurrentIndex(index)
        self.ai_provider_combo.currentIndexChanged.connect(
            self._on_ai_provider_changed
        )
        form.addRow("Provider", self.ai_provider_combo)

        self.ai_url_edit = QLineEdit(self.settings.ai_base_url)
        self.ai_url_edit.setPlaceholderText("http://localhost:11434")
        form.addRow("Server address", self.ai_url_edit)

        model_row = QHBoxLayout()
        self.ai_model_combo = QComboBox()
        self.ai_model_combo.setEditable(True)
        self.ai_model_combo.setMinimumWidth(240)
        if self.settings.ai_model:
            self.ai_model_combo.addItem(self.settings.ai_model)
            self.ai_model_combo.setCurrentText(self.settings.ai_model)
        self.ai_refresh_button = QPushButton("Refresh list")
        self.ai_refresh_button.clicked.connect(self._refresh_ai_models)
        model_row.addWidget(self.ai_model_combo, 1)
        model_row.addWidget(self.ai_refresh_button)
        form.addRow("Model", self._wrap(model_row))

        self.ai_key_edit = QLineEdit(self.settings.ai_api_key)
        self.ai_key_edit.setEchoMode(QLineEdit.Password)
        self.ai_key_edit.setPlaceholderText(
            "Not needed for Ollama on this computer"
        )
        show_key = QToolButton()
        show_key.setText("Show")
        show_key.setCheckable(True)

        def toggle_key(shown: bool) -> None:
            self.ai_key_edit.setEchoMode(
                QLineEdit.Normal if shown else QLineEdit.Password
            )
            show_key.setText("Hide" if shown else "Show")

        show_key.toggled.connect(toggle_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.ai_key_edit, 1)
        key_row.addWidget(show_key)
        form.addRow("API key", self._wrap(key_row))

        self.ai_test_button = QPushButton("Test connection")
        self.ai_test_button.clicked.connect(self._test_ai)
        form.addRow("", self.ai_test_button)

        self.ai_status = QLabel("")
        self.ai_status.setWordWrap(True)
        form.addRow("", self.ai_status)

        self.ai_prompt_edit = QPlainTextEdit(self.settings.ai_custom_prompt)
        self.ai_prompt_edit.setPlaceholderText(
            "Optional. Extra instructions for the AI, in your own words.\n\n"
            "For example: \"Always put the Vocaloid singer in the artist "
            "field. Keep (Original) only for Vocaloid songs.\""
        )
        self.ai_prompt_edit.setMinimumHeight(90)
        self.ai_prompt_edit.setToolTip(
            "Added to the built-in instructions, so the answer keeps the "
            "shape Music DLP needs"
        )
        form.addRow("Extra AI instructions", self.ai_prompt_edit)
        self._hint(
            form,
            "These are added to the built-in prompt for naming and "
            "translating. Leave it empty to use the built-in prompt alone.",
        )

        self.ai_artwork_check = QCheckBox(
            "Look up real album art instead of the video still"
        )
        self.ai_artwork_check.setChecked(self.settings.ai_find_artwork)
        form.addRow("Cover art", self.ai_artwork_check)
        self._hint(
            form,
            "Runs automatically when a song is converted. Uses free catalogue "
            "lookups (iTunes and MusicBrainz), not AI, and needs no account. "
            "The video picture is used when nothing is found.",
        )

        self.tabs.addTab(tab, "AI")

        if self._start_tab:
            index = self._tab_index(self._start_tab)
            if index >= 0:
                self.tabs.setCurrentIndex(index)

    def _tab_index(self, name: str) -> int:
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index).lower() == name.lower():
                return index
        return -1

    def _ai_provider_index(self) -> int:
        provider = self.settings.ai_provider
        url = self.settings.ai_base_url.rstrip("/")
        if provider == "auto":
            return 0
        if provider == "openai":
            return 3
        if provider == "anthropic":
            return 4
        if url == "https://ollama.com":
            return 2
        return 1

    def _on_ai_provider_changed(self, index: int) -> None:
        provider, url = self.ai_provider_combo.itemData(index)
        if provider == "auto":
            # Automatic mode works with whatever is already typed, so the
            # address is left alone rather than being replaced.
            self.ai_url_edit.setPlaceholderText(
                "http://localhost:11434, https://api.example.com/v1, "
                "or any server address"
            )
            self.ai_key_edit.setPlaceholderText(
                "Leave empty for a server on this computer"
            )
            self.ai_status.setText("")
            return

        # Always set the address for the chosen provider: leaving the old one
        # in place made "another compatible API" silently point at Ollama.
        self.ai_url_edit.setText(url or "")
        if not url:
            # Say what kind of address belongs here, rather than leaving the
            # Ollama example in place.
            self.ai_url_edit.setPlaceholderText(
                "https://api.anthropic.com" if provider == "anthropic"
                else "https://your-server.example/v1"
            )
        else:
            self.ai_url_edit.setPlaceholderText(url)
        if provider == "ollama" and url == "http://localhost:11434":
            self.ai_key_edit.setPlaceholderText(
                "Not needed for Ollama on this computer"
            )
        elif provider == "ollama":
            self.ai_key_edit.setPlaceholderText(
                "Your ollama.com API key"
            )
        else:
            self.ai_key_edit.setPlaceholderText(
                "The API key for that server"
            )
        self.ai_model_combo.clear()
        self.ai_status.setText("")

    def _ai_settings(self) -> AISettings:
        provider, _default_url = self.ai_provider_combo.currentData()
        return AISettings(
            provider=provider,
            base_url=self.ai_url_edit.text().strip(),
            model=self.ai_model_combo.currentText().strip(),
            api_key=self.ai_key_edit.text().strip(),
        )

    def done(self, result: int) -> None:
        """Stop watching for a slow check once the dialog is closing."""
        timer = getattr(self, "_task_timer", None)
        if timer is not None:
            timer.stop()
        super().done(result)

    def _run_task(
        self,
        work: Callable[[], object],
        on_done: Callable[[object, Optional[BaseException]], None],
        *,
        status_label: QLabel,
        busy_text: str,
        disable: tuple = (),
    ) -> None:
        """Run slow work off the UI thread and report back on it.

        Talking to a model server, or to pip, can take many seconds, and a
        frozen dialog looks broken. The result is handed back through a short
        timer rather than from the worker, so widgets are only ever touched
        here.
        """
        if getattr(self, "_task_busy", False):
            status_label.setText(
                "Please wait for the check that is already running."
            )
            return
        self._task_busy = True
        self._task_result: list = []
        status_label.setStyleSheet("")
        status_label.setText(busy_text)
        widgets = tuple(disable)
        for widget in widgets:
            widget.setEnabled(False)

        def runner() -> None:
            try:
                self._task_result.append((work(), None))
            except BaseException as exc:  # noqa: BLE001 - shown to the user
                self._task_result.append((None, exc))

        threading.Thread(target=runner, daemon=True).start()

        timer = QTimer(self)
        timer.setInterval(120)
        self._task_timer = timer

        def check() -> None:
            if not self._task_result:
                return
            timer.stop()
            timer.deleteLater()
            self._task_busy = False
            for widget in widgets:
                widget.setEnabled(True)
            value, error = self._task_result[0]
            on_done(value, error)

        timer.timeout.connect(check)
        timer.start()

    def _run_ai_task(
        self,
        work: Callable[[], object],
        on_done: Callable[[object, Optional[BaseException]], None],
        busy_text: str,
    ) -> None:
        self._run_task(
            work,
            on_done,
            status_label=self.ai_status,
            busy_text=busy_text,
            disable=(self.ai_test_button, self.ai_refresh_button),
        )

    def _refresh_ai_models(self) -> None:
        settings = self._ai_settings()
        if not settings.base_url:
            self.ai_status.setText("Enter the server address first.")
            return

        def done(models, error) -> None:
            if error is not None:
                if isinstance(error, AIError):
                    message, hint = friendly_error(error)
                else:
                    message, hint = "The server could not be reached.", str(error)
                self.ai_status.setText(
                    f"{message}\n{hint}" if hint else message
                )
                self.ai_status.setStyleSheet("color: #d97706;")
                return
            if not models:
                self.ai_status.setText(
                    "The server has no models yet. Install one, then refresh."
                )
                return
            current = self.ai_model_combo.currentText()
            self.ai_model_combo.clear()
            self.ai_model_combo.addItems(list(models))
            if current:
                self.ai_model_combo.setCurrentText(current)
            self.ai_status.setText(f"Found {len(models)} model(s).")

        self._run_ai_task(
            lambda: list_models(settings),
            done,
            "Asking the server for its models…",
        )

    def _test_ai(self) -> None:
        settings = self._ai_settings()

        def done(result, error) -> None:
            if error is not None:
                self.ai_status.setText(f"Could not test: {error}")
                self.ai_status.setStyleSheet("color: #d97706;")
                return
            ok, message = result
            self.ai_status.setText(message)
            self.ai_status.setStyleSheet(
                "color: #16a34a;" if ok else "color: #d97706;"
            )

        self._run_ai_task(lambda: test_connection(settings), done, "Testing…")

    @staticmethod
    def _ai_error_text(exc: AIError) -> str:
        if len(exc.args) > 1 and exc.args[1]:
            return f"{exc.args[0]}\n{exc.args[1]}"
        return str(exc)

    def _update_ytdlp(self) -> None:
        """Install the newest yt-dlp, which fixes most site breakages."""
        import sys

        if getattr(sys, "frozen", False):
            # A packaged build has yt-dlp inside it. Running pip from there
            # would relaunch this app instead of installing anything.
            self.ytdlp_status.setText(
                "This is the packaged app, so yt-dlp is built in. Install "
                "a newer Music DLP to get the newest yt-dlp."
            )
            return

        reply = QMessageBox.question(
            self,
            "Update yt-dlp",
            "Download and install the newest yt-dlp?\n\n"
            "This uses pip, needs an internet connection, and takes a "
            "minute. A restart of Music DLP is needed afterwards.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        def work():
            import subprocess
            import sys

            flags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                flags = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade",
                 "--quiet", "yt-dlp"],
                capture_output=True,
                text=True,
                timeout=300,
                creationflags=flags,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    (result.stderr or result.stdout or "pip failed").strip()[-400:]
                )
            # The running process still has the old module loaded, so ask a
            # fresh interpreter what was installed.
            probe = subprocess.run(
                [sys.executable, "-c",
                 "import yt_dlp; print(yt_dlp.version.__version__)"],
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=flags,
            )
            return (probe.stdout or "").strip() or "unknown"

        def done(version, error) -> None:
            if error is not None:
                self.ytdlp_status.setText(f"Update failed: {error}")
                return
            self.ytdlp_status.setText(
                f"yt-dlp {version} is installed. Restart Music DLP to use it."
            )
            self.ytdlp_button.setText(f"Update yt-dlp (currently {version})")

        self._run_task(
            work,
            done,
            status_label=self.ytdlp_status,
            busy_text="Updating yt-dlp…",
            disable=(self.ytdlp_button,),
        )

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

        self.ytdlp_button = QPushButton(
            f"Update yt-dlp (currently {self._ytdlp.version()})"
        )
        self.ytdlp_button.setToolTip(
            "Sites change often, and a site that stopped working is usually "
            "fixed by updating yt-dlp"
        )
        self.ytdlp_button.clicked.connect(self._update_ytdlp)
        self.ytdlp_status = QLabel("")
        self.ytdlp_status.setWordWrap(True)
        self.ytdlp_status.setObjectName("muted")
        form.addRow("", self.ytdlp_button)
        form.addRow("", self.ytdlp_status)

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

    def _browse_gdrive(self) -> None:
        start = self.gdrive_edit.text().strip() or str(
            gdrive.find_drive() or Path.home()
        )
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose a Google Drive folder", start
        )
        if chosen:
            self.gdrive_edit.setText(chosen)
            self._update_gdrive_status()

    def _find_gdrive(self) -> None:
        found = gdrive.find_drive()
        if found is None:
            self.gdrive_status.setText(
                "No Google Drive folder found. Install Google Drive for "
                "desktop and sign in, then press Find it again."
            )
            return
        # Show where songs would go, but leave the box empty: an empty box
        # means "find it automatically", which keeps working if Drive moves
        # to another drive letter. Typing a path would pin it forever.
        target = found / gdrive.SUBFOLDER
        self.gdrive_edit.clear()
        self.gdrive_status.setText(
            f"Found it: songs will be copied to {target}"
        )

    def _update_gdrive_status(self) -> None:
        configured = self.gdrive_edit.text().strip()
        path = gdrive.destination(configured)
        if path is None:
            self.gdrive_status.setText(
                "No Google Drive folder found on this computer."
            )
            return
        if configured:
            self.gdrive_status.setText(f"Songs will be copied to {path}")
        else:
            self.gdrive_status.setText(
                f"Found automatically: songs will be copied to {path}"
            )

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
            keep_raw=not self.keep_raw_check.isChecked(),
            smart_titles=self.smart_titles_check.isChecked(),
            filename_template=self.template_edit.text().strip() or "{title}",
            cookies_mode=self.cookies_combo.currentData(),
            cookies_browser=self.browser_combo.currentData(),
            cookies_file=self.cookies_file_edit.text().strip(),
            max_retries=self.retries_spin.value(),
            sleep_min=self.sleep_min_spin.value(),
            sleep_max=self.sleep_max_spin.value(),
            notifications=self.notifications_check.isChecked(),
            ai_provider=self.ai_provider_combo.currentData()[0],
            ai_base_url=self.ai_url_edit.text().strip(),
            ai_model=self.ai_model_combo.currentText().strip(),
            ai_api_key=self.ai_key_edit.text().strip(),
            ai_find_artwork=self.ai_artwork_check.isChecked(),
            ai_custom_prompt=self.ai_prompt_edit.toPlainText().strip(),
            gdrive_dir=self.gdrive_edit.text().strip(),
            # The Translate dialog remembers these; this dialog does not
            # show them, so carry them over rather than resetting them.
            translate_language=self.settings.translate_language,
            translate_style=self.settings.translate_style,
            translate_artist=self.settings.translate_artist,
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

        # A half-filled AI setup would leave "Ask AI" greyed out with no
        # explanation, so say so now.
        if settings.ai_model or settings.ai_base_url:
            if not settings.ai_configured:
                QMessageBox.warning(
                    self,
                    "AI is not finished",
                    "Songs will still be named by the built-in rules, but AI "
                    "naming is not ready.\n\n"
                    "Set both a server address and a model on the AI tab.",
                )
            elif not settings.ai_base_url.startswith(("http://", "https://")):
                QMessageBox.warning(
                    self,
                    "AI address looks wrong",
                    "The server address should start with http:// or "
                    "https://.\n\nAI naming will not work until it does.",
                )

        self.result_settings = settings
        self.accept()
