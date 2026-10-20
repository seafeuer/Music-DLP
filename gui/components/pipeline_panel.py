"""
Pipeline Panel Component

Pipeline control panel with:
- Stage buttons (Download, Convert)
- Progress bar
- Status text display
- Current operation display

Links are extracted automatically when they are added, so the pipeline
starts at Download.

Author: Music DLP Project
Version: 2.0.0
"""

import customtkinter as ctk
from typing import Callable, Optional


class PipelinePanel(ctk.CTkFrame):
    """
    Pipeline control panel (middle of main window).

    Shows pipeline stages and progress.
    """

    def __init__(self, parent, db, config, logger,
                 on_download: Optional[Callable] = None,
                 on_convert: Optional[Callable] = None):
        """
        Initialize pipeline panel.

        Args:
            parent: Parent widget
            db: Database instance
            config: ConfigManager instance
            logger: Logger instance
            on_download: Callback for Download button
            on_convert: Callback for Convert button
        """
        super().__init__(parent, height=150)

        self.db = db
        self.config = config
        self.logger = logger

        self.on_download = on_download
        self.on_convert = on_convert

        # Operation locking state
        self._active_stage: Optional[str] = None

        self._create_widgets()

    def _create_widgets(self):
        """Create pipeline widgets."""
        # Title
        title_label = ctk.CTkLabel(
            self,
            text="Pipeline",
            font=("Arial", 16, "bold")
        )
        title_label.pack(pady=(10, 5))

        # Button frame
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(fill="x", padx=20, pady=5)

        # Pipeline stage buttons
        self.download_button = ctk.CTkButton(
            button_frame,
            text="1. Download",
            width=140,
            command=self._on_download_click
        )
        self.download_button.pack(side="left", padx=5)

        self.convert_button = ctk.CTkButton(
            button_frame,
            text="2. Convert",
            width=140,
            command=self._on_convert_click
        )
        self.convert_button.pack(side="left", padx=5)

        # Hint
        hint_label = ctk.CTkLabel(
            button_frame,
            text="Paste a link above to add it to the library.",
            text_color="gray"
        )
        hint_label.pack(side="left", padx=15)

        # Progress frame
        progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        progress_frame.pack(fill="x", padx=20, pady=10)

        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(progress_frame, width=600)
        self.progress_bar.pack(fill="x", pady=5)
        self.progress_bar.set(0)

        # Status label
        self.status_label = ctk.CTkLabel(
            progress_frame,
            text="Ready",
            text_color="gray"
        )
        self.status_label.pack(pady=5)

    def _on_download_click(self):
        """Handle Download button click."""
        if self.on_download:
            self.on_download()

    def _on_convert_click(self):
        """Handle Convert button click."""
        if self.on_convert:
            self.on_convert()

    def update_progress(self, current: int, total: int, message: str = ""):
        """
        Update progress bar and status text.

        Args:
            current: Current item number
            total: Total items
            message: Status message
        """
        if total > 0:
            progress = current / total
            self.progress_bar.set(progress)

            status_text = f"{message} ({current}/{total})"
        else:
            self.progress_bar.set(0)
            status_text = message

        self.status_label.configure(text=status_text)

    def set_status(self, message: str, progress: float = None):
        """
        Set status message and optional progress.

        Args:
            message: Status message
            progress: Progress value (0.0-1.0) or None
        """
        self.status_label.configure(text=message)

        if progress is not None:
            self.progress_bar.set(progress)

    def reset(self):
        """Reset progress to initial state."""
        self.progress_bar.set(0)
        self.status_label.configure(text="Ready")

    def set_buttons_enabled(self, enabled: bool):
        """
        Enable or disable all pipeline buttons.

        Args:
            enabled: True to enable, False to disable
        """
        state = "normal" if enabled else "disabled"

        self.download_button.configure(state=state)
        self.convert_button.configure(state=state)

    def set_busy(self, stage_name: str):
        """
        Mark a pipeline stage as running. Disables all buttons.

        Args:
            stage_name: Name of the active stage (e.g., "Download")
        """
        self._active_stage = stage_name
        self.set_buttons_enabled(False)
        self.status_label.configure(text=f"Running: {stage_name}...", text_color=("blue", "#4da6ff"))

    def set_idle(self):
        """Mark pipeline as idle. Re-enables all buttons."""
        self._active_stage = None
        self.set_buttons_enabled(True)
        self.status_label.configure(text_color="gray")

    @property
    def is_busy(self) -> bool:
        """Check if a pipeline stage is currently running."""
        return self._active_stage is not None

    @property
    def active_stage(self) -> Optional[str]:
        """Get the name of the currently running stage, or None."""
        return self._active_stage
