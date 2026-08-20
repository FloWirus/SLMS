import logging
from pathlib import Path

from . import tags as tagsmod

logger = logging.getLogger(__name__)

LOOSE_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif"}
# Names that mean "this is the album's artwork" by convention, in priority
# order. Checked before falling back to "the directory holds exactly one
# image", so an album with front.jpg + back.jpg still resolves correctly.
PREFERRED_COVER_STEMS = ("cover", "folder", "front", "album", "albumart", "artwork")


def find_loose_cover(album_dir: Path) -> Path | None:
    """Locate an album's loose cover image file -- artwork sitting in the
    album directory itself (cover.jpg, folder.png, ...) rather than embedded
    in a track's tags.

    Read-only: the file stays exactly where the user put it, since that is
    what file managers and most players read from. `[Covers]/` holds only
    files this app produces, i.e. resized output.
    """
    try:
        images = [p for p in album_dir.iterdir() if p.is_file() and p.suffix.lower() in LOOSE_COVER_EXTENSIONS]
    except OSError as exc:
        logger.warning("Failed to list %s while looking for a loose cover: %s", album_dir, exc)
        return None

    for stem in PREFERRED_COVER_STEMS:
        match = next((p for p in images if p.stem.lower() == stem), None)
        if match is not None:
            return match

    # No conventional name: a lone image in an album directory is the cover
    # often enough to use, but several unnamed ones are ambiguous (scans,
    # booklet pages) and are better left alone than guessed at.
    return images[0] if len(images) == 1 else None


def read_loose_cover(album_dir: Path) -> tuple[bytes, str] | None:
    """The album's loose cover as (bytes, mime), or None if there isn't one
    or it can't be read."""
    path = find_loose_cover(album_dir)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError as exc:
        logger.warning("Failed to read loose cover %s: %s", path, exc)
        return None
    if not data:
        return None
    # Sniffed from the bytes rather than the extension: the resize step
    # re-encodes to exactly one of these two formats, so a webp/gif/bmp
    # source is correctly treated as "not PNG" and comes out as JPEG.
    return data, tagsmod.sniff_image_mime(data)
