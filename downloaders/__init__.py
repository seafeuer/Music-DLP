"""
Music DLP - Downloaders Package

This package handles media downloads and metadata extraction using yt-dlp.

Modules:
    yt_dlp_manager: Core yt-dlp wrapper with bot detection avoidance
    metadata_extractor: Extract playlist/item metadata without downloading
    thumbnail_processor: Download and process thumbnails to 300x300px
    url_utils: Classify and split YouTube, X/Twitter, and other links
"""

from .yt_dlp_manager import YTDLPManager
from .metadata_extractor import MetadataExtractor
from .thumbnail_processor import ThumbnailProcessor
from .url_utils import LinkInfo, classify_url, split_links

__all__ = [
    'YTDLPManager',
    'MetadataExtractor',
    'ThumbnailProcessor',
    'LinkInfo',
    'classify_url',
    'split_links',
]
