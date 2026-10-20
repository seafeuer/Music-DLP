"""
Main Window for Music DLP Application

CustomTkinter-based main window with:
- Library panel (left)
- Song panel (right)
- Pipeline panel (middle)
- Log panel (bottom)
- Menu bar with settings and theme switching

Author: Music DLP Project
Version: 2.0.0
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

from gui.components.playlist_panel import PlaylistPanel
from gui.components.song_panel import SongPanel
from gui.components.pipeline_panel import PipelinePanel
from gui.components.log_panel import LogPanel
from gui.dialogs.settings_dialog import SettingsDialog
from downloaders.url_utils import split_links


class MainWindow(ctk.CTk):
    """
    Main application window.

    Manages layout, theme, and coordinates all UI components.
    """

    def __init__(self, db, config, logger):
        """
        Initialize main window.

        Args:
            db: Database instance
            config: ConfigManager instance
            logger: Logger instance
        """
        super().__init__()

        self.db = db
        self.config = config
        self.logger = logger

        # Window configuration
        self.title("Music DLP - Media Downloader")
        self.geometry("1200x800")
        self.minsize(1000, 600)

        # Set theme from config
        theme_mode = config.theme_mode  # 'light', 'dark', or 'system'
        ctk.set_appearance_mode(theme_mode)
        ctk.set_default_color_theme("blue")

        # Create UI
        self._create_menu()
        self._create_layout()
        self._setup_logging()

        # Bind close event
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

        self.logger.info("Main window initialized")

    def _create_menu(self):
        """Create menu bar at top of window."""
        menu_frame = ctk.CTkFrame(self, height=50)
        menu_frame.grid(row=0, column=0, columnspan=3, sticky="ew", padx=5, pady=5)
        menu_frame.grid_columnconfigure(1, weight=1)  # Center spacer

        # Left side: Title
        title_label = ctk.CTkLabel(
            menu_frame,
            text="Music DLP",
            font=("Arial", 20, "bold")
        )
        title_label.grid(row=0, column=0, padx=20, pady=10, sticky="w")

        # Right side: Theme toggle and Settings button
        right_frame = ctk.CTkFrame(menu_frame, fg_color="transparent")
        right_frame.grid(row=0, column=2, padx=10, pady=10, sticky="e")

        self.theme_button = ctk.CTkButton(
            right_frame,
            text="☀️ Light" if self.config.theme_mode == "dark" else "🌙 Dark",
            width=100,
            command=self._toggle_theme
        )
        self.theme_button.grid(row=0, column=0, padx=5)

        settings_button = ctk.CTkButton(
            right_frame,
            text="⚙️ Settings",
            width=100,
            command=self._open_settings
        )
        settings_button.grid(row=0, column=1, padx=5)

    def _create_layout(self):
        """Create main window layout with all panels."""
        # Configure grid weights for responsive layout
        self.grid_rowconfigure(1, weight=2)  # Library/Song panels
        self.grid_rowconfigure(2, weight=0)  # Pipeline panel (fixed height)
        self.grid_rowconfigure(3, weight=1)  # Log panel
        self.grid_columnconfigure(0, weight=1)  # Library panel
        self.grid_columnconfigure(1, weight=3)  # Song panel

        # Link input bar
        url_frame = ctk.CTkFrame(self)
        url_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=5, pady=(0, 5))
        url_frame.grid_columnconfigure(1, weight=1)

        url_label = ctk.CTkLabel(url_frame, text="Link(s):")
        url_label.grid(row=0, column=0, padx=(10, 5), pady=10)

        self.url_entry = ctk.CTkEntry(
            url_frame,
            placeholder_text="YouTube playlist/video, X/Twitter post, or another yt-dlp link (one per line)"
        )
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=5, pady=10)
        self.url_entry.bind("<Return>", lambda e: self._add_links())

        add_button = ctk.CTkButton(url_frame, text="Add", width=100, command=self._add_links)
        add_button.grid(row=0, column=2, padx=(5, 10), pady=10)

        # Library panel (left)
        self.playlist_panel = PlaylistPanel(
            self,
            db=self.db,
            config=self.config,
            logger=self.logger,
            on_playlist_select=self._on_playlist_selected
        )
        self.playlist_panel.grid(row=2, column=0, sticky="nsew", padx=(5, 2), pady=5)

        # Song panel (right)
        self.song_panel = SongPanel(
            self,
            db=self.db,
            config=self.config,
            logger=self.logger
        )
        self.song_panel.grid(row=2, column=1, sticky="nsew", padx=(2, 5), pady=5)

        # Pipeline panel (full width below)
        self.pipeline_panel = PipelinePanel(
            self,
            db=self.db,
            config=self.config,
            logger=self.logger,
            on_download=self._on_download,
            on_convert=self._on_convert
        )
        self.pipeline_panel.grid(row=3, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

        # Log panel (full width at bottom)
        self.log_panel = LogPanel(
            self,
            db=self.db,
            config=self.config,
            logger=self.logger
        )
        self.log_panel.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=5, pady=(0, 5))

    def _setup_logging(self):
        """Setup GUI log handler to display logs in log panel."""
        from core.logger import GUILogHandler

        gui_handler = GUILogHandler(callback=self.log_panel.add_log_message)
        self.logger.addHandler(gui_handler)

        self.logger.info("GUI logging initialized")

    def _ui(self, func, *args):
        """Run a UI update on the Tk main thread.

        Worker threads must never touch Tk widgets directly.
        """
        try:
            self.after(0, lambda: func(*args))
        except Exception:
            pass

    def _toggle_theme(self):
        """Toggle between light and dark themes."""
        current = self.config.theme_mode

        # Cycle: dark → light → system → dark
        if current == "dark":
            new_theme = "light"
            button_text = "🌙 Dark"
        elif current == "light":
            new_theme = "system"
            button_text = "💻 System"
        else:  # system
            new_theme = "dark"
            button_text = "☀️ Light"

        self.config.theme_mode = new_theme
        ctk.set_appearance_mode(new_theme)
        self.theme_button.configure(text=button_text)

        self.logger.info(f"Theme changed to: {new_theme}")

    def _open_settings(self):
        """Open settings dialog."""
        dialog = SettingsDialog(self, db=self.db, config=self.config, logger=self.logger)
        dialog.grab_set()  # Modal
        self.wait_window(dialog)

        # Refresh UI after settings change
        self.playlist_panel.refresh()
        self.song_panel.refresh()

    # ==================== Adding links ====================

    def _add_links(self):
        """Add one or more links from the input bar."""
        raw = self.url_entry.get().strip()

        if not raw:
            messagebox.showinfo(
                "Add Link",
                "Paste a link first.\n\n"
                "Supported:\n"
                "• YouTube playlists\n"
                "• YouTube videos\n"
                "• X/Twitter posts\n"
                "• Other links supported by yt-dlp\n\n"
                "You can paste several links at once, one per line."
            )
            return

        links = split_links(raw)

        if not links:
            messagebox.showerror(
                "Invalid Link",
                "No usable links found.\n\n"
                "Paste a full URL, for example:\n"
                "https://www.youtube.com/playlist?list=...\n"
                "https://youtu.be/...\n"
                "https://x.com/user/status/..."
            )
            return

        self.url_entry.delete(0, 'end')
        self._extract_links(links)

    def _extract_links(self, links):
        """
        Extract metadata for the given links (Stage 1).

        Args:
            links: List of normalized URLs
        """
        if self.pipeline_panel.is_busy:
            self.logger.warning(
                f"Cannot add links - '{self.pipeline_panel.active_stage}' is still running"
            )
            return

        self.logger.info(f"Extracting {len(links)} link(s)...")
        self.pipeline_panel.set_busy("Extracting")

        from gui.workers.download_worker import DownloadWorker

        worker = DownloadWorker(
            db=self.db,
            config=self.config,
            logger=self.logger,
            mode='extract',
            urls=links,
            on_complete=lambda success, result: self._ui(
                self._on_extract_complete, success, result
            ),
            on_progress=lambda current, total, message: self._ui(
                self.pipeline_panel.update_progress, current, total, message
            )
        )
        worker.start()

    def _on_extract_complete(self, success, result):
        """Handle completion of link extraction."""
        self.pipeline_panel.set_idle()

        if not success:
            self.logger.error(f"Extraction failed: {result.get('error', 'Unknown error')}")
            messagebox.showerror(
                "Extraction Failed",
                result.get('error', 'The link could not be read.')
            )
            return

        self.playlist_panel.refresh()

        collections = result.get('collections') or []
        if collections:
            # Show the first newly added collection
            collection_id = collections[0]['playlist_id']
            self.playlist_panel.selected_playlist_id = collection_id
            self.song_panel.load_playlist_songs(collection_id)

        errors = result.get('errors') or []
        if errors:
            self.logger.warning(f"{len(errors)} link(s) failed to extract")
            details = "\n".join(
                f"• {e['url']}\n  {e['error']}" for e in errors[:5]
            )
            if len(errors) > 5:
                details += f"\n...and {len(errors) - 5} more (see log)"
            messagebox.showwarning(
                "Some Links Failed",
                f"Added {len(collections)} of {len(collections) + len(errors)} link(s).\n\n"
                f"{details}"
            )

    def _on_playlist_selected(self, playlist_id):
        """
        Handle collection selection.

        Args:
            playlist_id: Selected collection ID
        """
        self.logger.info(f"Collection selected: {playlist_id}")
        self.song_panel.load_playlist_songs(playlist_id)

    # ==================== Pipeline stages ====================

    def _on_download(self):
        """Stage 1: Download audio files."""
        if self.pipeline_panel.is_busy:
            self.logger.warning(
                f"Cannot start Download - '{self.pipeline_panel.active_stage}' is still running"
            )
            return

        videos = self.song_panel.get_selected_videos()

        if not videos:
            messagebox.showinfo(
                "Download",
                "Select the songs you want to download first.\n\n"
                "Use the checkboxes, or click 'Select All'."
            )
            return

        self.logger.info(f"Downloading {len(videos)} item(s)...")
        self.pipeline_panel.set_busy("Download")

        from gui.workers.download_worker import DownloadWorker

        worker = DownloadWorker(
            db=self.db,
            config=self.config,
            logger=self.logger,
            mode='download',
            videos=videos,
            on_complete=lambda success, result: self._ui(
                self._on_download_complete, success, result
            ),
            on_progress=lambda current, total, message: self._ui(
                self.pipeline_panel.update_progress, current, total, message
            )
        )
        worker.start()

    def _on_download_complete(self, success, result):
        """Handle completion of downloads."""
        self.pipeline_panel.set_idle()

        if success:
            self.logger.info(
                f"Downloaded {result.get('count', 0)} item(s)"
                + (f", {result.get('errors', 0)} failed" if result.get('errors') else "")
            )
            self.song_panel.refresh()
        else:
            self.logger.error(f"Download failed: {result.get('error', 'Unknown error')}")
            messagebox.showerror(
                "Download Failed",
                result.get('error', 'The download failed.')
            )

    def _on_convert(self):
        """Stage 2: Convert to MP3/M4A with metadata."""
        if self.pipeline_panel.is_busy:
            self.logger.warning(
                f"Cannot start Convert - '{self.pipeline_panel.active_stage}' is still running"
            )
            return

        # Convert the selected downloaded songs, or all downloaded songs
        # in the current collection when nothing is selected.
        selected = self.song_panel.get_selected_videos()
        ready = [v for v in selected if v.get('download_status') == 'downloaded']

        if not ready:
            ready = self.song_panel.get_downloaded_videos()

        if not ready:
            messagebox.showinfo(
                "Convert",
                "Nothing to convert yet.\n\n"
                "Download the songs first, then run Convert."
            )
            return

        self.logger.info(f"Converting {len(ready)} item(s)...")
        self.pipeline_panel.set_busy("Convert")

        from gui.workers.conversion_worker import ConversionWorker

        worker = ConversionWorker(
            db=self.db,
            config=self.config,
            logger=self.logger,
            videos=ready,
            on_complete=lambda success, result: self._ui(
                self._on_convert_complete, success, result
            ),
            on_progress=lambda current, total, message: self._ui(
                self.pipeline_panel.update_progress, current, total, message
            )
        )
        worker.start()

    def _on_convert_complete(self, success, result):
        """Handle completion of conversions."""
        self.pipeline_panel.set_idle()

        if success:
            self.logger.info(
                f"Converted {result.get('count', 0)} item(s)"
                + (f", {result.get('errors', 0)} failed" if result.get('errors') else "")
            )
            self.song_panel.refresh()
        else:
            self.logger.error(f"Conversion failed: {result.get('error', 'Unknown error')}")
            messagebox.showerror(
                "Conversion Failed",
                result.get('error', 'The conversion failed.')
            )

    def _on_closing(self):
        """Handle window close event."""
        self.logger.info("Closing application...")

        # Close database
        self.db.close()

        # Destroy window
        self.destroy()
