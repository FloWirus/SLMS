import re

from .db import Track

INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

def _zero_pad(value: str, width: int = 2) -> str:
    value = (value or "").strip()
    try:
        return f"{int(value):0{width}d}"
    except ValueError:
        return value


TEMPLATE_FIELDS = {
    "artist": lambda t: t.artist or "Unknown Artist",
    "album": lambda t: t.album or "Unknown Album",
    "title": lambda t: t.title or "Unknown Title",
    "track": lambda t: _zero_pad(t.track_number) if t.track_number else "00",
    "track_total": lambda t: _zero_pad(t.track_total) if t.track_total else "00",
    "disc": lambda t: t.disc_number or "0",
    "year": lambda t: t.year or "0000",
    "genre": lambda t: t.genre or "",
}


def sanitize_component(value: str) -> str:
    value = INVALID_CHARS_RE.sub("_", value)
    return value.strip().strip(".") or "_"


def render_template(template: str, track: Track) -> str:
    values = {key: sanitize_component(str(fn(track))) for key, fn in TEMPLATE_FIELDS.items()}
    try:
        return template.format(**values)
    except (KeyError, IndexError) as exc:
        raise ValueError(f"Nieprawidłowy znacznik w szablonie: {exc}") from exc


def build_relative_target_path(dir_template: str, filename_template: str, track: Track) -> str:
    dir_part = render_template(dir_template, track) if dir_template.strip() else ""
    filename_part = render_template(filename_template, track)
    filename_part = f"{filename_part}.{track.format}"
    if dir_part:
        return f"{dir_part}/{filename_part}"
    return filename_part
