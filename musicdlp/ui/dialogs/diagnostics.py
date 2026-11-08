"""A one-page answer to "why is this not working?"."""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QVBoxLayout,
)

from musicdlp.services import ai as ai_service
from musicdlp.services.ai import AISettings
from musicdlp.services.diagnostics import FAIL, WARN, Check, report, run_checks
from musicdlp.ui.dialogs.footer import DialogFooter
from musicdlp.ui.dialogs.sizing import fit_dialog


class _AISignals(QObject):
    done = Signal(bool, str)


class _AITask(QRunnable):
    def __init__(self, settings: AISettings, signals: _AISignals) -> None:
        super().__init__()
        self.settings = settings
        self.signals = signals

    def run(self) -> None:
        try:
            ok, message = ai_service.test_connection(self.settings)
        except Exception as exc:  # noqa: BLE001 - reported to the dialog
            ok, message = False, str(exc)
        try:
            self.signals.done.emit(ok, message)
        except RuntimeError:
            # The dialog was closed while the request was running.
            pass


class DiagnosticsDialog(QDialog):
    def __init__(
        self,
        settings,
        ffmpeg,
        ytdlp,
        spotify,
        bridge=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Diagnostics")
        fit_dialog(self, 760, 620)
        self.settings = settings
        self.checks: list[Check] = []
        self._signals = _AISignals(self)
        self._signals.done.connect(self._on_ai_result)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "What Music DLP can see right now. A ✓ means ready, ! means "
            "something worth checking, ✗ means the feature cannot work "
            "until it is fixed, and ? means the answer is unknown."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QTextEdit.NoWrap)
        self.text.setAccessibleName("Diagnostics report")
        layout.addWidget(self.text, 1)

        self.ai_status = QLabel("")
        self.ai_status.setWordWrap(True)
        self.ai_status.setObjectName("muted")
        layout.addWidget(self.ai_status)

        buttons = QVBoxLayout()
        row = QHBoxLayout()
        row.addStretch(1)
        self.copy_button = QPushButton("Copy report")
        self.copy_button.clicked.connect(self._copy)
        row.addWidget(self.copy_button)
        self.ai_button = QPushButton("Test AI connection")
        self.ai_button.setEnabled(bool(settings.ai_configured))
        self.ai_button.clicked.connect(self._test_ai)
        row.addWidget(self.ai_button)
        self.refresh_button = QPushButton("Check again")
        self.refresh_button.clicked.connect(self._refresh)
        row.addWidget(self.refresh_button)
        buttons.addLayout(row)
        layout.addLayout(buttons)

        self.footer = DialogFooter(self, cancel_text="Close")
        self.footer.hide_cancel()
        self.footer.set_primary("Done", self.accept)

        self._dependencies = (ffmpeg, ytdlp, spotify, bridge)
        self._refresh()

    # ------------------------------------------------------------- helpers

    def _refresh(self) -> None:
        ffmpeg, ytdlp, spotify, bridge = self._dependencies
        self.checks = run_checks(
            self.settings, ffmpeg, ytdlp, spotify, bridge
        )
        self.text.setPlainText(report(self.checks))
        failed = sum(1 for check in self.checks if check.status == FAIL)
        warned = sum(1 for check in self.checks if check.status == WARN)
        if failed:
            self.ai_status.setText(
                f"{failed} thing(s) need fixing before every feature works."
            )
        elif warned:
            self.ai_status.setText(
                f"{warned} thing(s) are optional or worth checking."
            )
        else:
            self.ai_status.setText("Everything checked is ready.")

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())
        self.ai_status.setText("Report copied to the clipboard.")

    def _test_ai(self) -> None:
        if not self.settings.ai_configured:
            self.ai_status.setText("Choose a model in Settings → AI first.")
            return
        self.ai_button.setEnabled(False)
        self.ai_status.setText("Testing the AI connection…")
        settings = AISettings(
            provider=self.settings.ai_provider,
            base_url=self.settings.ai_base_url,
            model=self.settings.ai_model,
            api_key=self.settings.ai_api_key,
            custom_prompt=self.settings.ai_custom_prompt,
        )
        QThreadPool.globalInstance().start(
            _AITask(settings, self._signals)
        )

    def _on_ai_result(self, ok: bool, message: str) -> None:
        self.ai_button.setEnabled(True)
        self.ai_status.setText(("AI: " if ok else "AI failed: ") + message)


