"""
Music DLP - Metadata Extractor

Extract playlist and media metadata using yt-dlp without downloading.

This module handles:
- Playlist metadata extraction (title, uploader, video count)
- Single-link extraction (YouTube video, X/Twitter post, or any yt-dlp site)
- Database storage of extracted metadata
- URL validation and type detection

Single links are stored as a one-item "collection" so the rest of the
pipeline (download, convert) works exactly the same way as for playlists.

Stage 1 of the pipeline: Extract metadata -> Store in database -> Ready for download
"""

import hashlib
from pathlib import Path
from typing import Optional, Tuple, List, Dict

import yt_dlp

from core.logger import setup_logger
from core.database import Database
from downloaders.url_utils import classify_url, single_collection_id


class MetadataExtractor:
    """Extracts playlist and media metadata using yt-dlp"""

    def __init__(self, database: Database, cookies_file: Optional[str] = None, logger=None):
        """
        Initialize metadata extractor.

        Args:
            database: Database instance for storing metadata
            cookies_file: Optional path to cookies.txt for authentication
            logger: Optional logger instance
        """
        self.db = database
        self.cookies_file = cookies_file
        self.logger = logger or setup_logger('metadata_extractor')

        self.logger.info("MetadataExtractor initialized")

    def _get_ydl_opts(self) -> dict:
        """
        Build yt-dlp options for metadata extraction only.

        Returns:
            Dict of yt-dlp options configured for extraction without download
        """
        opts = {
            # Don't download, only extract metadata
            'skip_download': True,
            'extract_flat': 'in_playlist',  # Get metadata without format resolution

            # Don't fail when formats can't be resolved (we don't need them)
            'ignore_no_formats_error': True,

            # EJS signature solving (required for YouTube as of 2026)
            'js_runtimes': {'node': {}},
            'remote_components': {'ejs:github': {}},

            # Performance
            'quiet': False,
            'no_warnings': False,

            # Authentication
            'sleep_interval_requests': 1,  # Be gentle with the APIs

            # Logging
            'logger': self.logger,

            # Don't save anything to disk
            'writeinfojson': False,
            'writethumbnail': False,
            'writedescription': False,
        }

        # Add cookies if configured
        if self.cookies_file and Path(self.cookies_file).exists():
            opts['cookiefile'] = self.cookies_file
            self.logger.debug(f"Using cookies from: {self.cookies_file}")

        return opts

    def extract_link(
        self,
        url: str,
        store_in_db: bool = True
    ) -> Tuple[bool, Optional[str], Optional[dict]]:
        """
        Extract metadata from any supported link.

        Playlists are stored as playlists; single items (YouTube videos,
        X/Twitter posts, other yt-dlp sites) are stored as a one-item
        collection so the rest of the pipeline treats them uniformly.

        Args:
            url: Link to extract
            store_in_db: If True, store results in database (default: True)

        Returns:
            Tuple of (success, error_message, collection_data)
        """
        info = classify_url(url)
        if info is None:
            return False, f"Unsupported link: {url}", None

        if info.kind == 'playlist':
            return self.extract_playlist(url, store_in_db)

        return self.extract_single_video(url, store_in_db)

    def extract_playlist(
        self,
        playlist_url: str,
        store_in_db: bool = True
    ) -> Tuple[bool, Optional[str], Optional[dict]]:
        """
        Extract metadata from a playlist (or a multi-item link).

        Args:
            playlist_url: URL of the playlist
            store_in_db: If True, store results in database (default: True)

        Returns:
            Tuple of (success: bool, error_message: str|None, playlist_data: dict|None)
        """
        self.logger.info(f"Extracting playlist metadata from: {playlist_url}")

        opts = self._get_ydl_opts()

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)

                if not info:
                    error_msg = "No metadata returned from yt-dlp"
                    self.logger.error(error_msg)
                    return False, error_msg, None

                # Some links resolve to a single item even when the URL looked
                # like a playlist (private playlist with one item, etc.).
                if info.get('_type') not in ('playlist', 'multi_video'):
                    self.logger.info("Link resolved to a single item; storing as a one-item collection")
                    return self._store_single_info(info, playlist_url, store_in_db)

                return self._store_playlist_info(info, playlist_url, store_in_db)

        except yt_dlp.utils.DownloadError as e:
            error_msg = f"yt-dlp download error: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg, None

        except Exception as e:
            error_msg = f"Unexpected error extracting playlist: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            return False, error_msg, None

    def extract_single_video(
        self,
        video_url: str,
        store_in_db: bool = True
    ) -> Tuple[bool, Optional[str], Optional[dict]]:
        """
        Extract metadata from a single link (video, tweet, etc.).

        Args:
            video_url: URL of the item
            store_in_db: If True, store result in database (default: True)

        Returns:
            Tuple of (success: bool, error_message: str|None, collection_data: dict|None)
        """
        self.logger.info(f"Extracting item metadata from: {video_url}")

        opts = self._get_ydl_opts()
        # Single items need full extraction for complete metadata
        opts['extract_flat'] = False

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=False)

                if not info:
                    error_msg = "No metadata returned from yt-dlp"
                    self.logger.error(error_msg)
                    return False, error_msg, None

                # An X/Twitter post can contain several videos; yt-dlp then
                # returns a playlist. Store it as a normal multi-item collection.
                if info.get('_type') in ('playlist', 'multi_video'):
                    return self._store_playlist_info(info, video_url, store_in_db)

                return self._store_single_info(info, video_url, store_in_db)

        except yt_dlp.utils.DownloadError as e:
            error_msg = f"yt-dlp download error: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg, None

        except Exception as e:
            error_msg = f"Unexpected error extracting video: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            return False, error_msg, None

    def _store_playlist_info(
        self,
        info: dict,
        source_url: str,
        store_in_db: bool
    ) -> Tuple[bool, Optional[str], Optional[dict]]:
        """Build playlist data from a yt-dlp info dict and optionally store it."""
        playlist_id = info.get('id') or ''
        if not playlist_id:
            digest = hashlib.md5(source_url.encode('utf-8')).hexdigest()[:12]
            playlist_id = single_collection_id('link', digest)

        title = info.get('title') or 'Untitled'
        uploader = info.get('uploader') or info.get('channel') or 'Unknown'
        entries = list(info.get('entries') or [])
        video_count = len(entries)

        self.logger.info(
            f"Extracted playlist: '{title}' by {uploader} "
            f"({video_count} items)"
        )

        videos = []
        for position, entry in enumerate(entries, start=1):
            if entry is None:
                self.logger.warning(f"Skipping null entry at position {position}")
                continue

            video_data = self._parse_video_entry(
                entry, position, source_url=source_url,
                shared_page=self._entries_share_page(entries, source_url)
            )
            if video_data:
                videos.append(video_data)

        playlist_data = {
            'playlist_id': playlist_id,
            'title': title,
            'uploader': uploader,
            'url': source_url,
            'video_count': video_count,
            'videos': videos,
            'is_single': False,
        }

        if store_in_db:
            self._store_playlist_in_db(playlist_data)

        self.logger.info(f"Successfully extracted {len(videos)} items")
        return True, None, playlist_data

    def _store_single_info(
        self,
        info: dict,
        source_url: str,
        store_in_db: bool
    ) -> Tuple[bool, Optional[str], Optional[dict]]:
        """Store a single item as a one-item collection."""
        video_data = self._parse_video_entry(info, position=1, source_url=source_url)

        if not video_data:
            error_msg = "Failed to parse item metadata"
            self.logger.error(error_msg)
            return False, error_msg, None

        link_info = classify_url(source_url)
        platform = link_info.platform if link_info else 'other'
        collection_id = single_collection_id(platform, video_data['video_id'])

        playlist_data = {
            'playlist_id': collection_id,
            'title': video_data['title'],
            'uploader': video_data['uploader'],
            'url': source_url,
            'video_count': 1,
            'videos': [video_data],
            'is_single': True,
        }

        self.logger.info(
            f"Extracted item: '{video_data['title']}' by {video_data['uploader']} "
            f"({video_data['duration']}s)"
        )

        if store_in_db:
            self._store_playlist_in_db(playlist_data)

        return True, None, playlist_data

    @staticmethod
    def _entries_share_page(entries: list, source_url: str) -> bool:
        """True when every item resolves to the same page URL.

        This happens for links that embed several videos in one page, such
        as a tweet with multiple videos. Each item then needs its index in
        the source so the downloader can select the right one.
        """
        resolved = [
            entry.get('webpage_url') or entry.get('url') or source_url
            for entry in entries
            if entry
        ]
        return len(resolved) > 1 and len(set(resolved)) == 1

    def _parse_video_entry(
        self,
        entry: dict,
        position: int = 1,
        source_url: Optional[str] = None,
        shared_page: bool = False
    ) -> Optional[dict]:
        """
        Parse a media entry dict from yt-dlp into our format.

        Handles both flat extraction (playlist mode) and full extraction entries.

        Args:
            entry: Media info dict from yt-dlp
            position: Position in the collection (default: 1)
            source_url: The URL the user supplied, used as a fallback
            shared_page: True when several items share one page URL

        Returns:
            Dict with standardized metadata, or None if parsing fails
        """
        try:
            video_id = entry.get('id')
            if not video_id:
                self.logger.warning("Media entry missing ID, skipping")
                return None

            # Prefer the page URL, then the direct URL, then the user's link.
            url = (
                entry.get('webpage_url')
                or entry.get('url')
                or source_url
                or ''
            )

            # Get thumbnail URL (prefer the highest quality available)
            thumbnails = entry.get('thumbnails') or []
            thumbnail_url = entry.get('thumbnail', '')

            if thumbnails:
                # Prefer the highest-quality JPEG; WebP variants are valid
                # but not every consumer handles them well.
                ordered = [
                    thumb for thumb in reversed(thumbnails)
                    if thumb.get('url')
                ]
                jpeg = next(
                    (t for t in ordered if '.webp' not in t['url'].lower()),
                    None
                )
                chosen = jpeg or (ordered[0] if ordered else None)
                if chosen:
                    thumbnail_url = chosen['url']

            # YouTube has a predictable thumbnail URL; other sites do not.
            if not thumbnail_url and 'youtu' in url.lower():
                thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

            uploader = (
                entry.get('uploader')
                or entry.get('channel')
                or entry.get('creator')
                or entry.get('uploader_id')
                or 'Unknown'
            )

            # When several entries share one page URL (a tweet with multiple
            # videos, for example), remember the entry's position so the
            # download step can select exactly this item.
            source_index = None
            if shared_page:
                source_index = int(entry.get('playlist_index') or position)

            video_data = {
                'video_id': video_id,
                'title': entry.get('title') or 'Untitled',
                'uploader': uploader,
                'duration': entry.get('duration') or 0,
                'url': url,
                'description': entry.get('description') or '',
                'thumbnail_url': thumbnail_url,
                'position': position,
                'source_index': source_index,
            }

            return video_data

        except Exception as e:
            self.logger.error(f"Error parsing media entry: {str(e)}", exc_info=True)
            return None

    def _store_playlist_in_db(self, playlist_data: dict):
        """
        Store a collection and its items in the database.

        Args:
            playlist_data: Dict with collection metadata and videos list
        """
        try:
            self.db.add_playlist(
                playlist_id=playlist_data['playlist_id'],
                title=playlist_data['title'],
                uploader=playlist_data['uploader'],
                url=playlist_data['url'],
                video_count=playlist_data['video_count']
            )

            self.logger.debug(f"Stored collection: {playlist_data['playlist_id']}")

            for video in playlist_data['videos']:
                self.db.add_video(
                    video_id=video['video_id'],
                    title=video['title'],
                    uploader=video['uploader'],
                    duration=video['duration'],
                    url=video['url'],
                    description=video.get('description', ''),
                    thumbnail_url=video.get('thumbnail_url', ''),
                    source_index=video.get('source_index')
                )

                self.db.link_video_to_playlist(
                    playlist_id=playlist_data['playlist_id'],
                    video_id=video['video_id'],
                    position=video['position']
                )

            self.logger.info(
                f"Stored {len(playlist_data['videos'])} item(s) in database "
                f"for collection {playlist_data['playlist_id']}"
            )

        except Exception as e:
            self.logger.error(f"Error storing collection in database: {str(e)}", exc_info=True)
            raise

    def _store_video_in_db(self, video_data: dict):
        """
        Store a single video in the database.

        Args:
            video_data: Dict with video metadata
        """
        try:
            self.db.add_video(
                video_id=video_data['video_id'],
                title=video_data['title'],
                uploader=video_data['uploader'],
                duration=video_data['duration'],
                url=video_data['url'],
                description=video_data.get('description', ''),
                thumbnail_url=video_data.get('thumbnail_url', ''),
                source_index=video_data.get('source_index')
            )

            self.logger.debug(f"Stored video: {video_data['video_id']}")

        except Exception as e:
            self.logger.error(f"Error storing video in database: {str(e)}", exc_info=True)
            raise

    def get_playlist_videos_count(self, playlist_url: str) -> Tuple[bool, Optional[str], int]:
        """
        Quickly get just the item count from a playlist (fast extraction).

        Args:
            playlist_url: URL of the playlist

        Returns:
            Tuple of (success: bool, error_message: str|None, video_count: int)
        """
        self.logger.info(f"Getting item count for playlist: {playlist_url}")

        opts = self._get_ydl_opts()
        opts['extract_flat'] = True  # Fast extraction, only IDs

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)

                if not info:
                    return False, "No metadata returned", 0

                if info.get('_type') != 'playlist':
                    return False, "URL is not a playlist", 0

                entries = list(info.get('entries') or [])
                count = len(entries)

                self.logger.info(f"Playlist contains {count} items")
                return True, None, count

        except Exception as e:
            error_msg = f"Error getting item count: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg, 0


# ==================== Compatibility helpers ====================

def validate_youtube_url(url: str) -> Tuple[bool, str]:
    """
    Validate a link and identify its type.

    Kept for compatibility; new code should use downloaders.url_utils.

    Returns:
        Tuple of (is_valid: bool, url_type: str)
        url_type can be: 'playlist', 'video', 'invalid'
    """
    info = classify_url(url)
    if info is None:
        return False, 'invalid'
    return True, info.kind


def extract_playlist_id(url: str) -> Optional[str]:
    """Extract a playlist ID from a URL, or None."""
    info = classify_url(url)
    if info and info.kind == 'playlist':
        return info.media_id
    return None


def extract_video_id(url: str) -> Optional[str]:
    """Extract a YouTube video ID from a URL, or None."""
    info = classify_url(url)
    if info and info.kind == 'video' and info.platform == 'youtube':
        return info.media_id
    return None
