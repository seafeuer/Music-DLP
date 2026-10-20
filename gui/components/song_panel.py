"""
Song Panel Component

Right panel showing list of songs/videos with:
- Title, channel, duration
- Download status (pending, downloading, done, error)
- Selection and bulk actions

Author: Music DLP Project
Version: 2.0.0
"""

import customtkinter as ctk
from typing import List, Dict
import tkinter as tk


class SongPanel(ctk.CTkScrollableFrame):
    """
    Song list panel (right side of main window).

    Displays videos from the selected collection with status indicators.
    """

    def __init__(self, parent, db, config, logger):
        """
        Initialize song panel.

        Args:
            parent: Parent widget
            db: Database instance
            config: ConfigManager instance
            logger: Logger instance
        """
        super().__init__(parent, label_text="Songs", width=700)

        self.db = db
        self.config = config
        self.logger = logger

        self.current_playlist_id = None
        self.song_vars = {}  # {video_id: IntVar}
        self.song_widgets = {}  # {video_id: frame}

        self._create_widgets()

    def _create_widgets(self):
        """Create static widgets (header and buttons)."""
        # Header frame
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=5, pady=5)

        # Column headers
        ctk.CTkLabel(header_frame, text="#", width=30, anchor="w").grid(row=0, column=0, padx=2)
        ctk.CTkLabel(header_frame, text="Title", width=300, anchor="w").grid(row=0, column=1, padx=2)
        ctk.CTkLabel(header_frame, text="Channel", width=150, anchor="w").grid(row=0, column=2, padx=2)
        ctk.CTkLabel(header_frame, text="Length", width=70, anchor="w").grid(row=0, column=3, padx=2)
        ctk.CTkLabel(header_frame, text="Status", width=100, anchor="w").grid(row=0, column=4, padx=2)

        # Button frame at bottom
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(side="bottom", fill="x", padx=5, pady=5)

        self.select_all_button = ctk.CTkButton(
            button_frame,
            text="Select All",
            width=100,
            command=self._select_all
        )
        self.select_all_button.pack(side="left", padx=2)

        self.select_downloaded_button = ctk.CTkButton(
            button_frame,
            text="Select Downloaded",
            width=140,
            command=self._select_downloaded
        )
        self.select_downloaded_button.pack(side="left", padx=2)

    def load_playlist_songs(self, playlist_id):
        """
        Load songs from a collection.

        Args:
            playlist_id: Collection ID to load
        """
        self.current_playlist_id = playlist_id
        self.refresh()

    def refresh(self):
        """Refresh song list from database."""
        # Clear existing widgets
        for widget in self.song_widgets.values():
            widget.destroy()

        self.song_widgets.clear()
        self.song_vars.clear()

        if not self.current_playlist_id:
            empty_label = ctk.CTkLabel(
                self,
                text="Select a link or playlist to view songs.",
                text_color="gray"
            )
            empty_label.pack(pady=20)
            self.song_widgets['empty'] = empty_label
            return

        try:
            videos = self.db.get_playlist_videos(self.current_playlist_id)

            if not videos:
                empty_label = ctk.CTkLabel(
                    self,
                    text="No songs in this collection.",
                    text_color="gray"
                )
                empty_label.pack(pady=20)
                self.song_widgets['empty'] = empty_label
                return

            for idx, video in enumerate(videos, 1):
                self._create_song_item(idx, video)

        except Exception as e:
            self.logger.error(f"Failed to refresh song panel: {e}")

    def _create_song_item(self, index, video):
        """
        Create a song item widget.

        Args:
            index: Song number (1-indexed)
            video: Video dict from database
        """
        video_id = video['video_id']

        # Item frame
        item_frame = ctk.CTkFrame(self)
        item_frame.pack(fill="x", padx=5, pady=2)

        # Checkbox variable
        var = tk.IntVar(value=0)
        self.song_vars[video_id] = var

        # Checkbox
        checkbox = ctk.CTkCheckBox(
            item_frame,
            text="",
            variable=var,
            width=20
        )
        checkbox.grid(row=0, column=0, padx=2, pady=5)

        # Index number
        ctk.CTkLabel(item_frame, text=str(index), width=30, anchor="w").grid(row=0, column=1, padx=2)

        # Title (truncate if too long)
        title = video.get('title', 'Unknown')
        title_display = title[:60] + "..." if len(title) > 60 else title
        ctk.CTkLabel(item_frame, text=title_display, width=300, anchor="w").grid(row=0, column=2, padx=2)

        # Channel
        uploader = video.get('uploader', '') or ''
        uploader_display = uploader[:22] + "..." if len(uploader) > 22 else uploader
        ctk.CTkLabel(item_frame, text=uploader_display, width=150, anchor="w").grid(row=0, column=3, padx=2)

        # Duration
        ctk.CTkLabel(
            item_frame,
            text=self._format_duration(video.get('duration') or 0),
            width=70,
            anchor="w"
        ).grid(row=0, column=4, padx=2)

        # Download status
        status = video.get('download_status', 'pending')
        ctk.CTkLabel(
            item_frame,
            text=self._get_status_text(status),
            width=100,
            anchor="w"
        ).grid(row=0, column=5, padx=2)

        self.song_widgets[video_id] = item_frame

    @staticmethod
    def _format_duration(seconds) -> str:
        """Format seconds as m:ss or h:mm:ss."""
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            return "-"

        if seconds <= 0:
            return "-"

        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)

        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    @staticmethod
    def _get_status_text(status) -> str:
        """Get a short status label."""
        status_map = {
            'pending': '⏸️ Pending',
            'downloading': '⬇️ Downloading',
            'downloaded': '✅ Done',
            'error': '❌ Error'
        }
        return status_map.get(status, status)

    def _select_all(self):
        """Select or deselect all songs."""
        any_selected = any(var.get() == 1 for var in self.song_vars.values())
        new_value = 0 if any_selected else 1

        for var in self.song_vars.values():
            var.set(new_value)

        action = "Deselected" if new_value == 0 else "Selected"
        self.logger.info(f"{action} all songs")

    def _select_downloaded(self):
        """Select every song that has been downloaded."""
        if not self.current_playlist_id:
            return

        try:
            videos = self.db.get_playlist_videos(self.current_playlist_id)
        except Exception as e:
            self.logger.error(f"Failed to select downloaded songs: {e}")
            return

        downloaded_ids = {
            v['video_id'] for v in videos
            if v.get('download_status') == 'downloaded'
        }

        for video_id, var in self.song_vars.items():
            var.set(1 if video_id in downloaded_ids else 0)

        self.logger.info(f"Selected {len(downloaded_ids)} downloaded song(s)")

    def get_selected_videos(self) -> List[Dict]:
        """
        Get list of selected video dictionaries (checked).

        Returns:
            List of video dictionaries with full metadata
        """
        selected = []
        for video_id, var in self.song_vars.items():
            if var.get() == 1:
                video = self.db.get_video(video_id)
                if video:
                    selected.append(video)

        return selected

    def get_downloaded_videos(self) -> List[Dict]:
        """
        Get all videos in the current collection that have been downloaded.

        Returns:
            List of video dicts
        """
        if not self.current_playlist_id:
            return []

        try:
            all_videos = self.db.get_playlist_videos(self.current_playlist_id)
            return [v for v in all_videos if v.get('download_status') == 'downloaded']
        except Exception as e:
            self.logger.error(f"Failed to get downloaded videos: {e}")
            return []
