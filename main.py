"""
Music DLP - Media Downloader

Main application entry point. Initializes the database, configuration,
logging, and launches the CustomTkinter GUI.

Supports YouTube playlists/videos, X/Twitter posts, and other
yt-dlp-supported links.

Author: Music DLP Project
Version: 2.0.0
"""

import sys
import os
from pathlib import Path
import subprocess


def get_app_dir() -> Path:
    """Get the application's base directory.

    When running from source: the directory containing main.py
    When running as frozen exe (PyInstaller --onedir): the directory containing the .exe
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).parent


# Set working directory to app dir so all relative paths resolve correctly
app_dir = get_app_dir()
os.chdir(app_dir)

# Add project root to path for imports (needed when running from source)
sys.path.insert(0, str(app_dir))

from core.database import Database
from core.config_manager import ConfigManager
from core.logger import setup_logger
from gui.main_window import MainWindow


def _setup_ffmpeg_path():
    """Add app-adjacent FFmpeg directories to PATH if ffmpeg.exe is found there."""
    import shutil
    for candidate in [app_dir / 'ffmpeg.exe', app_dir / 'ffmpeg' / 'ffmpeg.exe']:
        if candidate.exists():
            ffmpeg_dir = str(candidate.parent)
            if ffmpeg_dir not in os.environ.get('PATH', ''):
                os.environ['PATH'] = ffmpeg_dir + os.pathsep + os.environ.get('PATH', '')
            return
    # Fall through to system PATH - shutil.which will handle it


def check_dependencies():
    """
    Check that all external dependencies are installed.

    Returns:
        tuple: (success: bool, missing: list, message: str)
    """
    missing = []
    messages = []

    # Check FFmpeg - look in app-adjacent dirs first, then PATH
    _setup_ffmpeg_path()

    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode != 0:
            missing.append('ffmpeg')
            messages.append("FFmpeg is installed but not working correctly")
    except FileNotFoundError:
        missing.append('ffmpeg')
        messages.append(
            "FFmpeg not found. Please either:\n"
            "1. Place ffmpeg.exe next to MusicDLP.exe\n"
            "2. Place it in a 'ffmpeg' subfolder\n"
            "3. Install FFmpeg and add to system PATH\n\n"
            "Download from: https://ffmpeg.org/download.html"
        )
    except subprocess.TimeoutExpired:
        missing.append('ffmpeg')
        messages.append("FFmpeg check timed out")
    except Exception as e:
        missing.append('ffmpeg')
        messages.append(f"Error checking FFmpeg: {e}")

    success = len(missing) == 0
    message = "\n\n".join(messages) if messages else ""

    return success, missing, message


def initialize_app():
    """
    Initialize application components.

    Returns:
        tuple: (db: Database, config: ConfigManager, logger: Logger)
    """
    # Setup logger first
    logger = setup_logger('music_dlp', level='INFO')
    logger.info("=" * 60)
    logger.info("Music DLP - Starting Application")
    logger.info("=" * 60)

    # Initialize database
    try:
        db = Database()
        logger.info(f"Database initialized: {db.db_path}")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

    # Initialize config manager
    try:
        config = ConfigManager(db=db)
        logger.info("Configuration loaded")

        # Validate configuration
        errors = config.validate()
        if errors:
            logger.warning(f"Configuration validation warnings: {errors}")
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        raise

    # Ensure directories exist
    try:
        config.ensure_directories()
        logger.info("Application directories verified")
    except Exception as e:
        logger.error(f"Failed to create directories: {e}")
        raise

    return db, config, logger


def main():
    """
    Main application entry point.
    """
    try:
        # Check dependencies
        success, missing, message = check_dependencies()

        if not success:
            # Show error dialog if FFmpeg is missing (critical)
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()  # Hide main window

            messagebox.showerror(
                "Missing Dependencies",
                f"Required dependencies are missing:\n\n{message}"
            )
            root.destroy()
            return 1

        # Show warnings if any (non-critical)
        if message:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning("Setup Warning", message)
            root.destroy()

        # Initialize app components
        db, config, logger = initialize_app()

        # Reset any downloads that were interrupted mid-flight last session
        stale = db.get_videos_by_status('downloading')
        if stale:
            logger.info(f"Resetting {len(stale)} interrupted download(s) from previous session")
            for v in stale:
                vid = v['video_id']
                # Remove partial file if it exists
                raw_path = v.get('file_path_raw')
                if raw_path and Path(raw_path).exists():
                    Path(raw_path).unlink()
                    logger.debug(f"Removed partial file for {vid}")
                db.update_download_status(vid, 'pending')

        # Reset any conversions that were interrupted mid-flight last session
        try:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT video_id FROM approved_metadata WHERE conversion_status = 'converting'"
            )
            stale_conversions = cursor.fetchall()
            if stale_conversions:
                logger.info(f"Resetting {len(stale_conversions)} interrupted conversion(s) from previous session")
                for row in stale_conversions:
                    db.update_conversion_status(row['video_id'], 'pending')
        except Exception as e:
            logger.warning(f"Could not check for stale conversions: {e}")

        logger.info("Launching GUI...")

        # Create and run main window
        app = MainWindow(db=db, config=config, logger=logger)
        app.mainloop()

        logger.info("=" * 60)
        logger.info("Music DLP - Application Closed")
        logger.info("=" * 60)

        # Cleanup
        db.close()

        return 0

    except KeyboardInterrupt:
        print("\nApplication interrupted by user")
        return 130
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"Fatal error: {e}", file=sys.stderr)
        traceback.print_exc()

        # When frozen with no console, write crash log and show dialog
        if getattr(sys, 'frozen', False):
            try:
                crash_log = app_dir / 'crash.log'
                crash_log.write_text(
                    f"Music DLP Crash Report\n{'='*60}\n{tb}",
                    encoding='utf-8'
                )
            except Exception:
                pass
            try:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                messagebox.showerror(
                    "Music DLP - Fatal Error",
                    f"Application crashed:\n{e}\n\nSee crash.log for details."
                )
                root.destroy()
            except Exception:
                pass

        return 1


if __name__ == '__main__':
    sys.exit(main())
