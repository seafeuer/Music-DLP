"""Comprehensive logging setup for Music DLP.

Provides colored console output and file logging with rotation.
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False


class ColoredFormatter(logging.Formatter):
    """Custom formatter with color-coded log levels."""

    COLORS = {
        'DEBUG': Fore.CYAN if COLORAMA_AVAILABLE else '',
        'INFO': Fore.GREEN if COLORAMA_AVAILABLE else '',
        'WARNING': Fore.YELLOW if COLORAMA_AVAILABLE else '',
        'ERROR': Fore.RED if COLORAMA_AVAILABLE else '',
        'CRITICAL': Fore.RED + Style.BRIGHT if COLORAMA_AVAILABLE else '',
    }

    RESET = Style.RESET_ALL if COLORAMA_AVAILABLE else ''

    def format(self, record):
        """Format log record with colors."""
        # Add color to level name
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{levelname}{self.RESET}"

        # Format the message
        formatted = super().format(record)

        # Reset levelname for file logging
        record.levelname = levelname

        return formatted


def setup_logger(
    name: str = 'music_dlp',
    level: str = 'INFO',
    log_dir: Optional[Path] = None,
    console: bool = True,
    file_logging: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5
) -> logging.Logger:
    """Setup comprehensive logging with console and file handlers.

    Args:
        name: Logger name
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files
        console: Enable console logging
        file_logging: Enable file logging
        max_bytes: Maximum size per log file
        backup_count: Number of backup files to keep

    Returns:
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Console handler with colors
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_formatter = ColoredFormatter(
            fmt='[%(levelname)s] %(asctime)s - %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    # File handler with rotation
    if file_logging:
        if log_dir is None:
            log_dir = Path('logs')

        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Create log file with timestamp
        timestamp = datetime.now().strftime('%Y%m%d')
        log_file = log_dir / f'music_dlp_{timestamp}.log'

        file_handler = RotatingFileHandler(
            filename=log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            fmt='[%(levelname)s] %(asctime)s - %(name)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        logger.info(f"Logging to file: {log_file}")

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get logger instance by name.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


# Create convenience logging functions for GUI

class GUILogHandler(logging.Handler):
    """Custom handler that can emit logs to GUI components."""

    def __init__(self, callback=None):
        """Initialize GUI log handler.

        Args:
            callback: Function to call with formatted log message
        """
        super().__init__()
        self.callback = callback
        self.setFormatter(logging.Formatter(
            fmt='[%(levelname)s] %(asctime)s - %(message)s',
            datefmt='%H:%M:%S'
        ))

    def emit(self, record):
        """Emit log record to callback.

        Only the message is passed; the log panel adds its own level and
        timestamp so the display does not show them twice.
        """
        if self.callback:
            try:
                self.callback(record.levelname, record.getMessage())
            except Exception:
                self.handleError(record)


def add_gui_handler(logger: logging.Logger, callback):
    """Add GUI handler to logger.

    Args:
        logger: Logger instance
        callback: Function to call with (message, level) when log is emitted
    """
    gui_handler = GUILogHandler(callback)
    gui_handler.setLevel(logging.DEBUG)
    logger.addHandler(gui_handler)
    return gui_handler
