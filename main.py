import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"

try:
    import PySide6  # noqa: F401
except ModuleNotFoundError:
    if VENV_PYTHON.exists() and Path(sys.executable) != VENV_PYTHON:
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    raise SystemExit(
        "PySide6 nie jest zainstalowane i nie znaleziono .venv. "
        "Uruchom: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    )

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from music_sync.constants import DB_DIRNAME, app_data_dir
from music_sync.gui.main_window import MainWindow
from music_sync.gui.theme import apply_theme, init as init_theme
from music_sync.i18n import set_language, tr
from music_sync.settings import Settings


def _migrate_legacy_data_dir(project_root: Path, data_dir: Path) -> None:
    """One-time migration for installs that used to store music_db/ next to
    the script — not safe for AppImage/PyInstaller builds, whose executable
    location is a fresh temporary mountpoint on every launch."""
    old_dir = project_root / DB_DIRNAME
    new_dir = data_dir / DB_DIRNAME
    if old_dir.is_dir() and not new_dir.exists():
        import shutil

        data_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_dir), str(new_dir))


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def main():
    _setup_logging()
    logger = logging.getLogger(__name__)

    project_root = Path(__file__).resolve().parent
    data_dir = app_data_dir()
    _migrate_legacy_data_dir(project_root, data_dir)

    # Set before the first log call so even this startup message respects the
    # saved language (MainWindow re-applies it later; it's the same value).
    set_language(Settings.load(data_dir).language)
    logger.info(tr("log_starting", data_dir=data_dir))

    app = QApplication(sys.argv)
    app.setApplicationName("SLMS")
    app.setApplicationDisplayName("SLMS")

    icon_path = PROJECT_ROOT / "packaging" / "icon.png"
    if icon_path.is_file():
        # Without this, running from source (as opposed to the AppImage,
        # which carries its own .desktop/icon association) leaves the window
        # with no icon at all, so the window manager's taskbar/alt-tab shows
        # a generic placeholder instead of SLMS's own icon.
        app.setWindowIcon(QIcon(str(icon_path)))

    init_theme(app)
    apply_theme(app, Settings.load(data_dir).theme)

    def _on_system_theme_changed(_scheme):
        if Settings.load(data_dir).theme == "auto":
            apply_theme(app, "auto")

    app.styleHints().colorSchemeChanged.connect(_on_system_theme_changed)

    window = MainWindow(data_dir)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
