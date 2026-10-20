"""
Download Worker

Background threading worker for:
- Playlist metadata extraction
- Video downloads
- Progress callbacks to GUI

Author: Music DLP Project
Version: 1.0.0
"""

import threading
from typing import Callable, Optional, List, Dict
import time
import shutil
from pathlib import Path

from downloaders.yt_dlp_manager import YTDLPManager
from downloaders.metadata_extractor import MetadataExtractor
from downloaders.thumbnail_processor import ThumbnailProcessor


class DownloadWorker(threading.Thread):
    """
    Background worker for downloads and extraction.

    Runs in separate thread to prevent GUI blocking.
    """

    def __init__(self, db, config, logger,
                 mode: str = 'download',
                 url: Optional[str] = None,
                 urls: Optional[List[str]] = None,
                 videos: Optional[List[Dict]] = None,
                 on_complete: Optional[Callable] = None,
                 on_progress: Optional[Callable] = None):
        """
        Initialize download worker.

        Args:
            db: Database instance
            config: ConfigManager instance
            logger: Logger instance
            mode: Operation mode ('extract' or 'download')
            url: Single link (for extract mode)
            urls: Multiple links (for extract mode); takes precedence over url
            videos: List of video dicts (for download mode)
            on_complete: Callback when complete (success: bool, result: dict)
            on_progress: Callback for progress updates (current: int, total: int, message: str)
        """
        super().__init__(daemon=True)

        self.db = db
        self.config = config
        self.logger = logger

        self.mode = mode
        self.url = url
        self.urls = urls or ([url] if url else [])
        self.videos = videos or []

        self.on_complete = on_complete
        self.on_progress = on_progress

        self.cancelled = False

    def run(self):
        """Execute the download operation."""
        try:
            if self.mode == 'extract':
                self._extract_links()
            elif self.mode == 'download':
                self._download_videos()
            else:
                raise ValueError(f"Invalid mode: {self.mode}")

        except Exception as e:
            self.logger.error(f"Download worker error: {e}")
            if self.on_complete:
                self.on_complete(False, {'error': str(e)})

    def _extract_links(self):
        """Extract metadata for one or more links (Stage 1)."""
        if not self.urls:
            raise ValueError("At least one URL is required for extract mode")

        self.logger.info(f"Extracting {len(self.urls)} link(s)")

        extractor = MetadataExtractor(
            database=self.db,
            cookies_file=self.config.cookies_file,
            logger=self.logger
        )

        collections = []
        errors = []
        total = len(self.urls)

        for idx, url in enumerate(self.urls, 1):
            if self.cancelled:
                self.logger.info("Extraction cancelled by user")
                break

            if self.on_progress:
                self.on_progress(idx - 1, total, f"Extracting link {idx}/{total}...")

            success, error, collection_data = extractor.extract_link(url)

            if success:
                collections.append(collection_data)
                self.logger.info(
                    f"Extracted: {collection_data.get('title', 'Unknown')} "
                    f"({collection_data.get('video_count', 0)} item(s))"
                )
            else:
                errors.append({'url': url, 'error': error})
                self.logger.error(f"Extraction failed for {url}: {error}")

            if self.on_progress:
                self.on_progress(idx, total, f"Extracted link {idx}/{total}")

        if not collections:
            message = errors[0]['error'] if errors else "No links could be extracted"
            if self.on_complete:
                self.on_complete(False, {'error': message, 'errors': errors})
            return

        if self.on_complete:
            self.on_complete(True, {
                'collections': collections,
                'count': len(collections),
                'errors': errors,
            })

    def _download_videos(self):
        """Download videos (Stage 2) with resume support and disk space check."""
        if not self.videos:
            raise ValueError("Videos required for download mode")

        # --- Disk space pre-check ---
        try:
            output_dir = Path(str(self.config.raw_download_dir))
            output_dir.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(str(output_dir))
            free_gb = usage.free / (1024**3)
            if free_gb < 5.0:
                self.logger.warning(
                    f"LOW DISK SPACE: {free_gb:.1f}GB free. "
                    f"Recommend 5GB+ for large playlists."
                )
        except Exception as e:
            self.logger.warning(f"Could not check disk space: {e}")

        # --- Resume: filter out already-downloaded videos ---
        remaining = []
        skipped = 0
        for v in self.videos:
            vid = v['video_id']
            if v.get('download_status') == 'downloaded':
                raw_path = v.get('file_path_raw')
                if raw_path and Path(raw_path).exists():
                    skipped += 1
                    continue
            remaining.append(v)

        if skipped > 0:
            self.logger.info(f"Resuming: skipping {skipped} already-downloaded video(s)")

        if not remaining:
            self.logger.info("All videos already downloaded - nothing to do")
            if self.on_complete:
                self.on_complete(True, {'count': 0, 'total': len(self.videos), 'errors': 0, 'skipped': skipped})
            return

        total = len(remaining)
        self.logger.info(f"Downloading {total} videos ({skipped} already done)...")

        try:
            # Initialize download manager
            manager = YTDLPManager(
                cookies_file=self.config.cookies_file,
                sleep_interval=self.config.get_int('Download', 'sleep_interval_min', 2),
                max_sleep_interval=self.config.get_int('Download', 'sleep_interval_max', 5),
                max_retries=self.config.get_int('Download', 'max_retries', 3),
                logger=self.logger
            )

            # Initialize thumbnail processor
            thumb_processor = ThumbnailProcessor(
                logger=self.logger
            )

            success_count = 0
            error_count = 0

            for idx, video in enumerate(remaining, 1):
                if self.cancelled:
                    self.logger.info("Download cancelled by user")
                    break

                video_id = video['video_id']
                title = video.get('title', 'Unknown')

                # Update progress
                if self.on_progress:
                    self.on_progress(idx, total, f"Downloading: {title[:50]}...")

                try:
                    # Mark as in-progress so interrupted downloads are detectable
                    self.db.update_download_status(video_id=video_id, status='downloading')

                    # Download audio (pass the item's real URL so X/Twitter
                    # and other non-YouTube links work)
                    success, error_msg, info = manager.download_audio(
                        video_id=video_id,
                        output_dir=str(self.config.raw_download_dir),
                        url=video.get('url'),
                        source_index=video.get('source_index')
                    )

                    if success:
                        success_count += 1
                        self.logger.info(f"Downloaded: {video_id}")

                        # Update database
                        self.db.update_download_status(
                            video_id=video_id,
                            status='downloaded',
                            file_path=info.get('filename') if info else None
                        )

                        # Download thumbnail
                        try:
                            thumb_url = video.get('thumbnail_url')
                            if thumb_url:
                                thumb_path = self.config.raw_download_dir / f"{video_id}_thumb.jpg"
                                thumb_success, thumb_error = thumb_processor.download_and_process(
                                    thumbnail_url=thumb_url,
                                    output_path=str(thumb_path),
                                    fallback_urls=self._thumbnail_fallbacks(video)
                                )
                                if not thumb_success:
                                    self.logger.warning(f"Thumbnail download failed: {thumb_error}")
                        except Exception as te:
                            self.logger.warning(f"Thumbnail download failed: {te}")

                    else:
                        error_count += 1
                        self.logger.error(f"Download failed for {video_id}: {error_msg}")

                        # Update database with error
                        self.db.update_download_status(
                            video_id=video_id,
                            status='error',
                            error=error_msg
                        )

                except Exception as e:
                    error_count += 1
                    self.logger.error(f"Download error for {video_id}: {e}")

                # Cool-down pause every 50 successful downloads
                if success_count > 0 and success_count % 50 == 0:
                    self.logger.info("Cool-down: pausing 30s after 50 downloads to avoid rate limiting...")
                    time.sleep(30)

            # Final progress
            if self.on_progress:
                self.on_progress(
                    total,
                    total,
                    f"Complete: {success_count} succeeded, {error_count} failed, {skipped} skipped"
                )

            # Complete callback
            if self.on_complete:
                self.on_complete(True, {
                    'count': success_count,
                    'total': total,
                    'errors': error_count,
                    'skipped': skipped
                })

        except Exception as e:
            self.logger.error(f"Download batch failed: {e}")
            if self.on_complete:
                self.on_complete(False, {'error': str(e)})

    @staticmethod
    def _thumbnail_fallbacks(video: Dict) -> List[str]:
        """Known-good thumbnail URLs to try when the extracted one fails.

        YouTube's maxresdefault image does not exist for every video, so
        fall back through the sizes that always exist.
        """
        url = (video.get('url') or '').lower()
        if 'youtu' not in url:
            return []

        video_id = video.get('video_id') or ''
        if not video_id:
            return []

        return [
            f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
            f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
            f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        ]

    def cancel(self):
        """Cancel the download operation."""
        self.cancelled = True
        self.logger.info("Download worker cancellation requested")
