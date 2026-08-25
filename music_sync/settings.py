import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .constants import DEFAULT_DIR_TEMPLATE, DEFAULT_FILENAME_TEMPLATE, DB_DIRNAME, SETTINGS_FILENAME
from .tidal_cover import DEFAULT_SEARCH_COUNTRIES


@dataclass
class Settings:
    dir_template: str = DEFAULT_DIR_TEMPLATE
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    last_source_root: str = ""
    language: str = "en"
    theme: str = "auto"
    use_libsoxr: bool = False
    track_no_fix: bool = False
    # ISO codes of the regional Tidal catalogues cover lookups query; see
    # tidal_cover.COUNTRY_REGIONS.
    tidal_countries: list[str] = field(default_factory=lambda: list(DEFAULT_SEARCH_COUNTRIES))
    profiles: list[dict] = field(default_factory=list)
    last_profile_name: str = ""
    table_header_state: str = ""
    device_table_header_state: str = ""
    tree_header_state: str = ""
    device_tree_header_state: str = ""

    @staticmethod
    def load(project_root: Path) -> "Settings":
        """Read settings.json, falling back to defaults for anything missing,
        unreadable or of the wrong shape.

        Every value is checked against the type of its dataclass default, and
        each profile against being a named dict: a hand-edited (or truncated)
        file used to load happily here and then crash somewhere far away in
        the GUI, e.g. as `profiles` holding a string that something later
        tried to iterate as dicts."""
        path = Settings._path(project_root)
        if not path.exists():
            return Settings()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return Settings()
        if not isinstance(data, dict):
            return Settings()

        defaults = asdict(Settings())
        known = {
            f.name: data[f.name]
            for f in fields(Settings)
            if f.name in data and isinstance(data[f.name], type(defaults[f.name]))
        }
        settings = Settings(**{**defaults, **known})
        settings.profiles = [
            profile
            for profile in settings.profiles
            if isinstance(profile, dict) and isinstance(profile.get("name"), str)
        ]
        return settings

    def save(self, project_root: Path) -> None:
        """Write settings.json atomically: full contents to a temp file in the
        same directory, then one rename over the real one. A plain write can
        be interrupted (power loss, a full disk) after truncating the file but
        before finishing it, and a half-written settings.json means losing
        every profile and template on the next start."""
        path = self._path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        payload = json.dumps(asdict(self), indent=2, ensure_ascii=False)
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)

    @staticmethod
    def _path(project_root: Path) -> Path:
        return Path(project_root) / DB_DIRNAME / SETTINGS_FILENAME
