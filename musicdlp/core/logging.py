"""Logging setup and a Qt-safe log handler."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

LOGGER_NAME = "musicdlp"


class LogEmitter(QObject):
    """Emits log records to the UI on the GUI thread.

    Qt delivers cross-thread signals through the receiving object's event
    loop, so worker threads can log without touching widgets.
    """

    message = Signal(str, str)  # level, formatted line


class QtLogHandler(logging.Handler):
    def __init__(self, emitter: LogEmitter) -> None:
        super().__init__()
        self.emitter = emitter
        self.setFormatter(
            logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Only warnings and errors are labelled; ordinary lines read as
            # plain sentences instead of "module: text".
            text = self.format(record)
            if record.levelno >= logging.WARNING:
                text = f"{record.levelname.title()}: {text}"
            self.emitter.message.emit(record.levelname, text)
        except Exception:
            self.handleError(record)


def setup_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    """Configure the application logger once."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    if getattr(logger, "_musicdlp_configured", False):
        return logger

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "musicdlp.log"

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    # stderr is absent in a windowed PyInstaller build.
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(
            logging.Formatter("%(levelname)-8s %(message)s")
        )
        console.setLevel(logging.INFO)
        logger.addHandler(console)

    logger._musicdlp_configured = True  # type: ignore[attr-defined]
    logger.debug("Logging to %s", log_file)
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")
