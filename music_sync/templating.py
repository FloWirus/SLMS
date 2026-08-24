import re

from .db import Track
from .i18n import tr

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
    value = INVALID_CHARS_RE.sub("", value)
    value = re.sub(r" {2,}", " ", value)
    return value.strip().strip(".") or "_"


def render_template(template: str, track: Track) -> str:
    values = {key: sanitize_component(str(fn(track))) for key, fn in TEMPLATE_FIELDS.items()}
    try:
        return template.format(**values)
    except (KeyError, IndexError) as exc:
        raise ValueError(f"Nieprawidłowy znacznik w szablonie: {exc}") from exc


def validate_template(template: str) -> str | None:
    """An error message if `template` would write outside the sync target,
    or None if it is fine.

    sanitize_component() only cleans the *values* substituted into a
    template -- it can't clean the template itself, which comes straight from
    the settings dialog. A template starting with "/" makes the rendered
    path absolute, and joining an absolute path onto the target root
    silently discards the root ("/media/card" / "/tmp/x" == "/tmp/x"); a
    ".." segment walks out of it the slower way.
    """
    normalized = template.replace("\\", "/")
    if normalized.startswith("/"):
        return tr("error_template_absolute")
    if re.match(r"^[A-Za-z]:", normalized):
        return tr("error_template_absolute")
    if any(part == ".." for part in normalized.split("/")):
        return tr("error_template_parent")
    return None


def _strip_unsafe_segments(relative_path: str) -> str:
    """Drop the segments that would escape the target root: leading empties
    (an absolute path), "." and "..".

    Belt and braces next to validate_template(), which rejects such a
    template at the point it is typed -- this also covers templates already
    saved in settings.json by an earlier version, and anything that only
    turns into a ".." segment after substitution."""
    parts = [part for part in relative_path.replace("\\", "/").split("/") if part not in ("", ".", "..")]
    return "/".join(parts)


def build_relative_target_path(dir_template: str, filename_template: str, track: Track) -> str:
    dir_part = render_template(dir_template, track) if dir_template.strip() else ""
    filename_part = render_template(filename_template, track)
    filename_part = f"{filename_part}.{track.format}"
    combined = f"{dir_part}/{filename_part}" if dir_part else filename_part
    return _strip_unsafe_segments(combined)
