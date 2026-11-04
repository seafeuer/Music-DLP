"""Spotify Local Files setup and status dialog."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout,
)

from musicdlp.ui.dialogs.footer import DialogFooter
from musicdlp.ui.dialogs.sizing import fit_dialog

from musicdlp.core.paths import is_writable, start_directory
from musicdlp.services.spotify import SpotifyService


class SpotifyDialog(QDialog):
    def __init__(
        self,
        spotify_dir: str,
        spotify: SpotifyService,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Spotify folder")
        fit_dialog(self, 640, 480)
        self.spotify = spotify
        self.selected_dir: Optional[str] = None

        layout = QVBoxLayout(self)

        exe = spotify.find_exe()
        running = spotify.is_running()
        status = QLabel(
            f"Spotify: {'found' if exe else 'not found'}"
            f"{' · running' if running else ''}"
            + (f"\n{exe}" if exe else "")
        )
        status.setWordWrap(True)
        layout.addWidget(status)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder"))
        self.folder_edit = QLineEdit(spotify_dir)
        folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        actions = QHBoxLayout()
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(self._open_folder)
        open_spotify = QPushButton("Open Spotify")
        open_spotify.clicked.connect(lambda: self.spotify.open_spotify())
        restart = QPushButton("Restart Spotify")
        restart.clicked.connect(self._restart)
        test = QPushButton("Test folder")
        test.clicked.connect(self._test_folder)
        actions.addWidget(open_folder)
        actions.addWidget(open_spotify)
        actions.addWidget(restart)
        actions.addWidget(test)
        actions.addStretch(1)
        layout.addLayout(actions)

        steps = QLabel(SpotifyService.setup_steps(Path(spotify_dir)))
        steps.setWordWrap(True)
        steps.setTextInteractionFlags(Qt.TextSelectableByMouse)
        steps.setObjectName("muted")
        layout.addWidget(steps, 1)

        note = QLabel(
            "Music DLP writes tagged files into this folder and can restart "
            "Spotify so it reads them. Spotify does not let other apps add "
            "songs to a playlist, so drag them into one inside Spotify."
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)

        self.footer = DialogFooter(self)
        self.footer.set_primary("Save", self._save)
        layout.addWidget(self.footer)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose Spotify local-files folder",
            str(start_directory(self.folder_edit.text())),
            QFileDialog.ShowDirsOnly,
        )
        if folder:
            self.folder_edit.setText(folder)

    def _open_folder(self) -> None:
        SpotifyService.open_folder(Path(self.folder_edit.text()).expanduser())

    def _restart(self) -> None:
        ok, message = self.spotify.restart()
        (QMessageBox.information if ok else QMessageBox.warning)(
            self, "Spotify", message
        )

    def _test_folder(self) -> None:
        path = Path(self.folder_edit.text()).expanduser()
        if is_writable(path):
            QMessageBox.information(
                self, "Folder is writable", f"Music-DLP can write to:\n{path}"
            )
        else:
            QMessageBox.warning(
                self, "Folder is not writable",
                f"Choose a folder Music-DLP can write to:\n{path}",
            )

    def _save(self) -> None:
        path = self.folder_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "No folder", "Choose a Spotify folder.")
            return
        self.selected_dir = path
        self.accept()
