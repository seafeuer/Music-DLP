"""Configuration manager for Music DLP.

Handles loading, saving, and validating configuration from config.ini file
and SQLite database.
"""

import configparser
from pathlib import Path
from typing import Optional, Dict, Any
import logging


class ConfigManager:
    """Configuration manager with INI file and database sync."""

    def __init__(self, config_file: str = "config.ini", db=None):
        """Initialize configuration manager.

        Args:
            config_file: Path to INI configuration file
            db: Database instance for syncing config
        """
        self.config_file = Path(config_file)
        self.db = db
        self.logger = logging.getLogger(__name__)
        self.config = configparser.ConfigParser()

        # Load configuration
        self._load_or_create()

    def _load_or_create(self):
        """Load existing config or create default."""
        if self.config_file.exists():
            self.config.read(self.config_file)
            self.logger.info(f"Loaded config from {self.config_file}")
        else:
            self._create_default()
            self.logger.info(f"Created default config at {self.config_file}")

    def _create_default(self):
        """Create default configuration file."""
        # Appearance
        self.config['Appearance'] = {
            'theme_mode': 'dark',  # dark, light, system
        }

        # Download settings
        self.config['Download'] = {
            'format': '251/bestaudio[ext=opus]/bestaudio[ext=webm]/bestaudio/best',
            'cookies_file': '',
            'sleep_interval_min': '2',
            'sleep_interval_max': '5',
            'max_retries': '3',
        }

        # Output settings
        self.config['Output'] = {
            'mp3_enabled': 'true',
            'm4a_enabled': 'true',
            'mp3_bitrate': '320',
            'm4a_bitrate': '320',
            'normalize_audio': 'true',
            'raw_dir': 'downloads/raw',
            'final_dir': 'downloads/final',
        }

        # Logging
        self.config['Logging'] = {
            'level': 'INFO',  # DEBUG, INFO, WARNING, ERROR
            'save_logs': 'true',
            'log_dir': 'logs',
            'max_log_files': '10',
        }

        # Paths
        self.config['Paths'] = {
            'database': 'music_dlp.db',
            'downloads_raw': 'downloads/raw',
            'downloads_final': 'downloads/final',
            'logs': 'logs',
        }

        self.save()

    def save(self):
        """Save configuration to file."""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                self.config.write(f)
            self.logger.info(f"Saved config to {self.config_file}")

            # Sync to database if available
            if self.db:
                self._sync_to_db()
        except Exception as e:
            self.logger.error(f"Failed to save config: {e}")

    def _sync_to_db(self):
        """Sync configuration to database."""
        if not self.db:
            return

        # Sync key settings to database
        self.db.set_config('theme_mode', self.get('Appearance', 'theme_mode'))
        self.db.set_config('download_format', self.get('Download', 'format'))
        self.db.set_config('mp3_bitrate', self.get('Output', 'mp3_bitrate'))
        self.db.set_config('m4a_bitrate', self.get('Output', 'm4a_bitrate'))
        self.db.set_config('cookies_file', self.get('Download', 'cookies_file'))

    def get(self, section: str, key: str, fallback: Optional[str] = None) -> str:
        """Get configuration value.

        Args:
            section: Section name
            key: Key name
            fallback: Fallback value if not found

        Returns:
            Configuration value
        """
        return self.config.get(section, key, fallback=fallback)

    def get_int(self, section: str, key: str, fallback: int = 0) -> int:
        """Get integer configuration value.

        Args:
            section: Section name
            key: Key name
            fallback: Fallback value if not found

        Returns:
            Integer value
        """
        return self.config.getint(section, key, fallback=fallback)

    def get_bool(self, section: str, key: str, fallback: bool = False) -> bool:
        """Get boolean configuration value.

        Args:
            section: Section name
            key: Key name
            fallback: Fallback value if not found

        Returns:
            Boolean value
        """
        return self.config.getboolean(section, key, fallback=fallback)

    def set(self, section: str, key: str, value: Any):
        """Set configuration value.

        Args:
            section: Section name
            key: Key name
            value: Value to set
        """
        if not self.config.has_section(section):
            self.config.add_section(section)
        self.config.set(section, key, str(value))

    def get_all(self) -> Dict[str, Dict[str, str]]:
        """Get all configuration as nested dictionary.

        Returns:
            Dictionary of {section: {key: value}}
        """
        return {section: dict(self.config.items(section))
                for section in self.config.sections()}

    # Convenience methods for common settings

    @property
    def theme_mode(self) -> str:
        """Get theme mode (dark/light/system)."""
        return self.get('Appearance', 'theme_mode', 'dark')

    @theme_mode.setter
    def theme_mode(self, value: str):
        """Set theme mode."""
        self.set('Appearance', 'theme_mode', value)
        self.save()

    @property
    def download_format(self) -> str:
        """Get yt-dlp download format."""
        return self.get('Download', 'format', '251/bestaudio[ext=opus]/bestaudio[ext=webm]/bestaudio/best')

    @download_format.setter
    def download_format(self, value: str):
        """Set yt-dlp download format."""
        self.set('Download', 'format', value)
        self.save()

    @property
    def cookies_file(self) -> str:
        """Get path to cookies file."""
        return self.get('Download', 'cookies_file', '')

    @cookies_file.setter
    def cookies_file(self, value: str):
        """Set path to cookies file."""
        self.set('Download', 'cookies_file', value)
        self.save()

    @property
    def mp3_bitrate(self) -> str:
        """Get MP3 bitrate."""
        return self.get('Output', 'mp3_bitrate', '320')

    @mp3_bitrate.setter
    def mp3_bitrate(self, value: str):
        """Set MP3 bitrate."""
        self.set('Output', 'mp3_bitrate', value)
        self.save()

    @property
    def m4a_bitrate(self) -> str:
        """Get M4A bitrate."""
        return self.get('Output', 'm4a_bitrate', '320')

    @m4a_bitrate.setter
    def m4a_bitrate(self, value: str):
        """Set M4A bitrate."""
        self.set('Output', 'm4a_bitrate', value)
        self.save()

    @property
    def normalize_audio(self) -> bool:
        """Check if audio normalization is enabled."""
        return self.get_bool('Output', 'normalize_audio', True)

    @normalize_audio.setter
    def normalize_audio(self, value: bool):
        """Set audio normalization."""
        self.set('Output', 'normalize_audio', str(value).lower())
        self.save()

    @property
    def mp3_enabled(self) -> bool:
        """Check if MP3 output is enabled."""
        return self.get_bool('Output', 'mp3_enabled', True)

    @property
    def m4a_enabled(self) -> bool:
        """Check if M4A output is enabled."""
        return self.get_bool('Output', 'm4a_enabled', True)

    @property
    def log_level(self) -> str:
        """Get logging level."""
        return self.get('Logging', 'level', 'INFO')

    @log_level.setter
    def log_level(self, value: str):
        """Set logging level."""
        self.set('Logging', 'level', value)
        self.save()

    @property
    def raw_download_dir(self) -> Path:
        """Get raw downloads directory path."""
        return Path(self.get('Paths', 'downloads_raw', 'downloads/raw'))

    @property
    def final_output_dir(self) -> Path:
        """Get final output directory path."""
        return Path(self.get('Paths', 'downloads_final', 'downloads/final'))

    @property
    def log_dir(self) -> Path:
        """Get logs directory path."""
        return Path(self.get('Logging', 'log_dir', 'logs'))

    def validate(self) -> list:
        """Validate configuration and return list of issues.

        Returns:
            List of validation error messages
        """
        issues = []

        # Check download format
        if not self.download_format:
            issues.append("Download format is empty")

        # Check bitrates
        valid_bitrates = ['128', '192', '256', '320']
        if self.mp3_bitrate not in valid_bitrates:
            issues.append(f"MP3 bitrate must be one of: {', '.join(valid_bitrates)}")
        if self.m4a_bitrate not in valid_bitrates:
            issues.append(f"M4A bitrate must be one of: {', '.join(valid_bitrates)}")

        # Check at least one output format is enabled
        if not self.mp3_enabled and not self.m4a_enabled:
            issues.append("At least one output format (MP3 or M4A) must be enabled")

        return issues

    def ensure_directories(self):
        """Ensure all configured directories exist."""
        dirs = [
            self.raw_download_dir,
            self.final_output_dir,
            self.final_output_dir / "mp3",
            self.final_output_dir / "m4a",
            self.log_dir,
        ]

        for directory in dirs:
            directory.mkdir(parents=True, exist_ok=True)
            self.logger.debug(f"Ensured directory exists: {directory}")
