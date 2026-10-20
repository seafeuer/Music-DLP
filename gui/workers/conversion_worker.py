"""
Conversion Worker

Background threading worker for:
- FFmpeg audio conversion (Opus → MP3/M4A)
- Metadata embedding
- Thumbnail embedding
- Progress callbacks to GUI
- Resume support (skips already-converted videos)
- Output file validation before marking complete

Author: Music DLP Project
Version: 1.1.0
"""

import threading
import shutil
from typing import Callable, Optional, List, Dict
from pathlib import Path

from converters.ffmpeg_converter import FFmpegConverter
from converters.metadata_embedder import MetadataEmbedder

# Minimum file size (100KB) to consider a conversion valid
MIN_OUTPUT_SIZE_BYTES = 100 * 1024


class ConversionWorker(threading.Thread):
    """
    Background worker for audio conversion.

    Runs in separate thread to prevent GUI blocking.
    """

    def __init__(self, db, config, logger,
                 videos: List[Dict],
                 on_complete: Optional[Callable] = None,
                 on_progress: Optional[Callable] = None):
        """
        Initialize conversion worker.

        Args:
            db: Database instance
            config: ConfigManager instance
            logger: Logger instance
            videos: List of video dicts to convert
            on_complete: Callback when complete (success: bool, result: dict)
            on_progress: Callback for progress updates (current: int, total: int, message: str)
        """
        super().__init__(daemon=True)

        self.db = db
        self.config = config
        self.logger = logger

        self.videos = videos
        self.on_complete = on_complete
        self.on_progress = on_progress

        self.cancelled = False

    def run(self):
        """Execute conversion operation."""
        try:
            self._convert_videos()

        except Exception as e:
            self.logger.error(f"Conversion worker error: {e}")
            if self.on_complete:
                self.on_complete(False, {'error': str(e)})

    def _convert_videos(self):
        """Convert videos to MP3/M4A (Stage 5) with resume and validation."""

        # --- Disk space pre-check ---
        try:
            output_dir = self.config.final_output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(str(output_dir))
            free_gb = usage.free / (1024**3)
            if free_gb < 5.0:
                self.logger.warning(
                    f"LOW DISK SPACE: {free_gb:.1f}GB free. "
                    f"Need ~{len(self.videos) * 0.01:.1f}GB for {len(self.videos)} songs."
                )
        except Exception as e:
            self.logger.warning(f"Could not check disk space: {e}")

        # --- Resume: filter out already-completed videos ---
        remaining = []
        skipped = 0
        for v in self.videos:
            vid = v['video_id']
            metadata = self.db.get_approved_metadata(vid)
            if metadata and metadata.get('conversion_status') == 'completed':
                # Verify output files actually exist
                mp3_path = metadata.get('file_path_mp3')
                m4a_path = metadata.get('file_path_m4a')
                mp3_ok = mp3_path and Path(mp3_path).exists() and Path(mp3_path).stat().st_size > MIN_OUTPUT_SIZE_BYTES
                m4a_ok = m4a_path and Path(m4a_path).exists() and Path(m4a_path).stat().st_size > MIN_OUTPUT_SIZE_BYTES
                if mp3_ok and m4a_ok:
                    skipped += 1
                    continue
                else:
                    # DB says completed but files are missing/corrupt - re-convert
                    self.logger.warning(f"Re-converting {vid}: output files missing or corrupt despite 'completed' status")
            remaining.append(v)

        if skipped > 0:
            self.logger.info(f"Resuming: skipping {skipped} already-converted video(s)")

        if not remaining:
            self.logger.info("All videos already converted - nothing to do")
            if self.on_complete:
                self.on_complete(True, {'count': 0, 'total': len(self.videos), 'errors': 0, 'skipped': skipped})
            return

        total = len(remaining)
        self.logger.info(f"Converting {total} videos ({skipped} already done)...")

        if self.on_progress:
            self.on_progress(0, total, "Initializing converter...")

        try:
            # Initialize converter and embedder
            # Get bitrates from config
            mp3_bitrate = getattr(self.config, 'mp3_bitrate', '320') + 'k'
            m4a_bitrate = getattr(self.config, 'm4a_bitrate', '320') + 'k'
            normalize_audio = getattr(self.config, 'normalize_audio', True)

            converter = FFmpegConverter(
                mp3_bitrate=mp3_bitrate,
                m4a_bitrate=m4a_bitrate,
                normalize_audio=normalize_audio,
                logger=self.logger
            )

            embedder = MetadataEmbedder(
                logger=self.logger
            )

            success_count = 0
            error_count = 0

            for idx, video in enumerate(remaining, 1):
                if self.cancelled:
                    self.logger.info("Conversion cancelled by user")
                    break

                video_id = video['video_id']

                # Use the original title and uploader. If this video has no
                # output record yet, create the internal record that tracks
                # conversion status and output paths.
                metadata = self.db.get_approved_metadata(video_id)

                if not metadata:
                    fallback_title = video.get('title') or video_id
                    fallback_artist = video.get('uploader') or 'Unknown'
                    self.db.add_approved_metadata(
                        video_id=video_id,
                        final_title=fallback_title,
                        final_artist=fallback_artist
                    )
                    metadata = self.db.get_approved_metadata(video_id)

                if not metadata:
                    self.logger.warning(f"Could not create output record for {video_id}, skipping")
                    error_count += 1
                    continue

                title = (video.get('title') or metadata.get('final_title') or video_id).strip()
                artist = (video.get('uploader') or metadata.get('final_artist') or 'Unknown').strip()

                # Update progress
                if self.on_progress:
                    self.on_progress(
                        idx,
                        total,
                        f"Converting: {title[:50]}..."
                    )

                try:
                    # Mark as converting
                    self.db.update_conversion_status(video_id=video_id, status='converting')

                    # Get input file path
                    raw_path = video.get('file_path_raw')
                    if not raw_path:
                        raise FileNotFoundError(
                            f"No downloaded file for {video_id} "
                            f"(download_status: {video.get('download_status', 'unknown')}). "
                            f"Download the audio first."
                        )

                    input_file = Path(raw_path)
                    if not input_file.exists():
                        raise FileNotFoundError(f"Input file not found: {input_file}")

                    # Output file paths - split into format subdirectories
                    output_dir = self.config.final_output_dir
                    mp3_dir = output_dir / "mp3"
                    m4a_dir = output_dir / "m4a"
                    mp3_dir.mkdir(parents=True, exist_ok=True)
                    m4a_dir.mkdir(parents=True, exist_ok=True)

                    # Filename comes from the original title (no AI renaming)
                    safe_filename = self._safe_filename(title)
                    output_mp3 = mp3_dir / f"{safe_filename}.mp3"
                    output_m4a = m4a_dir / f"{safe_filename}.m4a"

                    # Don't overwrite a different video's file that happens to
                    # share the same title.
                    if (self._is_foreign_file(output_mp3, metadata)
                            or self._is_foreign_file(output_m4a, metadata)):
                        safe_filename = f"{safe_filename} [{video_id}]"
                        output_mp3 = mp3_dir / f"{safe_filename}.mp3"
                        output_m4a = m4a_dir / f"{safe_filename}.m4a"

                    # Convert to both MP3 and M4A (measures loudness once)
                    self.logger.info(f"Converting: {output_mp3.name} + {output_m4a.name}")
                    mp3_success, m4a_success, mp3_error, m4a_error = converter.convert_both_formats(
                        input_file=input_file,
                        output_mp3=output_mp3,
                        output_m4a=output_m4a
                    )

                    if not mp3_success:
                        raise Exception(f"MP3 conversion failed: {mp3_error}")

                    if not m4a_success:
                        raise Exception(f"M4A conversion failed: {m4a_error}")

                    # --- Validate output files before marking complete ---
                    if not output_mp3.exists() or output_mp3.stat().st_size < MIN_OUTPUT_SIZE_BYTES:
                        # Clean up partial file
                        if output_mp3.exists():
                            output_mp3.unlink()
                        raise Exception(f"MP3 output missing or too small: {output_mp3}")

                    if not output_m4a.exists() or output_m4a.stat().st_size < MIN_OUTPUT_SIZE_BYTES:
                        if output_m4a.exists():
                            output_m4a.unlink()
                        raise Exception(f"M4A output missing or too small: {output_m4a}")

                    # Prepare metadata for embedding (original title/uploader)
                    embed_metadata = {
                        'title': title,
                        'artist': artist,
                        'album': metadata.get('album'),
                        'tags': metadata.get('tags')
                    }

                    # Get thumbnail path - check database first, then raw dir
                    thumbnail_path = metadata.get('thumbnail_path')
                    if thumbnail_path:
                        thumbnail_path = Path(thumbnail_path)
                        if not thumbnail_path.exists():
                            thumbnail_path = None

                    # Fallback: look for thumbnail in raw download directory
                    if not thumbnail_path:
                        raw_thumb = self.config.raw_download_dir / f"{video_id}_thumb.jpg"
                        if raw_thumb.exists():
                            thumbnail_path = raw_thumb
                            self.logger.debug(f"Found thumbnail in raw dir: {raw_thumb}")
                        else:
                            self.logger.warning(f"No thumbnail found for {video_id}")

                    # Embed metadata in MP3
                    self.logger.info(f"Embedding metadata in MP3")
                    mp3_embed_success, mp3_embed_error = embedder.embed_mp3_metadata(
                        file_path=output_mp3,
                        metadata=embed_metadata,
                        thumbnail_path=thumbnail_path
                    )

                    if not mp3_embed_success:
                        self.logger.warning(f"MP3 metadata embedding failed: {mp3_embed_error}")

                    # Embed metadata in M4A
                    self.logger.info(f"Embedding metadata in M4A")
                    m4a_embed_success, m4a_embed_error = embedder.embed_m4a_metadata(
                        file_path=output_m4a,
                        metadata=embed_metadata,
                        thumbnail_path=thumbnail_path
                    )

                    if not m4a_embed_success:
                        self.logger.warning(f"M4A metadata embedding failed: {m4a_embed_error}")

                    # Update database with output file paths (AFTER validation)
                    self.db.update_conversion_status(
                        video_id=video_id,
                        status='completed',
                        mp3_path=str(output_mp3),
                        m4a_path=str(output_m4a),
                        thumbnail_path=str(thumbnail_path) if thumbnail_path else None
                    )

                    success_count += 1
                    self.logger.info(f"Conversion complete: {video_id}")

                except Exception as e:
                    error_count += 1
                    self.logger.error(f"Conversion error for {video_id}: {e}")

                    # Update database with error
                    try:
                        self.db.update_conversion_status(
                            video_id=video_id,
                            status='error',
                            error=str(e)
                        )
                    except:
                        pass

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
            self.logger.error(f"Conversion batch failed: {e}")
            if self.on_complete:
                self.on_complete(False, {'error': str(e)})

    @staticmethod
    def _safe_filename(name: str) -> str:
        """Make a title safe for Windows/macOS/Linux filesystems.

        Keeps Unicode letters so non-English titles stay intact, and removes
        only characters that are invalid in filenames.
        """
        invalid = '<>:"/\\|?*'
        cleaned = ''.join('_' if c in invalid else c for c in (name or ''))
        cleaned = ''.join(' ' if c in '\r\n\t' else c for c in cleaned)
        cleaned = ''.join(c for c in cleaned if c >= ' ' and c != '\x7f')
        cleaned = ' '.join(cleaned.split())  # collapse whitespace/newlines
        cleaned = cleaned.strip(' .')

        if not cleaned:
            cleaned = 'Untitled'

        # Keep well below the Windows path limit
        return cleaned[:150].strip(' .') or 'Untitled'

    @staticmethod
    def _is_foreign_file(path: Path, metadata: dict) -> bool:
        """True when a file exists but was not produced for this video."""
        if not path.exists():
            return False
        known = {metadata.get('file_path_mp3'), metadata.get('file_path_m4a')}
        return str(path) not in known

    def cancel(self):
        """Cancel the conversion operation."""
        self.cancelled = True
        self.logger.info("Conversion worker cancellation requested")
