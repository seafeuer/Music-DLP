"""
Music DLP - yt-dlp Manager

Core wrapper for yt-dlp with bot detection avoidance strategies.

This module handles all YouTube downloads with:
- Browser cookie authentication
- Sleep intervals to avoid rate limiting
- Proper error handling for 403/bot detection
- Progress tracking for GUI integration
- Format selection optimized for audio quality

Critical Bot Avoidance Strategy:
1. Use browser cookies (export from Chrome/Firefox)
2. Sleep 2-5 seconds between downloads
3. Sleep 1 second between API requests
4. Prefer format 251 (opus 160kbps)
5. On 403 errors, wait 60s before retrying
"""

import time
import random
from pathlib import Path
from typing import Optional, Callable, Tuple
import yt_dlp
from core.logger import setup_logger


class YTDLPManager:
    """Manages yt-dlp downloads with bot detection avoidance"""

    def __init__(
        self,
        cookies_file: Optional[str] = None,
        sleep_interval: int = 2,
        max_sleep_interval: int = 5,
        max_retries: int = 3,
        logger=None
    ):
        """
        Initialize yt-dlp manager with bot avoidance settings.

        Args:
            cookies_file: Path to cookies.txt file (export from browser)
            sleep_interval: Minimum seconds to sleep between downloads (default: 2)
            max_sleep_interval: Maximum seconds to sleep between downloads (default: 5)
            max_retries: Maximum retry attempts for failed downloads (default: 3)
            logger: Logger instance (will create one if not provided)
        """
        self.cookies_file = cookies_file
        self.sleep_interval = sleep_interval
        self.max_sleep_interval = max_sleep_interval
        self.max_retries = max_retries
        self.logger = logger or setup_logger('yt_dlp_manager')

        # Progress callback for GUI integration
        self.progress_callback: Optional[Callable] = None
        self.error_callback: Optional[Callable] = None

        # Exponential backoff state for 403/bot detection
        self._consecutive_403_count = 0

        # Track download statistics
        self.stats = {
            'downloads_attempted': 0,
            'downloads_successful': 0,
            'downloads_failed': 0,
            'bot_detections': 0,
            'total_bytes': 0
        }

        self.logger.info("YTDLPManager initialized")
        if self.cookies_file:
            self.logger.info(f"Using cookies from: {self.cookies_file}")
        else:
            self.logger.warning("No cookies file configured - may encounter bot detection!")

    def set_progress_callback(self, callback: Callable):
        """Set callback function for download progress updates"""
        self.progress_callback = callback

    def set_error_callback(self, callback: Callable):
        """Set callback function for error notifications"""
        self.error_callback = callback

    def _progress_hook(self, d: dict):
        """
        Internal progress hook called by yt-dlp.

        Args:
            d: Progress dict from yt-dlp with status, downloaded_bytes, total_bytes, etc.
        """
        if d['status'] == 'downloading':
            # Extract progress information
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            speed = d.get('speed', 0)
            eta = d.get('eta', 0)

            # Log progress periodically (not every update to avoid spam)
            if downloaded > 0 and total > 0:
                percent = (downloaded / total) * 100
                if percent % 10 < 1:  # Log every ~10%
                    self.logger.debug(
                        f"Download progress: {percent:.1f}% "
                        f"({downloaded / 1024 / 1024:.2f}MB / {total / 1024 / 1024:.2f}MB)"
                    )

            # Call GUI callback if registered
            if self.progress_callback:
                self.progress_callback({
                    'status': 'downloading',
                    'downloaded_bytes': downloaded,
                    'total_bytes': total,
                    'speed': speed,
                    'eta': eta,
                    'percent': (downloaded / total * 100) if total > 0 else 0
                })

        elif d['status'] == 'finished':
            filename = d.get('filename', 'unknown')
            total_bytes = d.get('total_bytes', 0)
            self.logger.info(
                f"Download finished: {Path(filename).name} "
                f"({total_bytes / 1024 / 1024:.2f}MB)"
            )
            self.stats['total_bytes'] += total_bytes

            if self.progress_callback:
                self.progress_callback({
                    'status': 'finished',
                    'filename': filename,
                    'total_bytes': total_bytes
                })

        elif d['status'] == 'error':
            self.logger.error(f"Download error: {d.get('error', 'Unknown error')}")
            if self.error_callback:
                self.error_callback(d.get('error', 'Unknown error'))

    def _get_ydl_opts(self, output_template: str, extract_only: bool = False) -> dict:
        """
        Build yt-dlp options dict with bot avoidance settings.

        Args:
            output_template: Output path template (e.g., '/path/[%(id)s].%(ext)s')
            extract_only: If True, skip download and only extract metadata

        Returns:
            Dict of yt-dlp options
        """
        opts = {
            # Audio format priority: 251 (opus 160kbps) > any opus > any webm > best audio
            # The final "best" fallback is what makes non-YouTube sites
            # (X/Twitter, etc.) work: FFmpeg extracts the audio afterwards.
            'format': '251/bestaudio[ext=opus]/bestaudio[ext=webm]/bestaudio/best',

            # Never expand a video link into a playlist
            'noplaylist': True,

            # Output configuration
            'outtmpl': output_template,
            'quiet': False,
            'no_warnings': False,
            'progress_hooks': [self._progress_hook],

            # Bot avoidance - CRITICAL FOR SUCCESS
            'sleep_interval': self.sleep_interval,
            'max_sleep_interval': self.max_sleep_interval,
            'sleep_interval_requests': 1,  # 1 second between API requests

            # Error handling
            'retries': self.max_retries,
            'fragment_retries': self.max_retries,
            'ignoreerrors': False,  # Don't skip errors, we'll handle them

            # Geo/network settings
            'geo_bypass': True,
            'nocheckcertificate': False,

            # Logging
            'logger': self.logger,

            # EJS signature solving (required for YouTube as of 2026)
            'js_runtimes': {'node': {}},
            'remote_components': {'ejs:github': {}},

            # Metadata
            'writeinfojson': False,  # We'll store in DB instead
            'writethumbnail': False,  # We'll handle thumbnails separately
            'postprocessors': [],
        }

        # Add cookies if configured
        if self.cookies_file and Path(self.cookies_file).exists():
            opts['cookiefile'] = self.cookies_file
        elif self.cookies_file:
            self.logger.warning(f"Cookies file not found: {self.cookies_file}")

        # If extract only, skip download
        if extract_only:
            opts['skip_download'] = True

        return opts

    @staticmethod
    def _resolve_downloaded_file(ydl, info: dict, output_dir: Path, safe_id: str):
        """Find the file yt-dlp actually wrote.

        ``prepare_filename()`` can return an extensionless path when the
        extractor does not populate ``ext`` (common for direct media URLs
        from X/Twitter). Prefer the ``requested_downloads`` record, then
        the prepared name, then a directory scan.
        """
        for download in info.get('requested_downloads') or []:
            filepath = download.get('filepath') or download.get('_filename')
            if filepath and Path(filepath).exists():
                return filepath

        prepared = ydl.prepare_filename(info)
        if prepared and Path(prepared).exists():
            return prepared

        prefix = f"[{safe_id}]"
        candidates = [
            path for path in output_dir.iterdir()
            if path.is_file()
            and path.name.startswith(prefix)
            and not path.name.endswith('.part')
        ]
        if candidates:
            return str(candidates[0])

        return prepared

    def download_audio(
        self,
        video_id: str,
        output_dir: str,
        filename_template: Optional[str] = None,
        url: Optional[str] = None,
        source_index: Optional[int] = None
    ) -> Tuple[bool, Optional[str], Optional[dict]]:
        """
        Download audio from a single media item.

        Args:
            video_id: Media ID, used for the temporary filename
            output_dir: Directory to save downloaded file
            filename_template: Custom filename template (default: '[{id}].%(ext)s')
            url: The item's page URL. Required for non-YouTube sites (X/Twitter,
                 etc.); defaults to a YouTube watch URL for compatibility.
            source_index: 1-based position when several items share one page
                 URL (for example a tweet containing several videos). yt-dlp
                 then selects that entry instead of the first one.

        Returns:
            Tuple of (success: bool, error_message: str|None, info: dict|None)
            info dict contains: filename, filesize, duration, etc.
        """
        self.stats['downloads_attempted'] += 1

        # Build output path. Media IDs from non-YouTube sites can contain
        # characters that are invalid in filenames, so sanitize them.
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        safe_id = "".join(
            c if (c.isalnum() or c in ('-', '_')) else '_'
            for c in str(video_id)
        ) or 'item'

        if filename_template is None:
            filename_template = f"[{safe_id}].%(ext)s"

        output_template = str(output_dir / filename_template)

        # Build URL
        url = url or f"https://www.youtube.com/watch?v={video_id}"

        # Get yt-dlp options
        opts = self._get_ydl_opts(output_template)

        if source_index:
            # Select one entry from a multi-item page instead of letting
            # yt-dlp pick the first. noplaylist would defeat this.
            opts['noplaylist'] = False
            opts['playlist_items'] = str(source_index)

        self.logger.info(f"Starting download: {video_id}")

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                # Download the video
                info = ydl.extract_info(url, download=True)

                # Get the actual filename (with extension)
                if info:
                    # playlist_items wraps the result in a playlist dict;
                    # the real item is its only entry.
                    item_info = info
                    if info.get('_type') in ('playlist', 'multi_video'):
                        entries = [e for e in (info.get('entries') or []) if e]
                        if entries:
                            item_info = entries[0]

                    downloaded_file = self._resolve_downloaded_file(
                        ydl, item_info, output_dir, safe_id
                    )

                    # Verify file exists
                    if downloaded_file and Path(downloaded_file).exists():
                        file_size = Path(downloaded_file).stat().st_size
                        self.logger.info(
                            f"Download successful: {video_id} -> {Path(downloaded_file).name} "
                            f"({file_size / 1024 / 1024:.2f}MB)"
                        )
                        self.stats['downloads_successful'] += 1
                        # Reset consecutive 403 counter on success
                        self._consecutive_403_count = 0

                        return True, None, {
                            'filename': downloaded_file,
                            'filesize': file_size,
                            'duration': item_info.get('duration', 0),
                            'title': item_info.get('title', ''),
                            'ext': item_info.get('ext', 'opus')
                        }
                    else:
                        error_msg = f"File not found after download: {downloaded_file}"
                        self.logger.error(error_msg)
                        self.stats['downloads_failed'] += 1
                        return False, error_msg, None
                else:
                    error_msg = "No info dict returned from yt-dlp"
                    self.logger.error(error_msg)
                    self.stats['downloads_failed'] += 1
                    return False, error_msg, None

        except yt_dlp.utils.DownloadError as e:
            error_str = str(e)

            # Check for bot detection / 403 errors
            if '403' in error_str or 'bot' in error_str.lower() or 'sign in' in error_str.lower():
                self._consecutive_403_count += 1
                self.stats['bot_detections'] += 1

                # Exponential backoff: 60s, 120s, 240s, capped at 600s
                wait_time = min(60 * (2 ** (self._consecutive_403_count - 1)), 600)

                self.logger.warning(
                    f"Bot detection triggered for {video_id}! "
                    f"(consecutive: {self._consecutive_403_count}) "
                    f"Error: {error_str}"
                )

                if self._consecutive_403_count >= 5:
                    # Circuit breaker: cookies likely expired
                    error_msg = (
                        f"COOKIE_REFRESH_NEEDED: {self._consecutive_403_count} consecutive 403 errors. "
                        f"Your cookies.txt may be expired. Re-export from browser and restart."
                    )
                    self.logger.error(error_msg)
                    self.stats['downloads_failed'] += 1
                    # Still wait before returning so next attempt has a chance
                    self.logger.warning(f"Waiting {wait_time}s before continuing...")
                    time.sleep(wait_time)
                    return False, error_msg, None

                self.logger.warning(f"Waiting {wait_time}s before next download (backoff level {self._consecutive_403_count})...")
                time.sleep(wait_time)

                error_msg = f"Bot detection (403). Waited {wait_time}s. Consecutive: {self._consecutive_403_count}."
                self.stats['downloads_failed'] += 1
                return False, error_msg, None

            else:
                # Other download error (not 403) - reset 403 counter
                self._consecutive_403_count = 0
                self.logger.error(f"Download failed for {video_id}: {error_str}")
                self.stats['downloads_failed'] += 1
                return False, error_str, None

        except Exception as e:
            # Unexpected error
            error_msg = f"Unexpected error downloading {video_id}: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            self.stats['downloads_failed'] += 1
            return False, error_msg, None

    def download_batch(
        self,
        video_ids: list[str],
        output_dir: str,
        progress_callback: Optional[Callable] = None
    ) -> dict:
        """
        Download multiple videos with bot avoidance delays.

        Args:
            video_ids: List of YouTube video IDs
            output_dir: Directory to save downloads
            progress_callback: Optional callback(video_id, index, total, success, error)

        Returns:
            Dict with results: {
                'successful': [video_ids],
                'failed': [(video_id, error_msg), ...],
                'total_time': seconds,
                'total_size': bytes
            }
        """
        start_time = time.time()
        successful = []
        failed = []
        total_size = 0

        self.logger.info(f"Starting batch download of {len(video_ids)} videos")

        for index, video_id in enumerate(video_ids, start=1):
            self.logger.info(f"Downloading {index}/{len(video_ids)}: {video_id}")

            # Download the video
            success, error, info = self.download_audio(video_id, output_dir)

            if success:
                successful.append(video_id)
                total_size += info.get('filesize', 0)

                # Call progress callback
                if progress_callback:
                    progress_callback(video_id, index, len(video_ids), True, None)
            else:
                failed.append((video_id, error))

                # Call progress callback
                if progress_callback:
                    progress_callback(video_id, index, len(video_ids), False, error)

            # Random sleep between downloads (except after last one)
            if index < len(video_ids):
                sleep_time = random.uniform(self.sleep_interval, self.max_sleep_interval)
                self.logger.debug(f"Sleeping {sleep_time:.1f}s before next download...")
                time.sleep(sleep_time)

        total_time = time.time() - start_time

        # Log summary
        self.logger.info(
            f"Batch download complete: {len(successful)}/{len(video_ids)} successful "
            f"in {total_time:.1f}s ({total_size / 1024 / 1024:.2f}MB total)"
        )

        if failed:
            self.logger.warning(f"{len(failed)} downloads failed:")
            for video_id, error in failed:
                self.logger.warning(f"  - {video_id}: {error}")

        return {
            'successful': successful,
            'failed': failed,
            'total_time': total_time,
            'total_size': total_size
        }

    def get_stats(self) -> dict:
        """
        Get download statistics.

        Returns:
            Dict with download stats
        """
        return self.stats.copy()

    def reset_stats(self):
        """Reset download statistics"""
        self.stats = {
            'downloads_attempted': 0,
            'downloads_successful': 0,
            'downloads_failed': 0,
            'bot_detections': 0,
            'total_bytes': 0
        }
        self.logger.info("Download statistics reset")


def validate_cookies_file(cookies_path: str) -> bool:
    """
    Validate that cookies file exists and is readable.

    Args:
        cookies_path: Path to cookies.txt file

    Returns:
        True if valid, False otherwise
    """
    path = Path(cookies_path)

    if not path.exists():
        return False

    if not path.is_file():
        return False

    # Try to read first few lines
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read(100)
            # Basic validation - should contain "# Netscape HTTP Cookie File"
            # or have cookie-like content
            return len(content) > 0
    except Exception:
        return False
