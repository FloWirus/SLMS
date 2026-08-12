import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .constants import DEFAULT_DIR_TEMPLATE, DEFAULT_FILENAME_TEMPLATE, DB_DIRNAME, SETTINGS_FILENAME


@dataclass
class Settings:
    dir_template: str = DEFAULT_DIR_TEMPLATE
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    last_source_root: str = ""
    language: str = "en"
    theme: str = "auto"

    @staticmethod
    def load(project_root: Path) -> "Settings":
        path = Settings._path(project_root)
        if not path.exists():
            return Settings()
        try:
            data = json.loads(path.read_text())
            return Settings(**{**asdict(Settings()), **data})
        except (json.JSONDecodeError, TypeError):
            return Settings()

    def save(self, project_root: Path) -> None:
        path = self._path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @staticmethod
    def _path(project_root: Path) -> Path:
        return Path(project_root) / DB_DIRNAME / SETTINGS_FILENAME
