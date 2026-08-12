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

from PySide6.QtWidgets import QApplication

from music_sync.gui.main_window import MainWindow
from music_sync.gui.theme import apply_theme, init as init_theme
from music_sync.settings import Settings


def main():
    project_root = Path(__file__).resolve().parent
    app = QApplication(sys.argv)
    app.setApplicationName("SLMS")
    app.setApplicationDisplayName("SLMS")

    init_theme(app)
    apply_theme(app, Settings.load(project_root).theme)

    def _on_system_theme_changed(_scheme):
        if Settings.load(project_root).theme == "auto":
            apply_theme(app, "auto")

    app.styleHints().colorSchemeChanged.connect(_on_system_theme_changed)

    window = MainWindow(project_root)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
