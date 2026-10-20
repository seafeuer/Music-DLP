"""
Music DLP - Thumbnail Processor

Download and process YouTube thumbnails to 300x300px square format.

YouTube thumbnails are typically 16:9 aspect ratio (e.g., 1280x720).
For audio metadata embedding, we need square 300x300px JPEG images.

This module:
1. Downloads thumbnail from URL
2. Crops to center square (removes black bars if any)
3. Resizes to exact 300x300px using high-quality LANCZOS resampling
4. Saves as JPEG with 95% quality
"""

from pathlib import Path
from typing import Tuple, Optional, List
import requests
from io import BytesIO
from PIL import Image
from core.logger import setup_logger


class ThumbnailProcessor:
    """Handles thumbnail downloading and processing"""

    def __init__(self, logger=None):
        """
        Initialize thumbnail processor.

        Args:
            logger: Optional logger instance
        """
        self.logger = logger or setup_logger('thumbnail_processor')
        self.logger.info("ThumbnailProcessor initialized")

        # Default settings
        self.output_size = (300, 300)
        self.output_format = 'JPEG'
        self.output_quality = 95
        self.request_timeout = 10  # seconds

    def download_and_process(
        self,
        thumbnail_url: str,
        output_path: str,
        size: Tuple[int, int] = (300, 300),
        fallback_urls: Optional[List[str]] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Download thumbnail from URL and process to square format.

        Args:
            thumbnail_url: URL of the thumbnail image
            output_path: Path to save processed thumbnail (with .jpg extension)
            size: Output size as (width, height) tuple (default: 300x300)
            fallback_urls: Additional URLs to try if the first one fails
                (useful for YouTube, where maxresdefault may not exist)

        Returns:
            Tuple of (success: bool, error_message: str|None)
        """
        if not thumbnail_url and not fallback_urls:
            error_msg = "No thumbnail URL provided"
            self.logger.warning(error_msg)
            return False, error_msg

        candidates = []
        for url in [thumbnail_url, *(fallback_urls or [])]:
            if url and url not in candidates:
                candidates.append(url)

        last_error = None
        for url in candidates:
            success, error = self._download_and_process_one(url, output_path, size)
            if success:
                return True, None
            last_error = error
            self.logger.debug(f"Thumbnail attempt failed ({url}): {error}")

        return False, last_error

    def _download_and_process_one(
        self,
        thumbnail_url: str,
        output_path: str,
        size: Tuple[int, int]
    ) -> Tuple[bool, Optional[str]]:
        """Download and process a single thumbnail URL."""
        self.logger.debug(f"Downloading thumbnail from: {thumbnail_url}")

        try:
            # Download thumbnail
            response = requests.get(thumbnail_url, timeout=self.request_timeout)
            response.raise_for_status()

            # Open image from bytes
            img = Image.open(BytesIO(response.content))

            original_size = img.size
            self.logger.debug(f"Original thumbnail size: {original_size}")

            # Convert to RGB if necessary (some images might be RGBA or other modes)
            if img.mode != 'RGB':
                self.logger.debug(f"Converting from {img.mode} to RGB")
                img = img.convert('RGB')

            # Crop to center square
            img = self._crop_to_center_square(img)

            # Resize to target size
            if img.size != size:
                self.logger.debug(f"Resizing from {img.size} to {size}")
                img = img.resize(size, Image.Resampling.LANCZOS)

            # Ensure output directory exists
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Save as JPEG
            img.save(
                output_path,
                self.output_format,
                quality=self.output_quality,
                optimize=True
            )

            file_size = output_path.stat().st_size
            self.logger.info(
                f"Thumbnail processed successfully: {output_path.name} "
                f"({file_size / 1024:.1f}KB)"
            )

            return True, None

        except requests.RequestException as e:
            error_msg = f"Failed to download thumbnail: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg

        except Image.UnidentifiedImageError as e:
            error_msg = f"Invalid image format: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg

        except Exception as e:
            error_msg = f"Unexpected error processing thumbnail: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            return False, error_msg

    def _crop_to_center_square(self, img: Image.Image) -> Image.Image:
        """
        Crop image to center square.

        For landscape images (width > height):
            - Crop left and right sides
        For portrait images (height > width):
            - Crop top and bottom

        Args:
            img: PIL Image object

        Returns:
            Cropped PIL Image object (square)
        """
        width, height = img.size

        if width == height:
            # Already square
            return img

        if width > height:
            # Landscape: crop sides to make square
            # Calculate crop box: (left, top, right, bottom)
            left = (width - height) // 2
            top = 0
            right = left + height
            bottom = height

            self.logger.debug(f"Cropping landscape {width}x{height} to square {height}x{height}")

        else:
            # Portrait: crop top/bottom to make square
            left = 0
            top = (height - width) // 2
            right = width
            bottom = top + width

            self.logger.debug(f"Cropping portrait {width}x{height} to square {width}x{width}")

        crop_box = (left, top, right, bottom)
        cropped = img.crop(crop_box)

        return cropped

    def process_local_image(
        self,
        input_path: str,
        output_path: str,
        size: Tuple[int, int] = (300, 300)
    ) -> Tuple[bool, Optional[str]]:
        """
        Process an already downloaded local image file.

        Args:
            input_path: Path to input image file
            output_path: Path to save processed thumbnail
            size: Output size as (width, height) tuple (default: 300x300)

        Returns:
            Tuple of (success: bool, error_message: str|None)
        """
        input_path = Path(input_path)

        if not input_path.exists():
            error_msg = f"Input file not found: {input_path}"
            self.logger.error(error_msg)
            return False, error_msg

        self.logger.debug(f"Processing local image: {input_path}")

        try:
            # Open image
            img = Image.open(input_path)

            original_size = img.size
            self.logger.debug(f"Original image size: {original_size}")

            # Convert to RGB if necessary
            if img.mode != 'RGB':
                self.logger.debug(f"Converting from {img.mode} to RGB")
                img = img.convert('RGB')

            # Crop to center square
            img = self._crop_to_center_square(img)

            # Resize to target size
            if img.size != size:
                self.logger.debug(f"Resizing from {img.size} to {size}")
                img = img.resize(size, Image.Resampling.LANCZOS)

            # Ensure output directory exists
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Save as JPEG
            img.save(
                output_path,
                self.output_format,
                quality=self.output_quality,
                optimize=True
            )

            file_size = output_path.stat().st_size
            self.logger.info(
                f"Image processed successfully: {output_path.name} "
                f"({file_size / 1024:.1f}KB)"
            )

            return True, None

        except Image.UnidentifiedImageError as e:
            error_msg = f"Invalid image format: {str(e)}"
            self.logger.error(error_msg)
            return False, error_msg

        except Exception as e:
            error_msg = f"Unexpected error processing image: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            return False, error_msg

    def batch_process(
        self,
        thumbnail_data: list[dict],
        output_dir: str,
        filename_template: str = "{video_id}_thumb.jpg"
    ) -> dict:
        """
        Process multiple thumbnails in batch.

        Args:
            thumbnail_data: List of dicts with 'video_id' and 'thumbnail_url'
            output_dir: Directory to save processed thumbnails
            filename_template: Template for output filenames (default: "{video_id}_thumb.jpg")

        Returns:
            Dict with results: {
                'successful': [video_ids],
                'failed': [(video_id, error_msg), ...],
                'total_size': bytes
            }
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        successful = []
        failed = []
        total_size = 0

        self.logger.info(f"Processing batch of {len(thumbnail_data)} thumbnails")

        for item in thumbnail_data:
            video_id = item.get('video_id')
            thumbnail_url = item.get('thumbnail_url')

            if not video_id or not thumbnail_url:
                self.logger.warning(f"Skipping item with missing data: {item}")
                failed.append((video_id or 'unknown', "Missing video_id or thumbnail_url"))
                continue

            # Build output filename
            filename = filename_template.format(video_id=video_id)
            output_path = output_dir / filename

            # Process thumbnail
            success, error = self.download_and_process(
                thumbnail_url=thumbnail_url,
                output_path=str(output_path)
            )

            if success:
                successful.append(video_id)
                # Add file size to total
                if output_path.exists():
                    total_size += output_path.stat().st_size
            else:
                failed.append((video_id, error))

        self.logger.info(
            f"Batch processing complete: {len(successful)}/{len(thumbnail_data)} successful "
            f"({total_size / 1024:.1f}KB total)"
        )

        if failed:
            self.logger.warning(f"{len(failed)} thumbnails failed:")
            for video_id, error in failed:
                self.logger.warning(f"  - {video_id}: {error}")

        return {
            'successful': successful,
            'failed': failed,
            'total_size': total_size
        }


def get_best_thumbnail_url(thumbnails: list[dict]) -> Optional[str]:
    """
    Get the best quality thumbnail URL from a list of thumbnail dicts.

    YouTube provides multiple thumbnail sizes. This function selects
    the highest quality one available.

    Args:
        thumbnails: List of thumbnail dicts from yt-dlp (each has 'url', 'width', 'height')

    Returns:
        URL of best thumbnail, or None if list is empty
    """
    if not thumbnails:
        return None

    # Sort by resolution (width * height), highest first
    sorted_thumbs = sorted(
        thumbnails,
        key=lambda t: (t.get('width', 0) * t.get('height', 0)),
        reverse=True
    )

    # Return the highest resolution thumbnail URL
    best = sorted_thumbs[0]
    return best.get('url')


def validate_thumbnail_url(url: str) -> bool:
    """
    Validate that a thumbnail URL is accessible.

    Args:
        url: Thumbnail URL to validate

    Returns:
        True if URL is accessible, False otherwise
    """
    if not url:
        return False

    try:
        response = requests.head(url, timeout=5)
        return response.status_code == 200
    except Exception:
        return False
