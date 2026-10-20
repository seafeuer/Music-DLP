"""
Playlist Panel Component

Left panel showing list of playlists with:
- Checkbox for selection
- Playlist title and video count
- Refresh and Delete buttons
- Extract selected playlists

Author: Music DLP Project
Version: 1.0.0
"""

import customtkinter as ctk
from typing import Callable, Optional, List
import tkinter as tk


class PlaylistPanel(ctk.CTkScrollableFrame):
    """
    Playlist list panel (left side of main window).

    Displays all playlists from database with checkboxes for selection.
    """

    def __init__(self, parent, db, config, logger, on_playlist_select: Optional[Callable] = None):
        """
        Initialize playlist panel.

        Args:
            parent: Parent widget
            db: Database instance
            config: ConfigManager instance
            logger: Logger instance
            on_playlist_select: Callback when playlist is selected (receives playlist_id)
        """
        super().__init__(parent, label_text="Library", width=250)

        self.db = db
        self.config = config
        self.logger = logger
        self.on_playlist_select = on_playlist_select

        self.playlist_vars = {}  # {playlist_id: IntVar}
        self.playlist_widgets = {}  # {playlist_id: frame}
        self.selected_playlist_id = None

        self._create_widgets()
        self.refresh()

    def _create_widgets(self):
        """Create static widgets (buttons at bottom)."""
        # Button frame at bottom
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(side="bottom", fill="x", padx=5, pady=5)

        self.refresh_button = ctk.CTkButton(
            button_frame,
            text="Refresh",
            width=100,
            command=self.refresh
        )
        self.refresh_button.pack(side="left", padx=2)

        self.delete_button = ctk.CTkButton(
            button_frame,
            text="Delete",
            width=100,
            fg_color="red",
            hover_color="darkred",
            command=self._delete_selected
        )
        self.delete_button.pack(side="left", padx=2)

    def refresh(self):
        """Refresh playlist list from database."""
        # Clear existing widgets
        for widget in self.playlist_widgets.values():
            widget.destroy()

        self.playlist_widgets.clear()
        self.playlist_vars.clear()

        # Get all playlists from database
        try:
            playlists = self.db.get_playlists()

            if not playlists:
                # Show empty state
                empty_label = ctk.CTkLabel(
                    self,
                    text="Nothing added yet.\nPaste a link above and press Add.",
                    text_color="gray"
                )
                empty_label.pack(pady=20)
                self.playlist_widgets['empty'] = empty_label
                return

            # Create checkbox for each playlist
            for playlist in playlists:
                self._create_playlist_item(playlist)

        except Exception as e:
            self.logger.error(f"Failed to refresh playlist panel: {e}")

    def _create_playlist_item(self, playlist):
        """
        Create a playlist item widget.

        Args:
            playlist: Playlist dict from database
        """
        playlist_id = playlist['playlist_id']

        # Item frame
        item_frame = ctk.CTkFrame(self)
        item_frame.pack(fill="x", padx=5, pady=2)

        # Checkbox variable
        var = tk.IntVar(value=0)
        self.playlist_vars[playlist_id] = var

        # Checkbox
        checkbox = ctk.CTkCheckBox(
            item_frame,
            text="",
            variable=var,
            width=20,
            command=lambda: self._on_checkbox_changed(playlist_id)
        )
        checkbox.grid(row=0, column=0, padx=(5, 2), pady=5, sticky="w")

        # Playlist title (clickable)
        title = playlist.get('title', 'Unknown Playlist')
        video_count = playlist.get('video_count', 0)

        title_button = ctk.CTkButton(
            item_frame,
            text=f"{title[:30]}...\n({video_count} videos)" if len(title) > 30 else f"{title}\n({video_count} videos)",
            fg_color="transparent",
            hover_color=("gray70", "gray30"),
            anchor="w",
            command=lambda: self._select_playlist(playlist_id)
        )
        title_button.grid(row=0, column=1, padx=2, pady=5, sticky="ew")
        item_frame.grid_columnconfigure(1, weight=1)

        self.playlist_widgets[playlist_id] = item_frame

    def _on_checkbox_changed(self, playlist_id):
        """
        Handle checkbox change.

        Args:
            playlist_id: Playlist ID
        """
        is_checked = self.playlist_vars[playlist_id].get() == 1
        self.logger.debug(f"Playlist {playlist_id} checkbox: {is_checked}")

    def _select_playlist(self, playlist_id):
        """
        Select a playlist and trigger callback.

        Args:
            playlist_id: Playlist ID to select
        """
        self.selected_playlist_id = playlist_id
        self.logger.info(f"Playlist selected: {playlist_id}")

        if self.on_playlist_select:
            self.on_playlist_select(playlist_id)

    def get_selected_playlists(self) -> List[str]:
        """
        Get list of selected playlist IDs (checked).

        Returns:
            List of playlist IDs
        """
        selected = []
        for playlist_id, var in self.playlist_vars.items():
            if var.get() == 1:
                selected.append(playlist_id)

        return selected

    def _delete_selected(self):
        """Delete selected collections with confirmation."""
        selected = self.get_selected_playlists()

        if not selected:
            self.logger.warning("Nothing selected for deletion")
            return

        # Show confirmation dialog
        from tkinter import messagebox

        confirm = messagebox.askyesno(
            "Delete Selected",
            f"Are you sure you want to delete {len(selected)} item(s)?\n\n"
            "This will also delete:\n"
            "• Songs that are not in any other collection\n"
            "• Downloaded files (.webm, .mp3, .m4a)\n"
            "• Thumbnails and metadata\n\n"
            "This action cannot be undone!"
        )

        if not confirm:
            return

        # Delete playlists and collect summary
        try:
            total_videos = 0
            total_files = 0
            errors = []

            for playlist_id in selected:
                summary = self.db.delete_playlist(playlist_id, cleanup_orphaned=True)

                if summary['playlist_deleted']:
                    self.logger.info(f"Deleted playlist: {playlist_id}")
                    total_videos += summary['orphaned_videos_deleted']
                    total_files += summary['files_deleted']
                    errors.extend(summary['errors'])
                else:
                    errors.extend(summary['errors'])

            # Show summary
            if errors:
                messagebox.showwarning(
                    "Deletion Complete with Warnings",
                    f"Deleted {len(selected)} item(s)\n"
                    f"Cleaned up {total_videos} orphaned videos\n"
                    f"Deleted {total_files} files\n\n"
                    f"⚠️ {len(errors)} error(s) occurred:\n" +
                    "\n".join(errors[:3]) +
                    (f"\n...and {len(errors) - 3} more" if len(errors) > 3 else "")
                )
            else:
                messagebox.showinfo(
                    "Deletion Complete",
                    f"✅ Successfully deleted {len(selected)} item(s)\n"
                    f"✅ Cleaned up {total_videos} orphaned videos\n"
                    f"✅ Deleted {total_files} files"
                )

            self.refresh()

        except Exception as e:
            self.logger.error(f"Failed to delete playlists: {e}")
            messagebox.showerror("Error", f"Failed to delete playlists: {e}")
