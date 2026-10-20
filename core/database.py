"""SQLite database manager for Music DLP.

Handles all database operations including schema creation, migrations,
and CRUD operations for collections, videos, and output records.
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
import logging


class Database:
    """SQLite database manager."""

    SCHEMA_VERSION = "2.0"

    def __init__(self, db_path: str = "music_dlp.db"):
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.logger = logging.getLogger(__name__)
        self.conn: Optional[sqlite3.Connection] = None

        # Connect and initialize schema
        self._connect()
        self._init_schema()

    def _connect(self):
        """Establish database connection."""
        self.conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False  # Allow multi-threaded access
        )
        self.conn.row_factory = sqlite3.Row  # Access columns by name
        self.conn.execute("PRAGMA foreign_keys = ON")  # Enable CASCADE deletes
        self.logger.info(f"Connected to database: {self.db_path}")

    def _init_schema(self):
        """Create database schema if it doesn't exist."""
        cursor = self.conn.cursor()

        # Playlists table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlists (
                playlist_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                uploader TEXT,
                url TEXT NOT NULL,
                video_count INTEGER,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Videos table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                uploader TEXT,
                duration INTEGER,
                url TEXT NOT NULL,
                description TEXT,
                thumbnail_url TEXT,

                download_status TEXT DEFAULT 'pending' CHECK(download_status IN ('pending', 'downloading', 'downloaded', 'error')),
                download_date TIMESTAMP,
                download_error TEXT,
                file_path_raw TEXT,

                -- Index within the source collection when several items
                -- share one page URL (e.g. a tweet with multiple videos).
                source_index INTEGER,

                extraction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Playlist-Videos many-to-many relationship
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlist_videos (
                playlist_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (playlist_id, video_id),
                FOREIGN KEY (playlist_id) REFERENCES playlists(playlist_id) ON DELETE CASCADE,
                FOREIGN KEY (video_id) REFERENCES videos(video_id) ON DELETE CASCADE
            )
        """)

        # Create indices for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_videos_download_status
            ON videos(download_status)
        """)

        # Output records table
        #
        # Despite the historical name, this table now tracks conversion
        # state and output file paths for each video. It keeps its name so
        # databases created by earlier versions continue to work.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approved_metadata (
                video_id TEXT PRIMARY KEY,

                final_title TEXT NOT NULL,
                final_artist TEXT,
                album TEXT,
                tags TEXT,

                approval_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                conversion_status TEXT DEFAULT 'pending' CHECK(conversion_status IN ('pending', 'converting', 'completed', 'error')),
                conversion_error TEXT,

                file_path_mp3 TEXT,
                file_path_m4a TEXT,
                thumbnail_path TEXT,

                FOREIGN KEY (video_id) REFERENCES videos(video_id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_approved_conversion_status
            ON approved_metadata(conversion_status)
        """)

        # App configuration table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Run migrations for new columns
        self._run_migrations(cursor)

        self.conn.commit()
        self.logger.info(f"Database schema initialized (version {self.SCHEMA_VERSION})")

        # Initialize default config if empty
        self._init_default_config()

    def _run_migrations(self, cursor):
        """Run schema migrations for new columns."""
        # Add source_index to databases created before multi-item sources
        # (such as multi-video tweets) were supported.
        cursor.execute("PRAGMA table_info(videos)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'source_index' not in columns:
            cursor.execute("ALTER TABLE videos ADD COLUMN source_index INTEGER")
            self.logger.info("Migration: Added source_index to videos")

    def _init_default_config(self):
        """Initialize default configuration values."""
        defaults = {
            'theme_mode': 'dark',
            'download_format': '251/bestaudio[ext=opus]/bestaudio[ext=webm]/bestaudio/best',
            'mp3_bitrate': '320',
            'm4a_bitrate': '320',
            'cookies_file': '',
            'schema_version': self.SCHEMA_VERSION
        }

        cursor = self.conn.cursor()
        for key, value in defaults.items():
            cursor.execute(
                "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
                (key, value)
            )
        self.conn.commit()

    # ==================== Playlist Operations ====================

    def add_playlist(self, playlist_id: str, title: str, url: str,
                     uploader: Optional[str] = None,
                     video_count: Optional[int] = None) -> bool:
        """Add a new playlist to the database.

        Args:
            playlist_id: YouTube playlist ID
            title: Playlist title
            url: Full playlist URL
            uploader: Channel name
            video_count: Number of videos

        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO playlists
                (playlist_id, title, uploader, url, video_count, last_updated)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (playlist_id, title, uploader, url, video_count))
            self.conn.commit()
            self.logger.info(f"Added playlist: {title} ({playlist_id})")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to add playlist {playlist_id}: {e}")
            return False

    def get_playlists(self) -> List[Dict[str, Any]]:
        """Get all playlists.

        Returns:
            List of playlist dictionaries
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM playlists ORDER BY added_date DESC")
        return [dict(row) for row in cursor.fetchall()]

    def get_playlist(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        """Get a single playlist by ID.

        Args:
            playlist_id: YouTube playlist ID

        Returns:
            Playlist dictionary or None
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM playlists WHERE playlist_id = ?", (playlist_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_playlist(self, playlist_id: str, cleanup_orphaned: bool = True) -> Dict[str, Any]:
        """Delete a playlist and optionally clean up orphaned videos.

        Args:
            playlist_id: Playlist ID to delete
            cleanup_orphaned: If True, also delete videos and files that become orphaned

        Returns:
            Dictionary with deletion summary:
            {
                'playlist_deleted': bool,
                'orphaned_videos_deleted': int,
                'files_deleted': int,
                'errors': List[str]
            }
        """
        summary = {
            'playlist_deleted': False,
            'orphaned_videos_deleted': 0,
            'files_deleted': 0,
            'errors': []
        }

        try:
            cursor = self.conn.cursor()

            # Get playlist info before deleting
            cursor.execute("SELECT title FROM playlists WHERE playlist_id = ?", (playlist_id,))
            playlist = cursor.fetchone()
            if not playlist:
                summary['errors'].append(f"Playlist {playlist_id} not found")
                return summary

            playlist_title = playlist['title']

            # Delete playlist (CASCADE will delete playlist_videos relationships)
            cursor.execute("DELETE FROM playlists WHERE playlist_id = ?", (playlist_id,))
            self.conn.commit()

            summary['playlist_deleted'] = True
            self.logger.info(f"Deleted playlist: {playlist_title} ({playlist_id})")

            # Clean up orphaned videos and files if requested
            if cleanup_orphaned:
                self.logger.info("Checking for orphaned videos...")
                cleanup_result = self.delete_orphaned_videos(delete_files=True)

                summary['orphaned_videos_deleted'] = cleanup_result['videos_deleted']
                summary['files_deleted'] = cleanup_result['files_deleted']
                summary['errors'].extend(cleanup_result['errors'])

                if cleanup_result['videos_deleted'] > 0:
                    self.logger.info(
                        f"Cleaned up {cleanup_result['videos_deleted']} orphaned videos "
                        f"and {cleanup_result['files_deleted']} files"
                    )

            return summary

        except sqlite3.Error as e:
            error_msg = f"Failed to delete playlist {playlist_id}: {e}"
            self.logger.error(error_msg)
            summary['errors'].append(error_msg)
            self.conn.rollback()
            return summary

    def update_playlist(self, playlist_id: str, title: Optional[str] = None,
                       video_count: Optional[int] = None) -> bool:
        """Update playlist information.

        Args:
            playlist_id: Playlist ID to update
            title: New title (optional)
            video_count: New video count (optional)

        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()

            # Build dynamic update query
            updates = []
            params = []

            if title is not None:
                updates.append("title = ?")
                params.append(title)

            if video_count is not None:
                updates.append("video_count = ?")
                params.append(video_count)

            if not updates:
                return True  # Nothing to update

            updates.append("last_updated = CURRENT_TIMESTAMP")
            params.append(playlist_id)

            query = f"UPDATE playlists SET {', '.join(updates)} WHERE playlist_id = ?"
            cursor.execute(query, params)

            self.conn.commit()
            self.logger.debug(f"Updated playlist: {playlist_id}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to update playlist {playlist_id}: {e}")
            self.conn.rollback()
            return False

    # ==================== Video Operations ====================

    def add_video(self, video_id: str, title: str, url: str,
                  uploader: Optional[str] = None,
                  duration: Optional[int] = None,
                  description: Optional[str] = None,
                  thumbnail_url: Optional[str] = None,
                  source_index: Optional[int] = None) -> bool:
        """Add a new video to the database.

        Args:
            video_id: Media ID
            title: Video title
            url: Full video URL
            uploader: Channel name
            duration: Duration in seconds
            description: Video description
            thumbnail_url: Thumbnail URL
            source_index: Position within a shared page URL, when several
                items come from one link (e.g. a multi-video tweet)

        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT INTO videos
                (video_id, title, uploader, duration, url, description, thumbnail_url, source_index)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    title = excluded.title,
                    uploader = excluded.uploader,
                    duration = excluded.duration,
                    url = excluded.url,
                    description = excluded.description,
                    thumbnail_url = excluded.thumbnail_url,
                    source_index = excluded.source_index
            """, (video_id, title, uploader, duration, url, description, thumbnail_url, source_index))
            self.conn.commit()
            self.logger.debug(f"Added video: {title} ({video_id})")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to add video {video_id}: {e}")
            return False

    def link_video_to_playlist(self, playlist_id: str, video_id: str, position: int) -> bool:
        """Link a video to a playlist.

        Args:
            playlist_id: Playlist ID
            video_id: Video ID
            position: Position in playlist (1-indexed)

        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO playlist_videos (playlist_id, video_id, position)
                VALUES (?, ?, ?)
            """, (playlist_id, video_id, position))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to link video {video_id} to playlist {playlist_id}: {e}")
            return False

    def get_video(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Get a single video by ID.

        Args:
            video_id: YouTube video ID

        Returns:
            Video dictionary or None
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_playlist_videos(self, playlist_id: str) -> List[Dict[str, Any]]:
        """Get all videos in a playlist, ordered by position.

        Args:
            playlist_id: Playlist ID

        Returns:
            List of video dictionaries with position
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT v.*, pv.position
            FROM videos v
            JOIN playlist_videos pv ON v.video_id = pv.video_id
            WHERE pv.playlist_id = ?
            ORDER BY pv.position
        """, (playlist_id,))
        return [dict(row) for row in cursor.fetchall()]

    def update_download_status(self, video_id: str, status: str,
                               error: Optional[str] = None,
                               file_path: Optional[str] = None) -> bool:
        """Update video download status.

        Args:
            video_id: Video ID
            status: New status (pending/downloading/downloaded/error)
            error: Error message if status is 'error'
            file_path: Path to downloaded file

        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                UPDATE videos
                SET download_status = ?,
                    download_error = ?,
                    file_path_raw = ?,
                    download_date = CASE WHEN ? = 'downloaded' THEN CURRENT_TIMESTAMP ELSE download_date END
                WHERE video_id = ?
            """, (status, error, file_path, status, video_id))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to update download status for {video_id}: {e}")
            return False

    def get_videos_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Get videos by download status.

        Args:
            status: Download status to filter by

        Returns:
            List of video dictionaries
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM videos WHERE download_status = ?", (status,))
        return [dict(row) for row in cursor.fetchall()]

    def delete_video(self, video_id: str) -> bool:
        """Delete a video and all related data.

        Warning: This will CASCADE delete:
        - playlist_videos relationships
        - output records (approved_metadata)

        Args:
            video_id: Video ID to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()

            # Delete video (CASCADE will delete related records)
            cursor.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))

            self.conn.commit()
            self.logger.info(f"Deleted video: {video_id}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to delete video {video_id}: {e}")
            self.conn.rollback()
            return False

    def delete_orphaned_videos(self, delete_files: bool = True) -> Dict[str, Any]:
        """Delete videos not linked to any playlist and optionally their files.

        Args:
            delete_files: If True, also delete associated files from disk

        Returns:
            Dictionary with cleanup summary:
            {
                'videos_deleted': int,
                'files_deleted': int,
                'errors': List[str]
            }
        """
        from pathlib import Path

        orphaned = self.get_orphaned_videos()
        summary = {
            'videos_deleted': 0,
            'files_deleted': 0,
            'errors': []
        }

        if not orphaned:
            self.logger.info("No orphaned videos found")
            return summary

        self.logger.info(f"Found {len(orphaned)} orphaned videos")

        for video in orphaned:
            video_id = video['video_id']
            files_to_delete = []

            try:
                # Collect file paths
                if delete_files:
                    # Raw download file
                    if video.get('file_path_raw'):
                        files_to_delete.append(Path(video['file_path_raw']))

                    # Get output record for converted files
                    metadata = self.get_approved_metadata(video_id)
                    if metadata:
                        if metadata.get('file_path_mp3'):
                            files_to_delete.append(Path(metadata['file_path_mp3']))
                        if metadata.get('file_path_m4a'):
                            files_to_delete.append(Path(metadata['file_path_m4a']))
                        if metadata.get('thumbnail_path'):
                            files_to_delete.append(Path(metadata['thumbnail_path']))

                # Delete files first
                if delete_files:
                    for file_path in files_to_delete:
                        try:
                            if file_path.exists():
                                file_path.unlink()
                                summary['files_deleted'] += 1
                                self.logger.debug(f"Deleted file: {file_path}")
                        except Exception as e:
                            error_msg = f"Failed to delete file {file_path}: {e}"
                            summary['errors'].append(error_msg)
                            self.logger.warning(error_msg)

                # Delete video from database (CASCADE handles related data)
                if self.delete_video(video_id):
                    summary['videos_deleted'] += 1

            except Exception as e:
                error_msg = f"Failed to process orphaned video {video_id}: {e}"
                summary['errors'].append(error_msg)
                self.logger.error(error_msg)

        self.logger.info(
            f"Cleanup complete: {summary['videos_deleted']} videos deleted, "
            f"{summary['files_deleted']} files deleted, "
            f"{len(summary['errors'])} errors"
        )

        return summary

    def unlink_video_from_playlist(self, playlist_id: str, video_id: str) -> bool:
        """Remove video from a specific playlist without deleting the video.

        Args:
            playlist_id: Playlist ID
            video_id: Video ID

        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()

            cursor.execute("""
                DELETE FROM playlist_videos
                WHERE playlist_id = ? AND video_id = ?
            """, (playlist_id, video_id))

            self.conn.commit()
            self.logger.debug(f"Unlinked video {video_id} from playlist {playlist_id}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to unlink video {video_id} from playlist {playlist_id}: {e}")
            self.conn.rollback()
            return False

    def bulk_update_download_status(self, video_ids: List[str], status: str) -> bool:
        """Update download status for multiple videos at once.

        Useful for batch operations like marking multiple videos as pending or error.

        Args:
            video_ids: List of video IDs to update
            status: New status (pending/downloading/downloaded/error)

        Returns:
            True if successful, False otherwise
        """
        if not video_ids:
            return True  # Nothing to update

        try:
            cursor = self.conn.cursor()

            # Use executemany for better performance
            placeholders = ', '.join('?' * len(video_ids))
            query = f"""
                UPDATE videos
                SET download_status = ?
                WHERE video_id IN ({placeholders})
            """

            cursor.execute(query, [status] + video_ids)
            self.conn.commit()

            updated_count = cursor.rowcount
            self.logger.info(f"Bulk updated {updated_count} videos to status: {status}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to bulk update download status: {e}")
            self.conn.rollback()
            return False

    # ==================== Output Record Operations ====================

    def approve_metadata(self, video_id: str, final_title: str, final_artist: Optional[str] = None,
                        album: Optional[str] = None, tags: Optional[List[str]] = None) -> bool:
        """Create or replace the output record for a video.

        Args:
            video_id: Video ID
            final_title: Title to embed and use for the filename
            final_artist: Artist to embed (the source channel/uploader)
            album: Album name
            tags: List of tags

        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()
            tags_json = json.dumps(tags) if tags else None
            cursor.execute("""
                INSERT OR REPLACE INTO approved_metadata
                (video_id, final_title, final_artist, album, tags)
                VALUES (?, ?, ?, ?, ?)
            """, (video_id, final_title, final_artist, album, tags_json))
            self.conn.commit()
            self.logger.info(f"Saved output record for {video_id}: {final_title}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to save output record for {video_id}: {e}")
            return False

    def get_approved_metadata(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Get the output record for a video.

        Args:
            video_id: Video ID

        Returns:
            Record dictionary with parsed tags or None
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM approved_metadata WHERE video_id = ?", (video_id,))
        row = cursor.fetchone()
        if row:
            metadata = dict(row)
            if metadata['tags']:
                metadata['tags'] = json.loads(metadata['tags'])
            return metadata
        return None

    def add_approved_metadata(self, video_id: str, final_title: str, final_artist: Optional[str] = None,
                             album: Optional[str] = None, tags: Optional[List[str]] = None) -> bool:
        """Alias for approve_metadata(), kept for call-site compatibility."""
        return self.approve_metadata(video_id, final_title, final_artist, album, tags)

    def update_approved_metadata(self, video_id: str, final_title: Optional[str] = None,
                                final_artist: Optional[str] = None, album: Optional[str] = None,
                                tags: Optional[List[str]] = None) -> bool:
        """Update an existing output record (partial update).

        Args:
            video_id: Video ID
            final_title: New title (optional)
            final_artist: New artist (optional)
            album: New album (optional)
            tags: New tags (optional)

        Returns:
            True if successful, False otherwise
        """
        try:
            existing = self.get_approved_metadata(video_id)
            if not existing:
                self.logger.warning(f"No output record found for {video_id}")
                return False

            # Use existing values if not provided
            cursor = self.conn.cursor()
            tags_json = json.dumps(tags) if tags is not None else existing.get('tags')
            if tags_json and isinstance(tags_json, list):
                tags_json = json.dumps(tags_json)

            cursor.execute("""
                UPDATE approved_metadata
                SET final_title = COALESCE(?, final_title),
                    final_artist = COALESCE(?, final_artist),
                    album = COALESCE(?, album),
                    tags = COALESCE(?, tags)
                WHERE video_id = ?
            """, (final_title, final_artist, album, tags_json, video_id))

            self.conn.commit()
            self.logger.debug(f"Updated output record for {video_id}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to update output record for {video_id}: {e}")
            self.conn.rollback()
            return False

    def update_conversion_status(self, video_id: str, status: str,
                                 mp3_path: Optional[str] = None,
                                 m4a_path: Optional[str] = None,
                                 thumbnail_path: Optional[str] = None,
                                 error: Optional[str] = None) -> bool:
        """Update conversion status and output paths for a video.

        Args:
            video_id: Video ID
            status: Conversion status
            mp3_path: Path to MP3 file
            m4a_path: Path to M4A file
            thumbnail_path: Path to thumbnail
            error: Error message if status is 'error'

        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                UPDATE approved_metadata
                SET conversion_status = ?,
                    file_path_mp3 = COALESCE(?, file_path_mp3),
                    file_path_m4a = COALESCE(?, file_path_m4a),
                    thumbnail_path = COALESCE(?, thumbnail_path),
                    conversion_error = ?
                WHERE video_id = ?
            """, (status, mp3_path, m4a_path, thumbnail_path, error, video_id))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to update conversion status for {video_id}: {e}")
            return False

    def delete_approved_metadata(self, video_id: str) -> bool:
        """Delete the output record for a video.

        This removes conversion tracking for the video but keeps the video
        itself and its downloaded file.

        Args:
            video_id: Video ID

        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM approved_metadata WHERE video_id = ?", (video_id,))
            self.conn.commit()
            self.logger.info(f"Deleted output record for: {video_id}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to delete output record for {video_id}: {e}")
            self.conn.rollback()
            return False

    # ==================== Query Methods ====================

    def get_videos_without_metadata(self) -> List[Dict[str, Any]]:
        """Get downloaded videos that have no output record yet.

        Returns:
            List of video dictionaries that still need converting
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT v.*
            FROM videos v
            LEFT JOIN approved_metadata am ON v.video_id = am.video_id
            WHERE v.download_status = 'downloaded'
              AND am.video_id IS NULL
            ORDER BY v.extraction_date DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_conversion_queue(self) -> List[Dict[str, Any]]:
        """Get videos that are ready for conversion.

        Returns videos where:
        - Download status is 'downloaded'
        - An output record exists
        - Conversion status is 'pending'

        Returns:
            List of video dictionaries, ready to convert
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT v.*, am.final_title, am.final_artist, am.album, am.tags,
                   am.thumbnail_path, am.conversion_status
            FROM videos v
            JOIN approved_metadata am ON v.video_id = am.video_id
            WHERE v.download_status = 'downloaded'
              AND am.conversion_status = 'pending'
            ORDER BY v.extraction_date ASC
        """)

        results = []
        for row in cursor.fetchall():
            video = dict(row)
            # Parse JSON tags
            if video.get('tags'):
                video['tags'] = json.loads(video['tags'])
            results.append(video)
        return results

    def get_orphaned_videos(self) -> List[Dict[str, Any]]:
        """Get videos that are not linked to any playlist.

        These are orphaned videos that can be safely deleted.

        Returns:
            List of video dictionaries not in any playlist
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT v.*
            FROM videos v
            LEFT JOIN playlist_videos pv ON v.video_id = pv.video_id
            WHERE pv.video_id IS NULL
            ORDER BY v.extraction_date DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    # ==================== Config Operations ====================

    def get_config(self, key: str) -> Optional[str]:
        """Get configuration value.

        Args:
            key: Configuration key

        Returns:
            Configuration value or None
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT value FROM app_config WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row['value'] if row else None

    def set_config(self, key: str, value: str) -> bool:
        """Set configuration value.

        Args:
            key: Configuration key
            value: Configuration value

        Returns:
            True if successful, False otherwise
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO app_config (key, value, updated_date)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (key, value))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            self.logger.error(f"Failed to set config {key}: {e}")
            return False

    def get_all_config(self) -> Dict[str, str]:
        """Get all configuration values.

        Returns:
            Dictionary of all config key-value pairs
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT key, value FROM app_config")
        return {row['key']: row['value'] for row in cursor.fetchall()}

    # ==================== Utility Methods ====================

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.logger.info("Database connection closed")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
