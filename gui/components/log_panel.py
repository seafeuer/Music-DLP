"""
Log Panel Component

Bottom panel showing real-time logs with:
- Color-coded log levels
- Auto-scroll to bottom
- Clear logs button
- Integration with core.logger

Author: Music DLP Project
Version: 1.0.0
"""

import customtkinter as ctk
import tkinter as tk
from datetime import datetime
import queue


class LogPanel(ctk.CTkFrame):
    """
    Log viewer panel (bottom of main window).

    Displays real-time logs from the application.
    """

    def __init__(self, parent, db, config, logger):
        """
        Initialize log panel.

        Args:
            parent: Parent widget
            db: Database instance
            config: ConfigManager instance
            logger: Logger instance
        """
        super().__init__(parent, height=200)

        self.db = db
        self.config = config
        self.logger = logger

        self.max_logs = 1000  # Maximum number of log entries to keep

        # Worker threads enqueue log lines; the main thread drains them.
        self._log_queue = queue.Queue()

        self._create_widgets()

        # Poll the queue on the Tk main thread
        self.after(100, self._drain_queue)

    def _create_widgets(self):
        """Create log panel widgets."""
        # Header frame
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=5, pady=5)

        # Title
        title_label = ctk.CTkLabel(
            header_frame,
            text="Logs",
            font=("Arial", 14, "bold")
        )
        title_label.pack(side="left", padx=10)

        # Clear button
        clear_button = ctk.CTkButton(
            header_frame,
            text="Clear Logs",
            width=100,
            command=self._clear_logs
        )
        clear_button.pack(side="right", padx=10)

        # Log text widget (using tkinter Text for color support)
        text_frame = ctk.CTkFrame(self)
        text_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        # Scrollbar
        scrollbar = ctk.CTkScrollbar(text_frame)
        scrollbar.pack(side="right", fill="y")

        # Text widget
        self.log_text = tk.Text(
            text_frame,
            wrap="word",
            yscrollcommand=scrollbar.set,
            bg="#2b2b2b" if self.config.theme_mode == "dark" else "white",
            fg="white" if self.config.theme_mode == "dark" else "black",
            font=("Courier", 9),
            state="disabled",
            height=10
        )
        self.log_text.pack(side="left", fill="both", expand=True)

        scrollbar.configure(command=self.log_text.yview)

        # Configure text tags for colors
        self._configure_tags()

    def _configure_tags(self):
        """Configure text tags for different log levels."""
        self.log_text.tag_config("DEBUG", foreground="gray")
        self.log_text.tag_config("INFO", foreground="lightgreen")
        self.log_text.tag_config("SUCCESS", foreground="green")
        self.log_text.tag_config("WARNING", foreground="yellow")
        self.log_text.tag_config("ERROR", foreground="red")
        self.log_text.tag_config("CRITICAL", foreground="red", font=("Courier", 9, "bold"))

    def add_log_message(self, level: str, message: str):
        """
        Add a log message to the display.

        Safe to call from any thread: messages are queued and rendered on
        the Tk main thread by _drain_queue.

        Args:
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            message: Log message text
        """
        self._log_queue.put((level, message))

    def _drain_queue(self):
        """Render queued log messages, then reschedule."""
        rendered = 0
        while rendered < 200:
            try:
                level, message = self._log_queue.get_nowait()
            except queue.Empty:
                break

            self._append_line(level, message)
            rendered += 1

        try:
            self.after(100, self._drain_queue)
        except Exception:
            pass  # Widget destroyed

    def _append_line(self, level: str, message: str):
        """Append one line to the text widget (main thread only)."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_line = f"[{level}] {timestamp} - {message}\n"

        # Enable editing temporarily
        self.log_text.configure(state="normal")

        # Insert log with color tag
        self.log_text.insert("end", log_line, level.upper())

        # Trim logs if exceeds max
        line_count = int(self.log_text.index('end-1c').split('.')[0])
        if line_count > self.max_logs:
            self.log_text.delete("1.0", f"{line_count - self.max_logs}.0")

        # Disable editing
        self.log_text.configure(state="disabled")

        # Auto-scroll to bottom
        self.log_text.see("end")

    def _clear_logs(self):
        """Clear all log messages."""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        self.logger.info("Logs cleared")
