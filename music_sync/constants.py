import os
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac", ".wma"}

DEFAULT_DIR_TEMPLATE = "{artist}/{album}"
DEFAULT_FILENAME_TEMPLATE = "{track}. {artist} - {title}"

DB_DIRNAME = "music_db"
LIBRARY_DB_FILENAME = "library.db"
DEVICE_DB_FILENAME = "device.db"
SETTINGS_FILENAME = "settings.json"


def app_data_dir() -> Path:
    """Persistent per-user location for the app's own database/settings.

    Not tied to the executable's location, since PyInstaller/AppImage builds
    run from a temporary mountpoint that differs on every launch.
    """
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / "SLMS"
