"""
GUI Workers Package

Threading workers for long-running operations:
- DownloadWorker: Background downloads
- ConversionWorker: Background audio conversion

All workers use threading to prevent GUI blocking.

Author: Music DLP Project
Version: 2.0.0
"""

from .download_worker import DownloadWorker
from .conversion_worker import ConversionWorker

__all__ = ['DownloadWorker', 'ConversionWorker']
