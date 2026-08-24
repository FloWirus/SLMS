import hashlib
import logging
import os
from collections.abc import Callable, Iterator
from pathlib import Path

from .constants import AUDIO_EXTENSIONS
from .db import MusicDatabase, Track
from .i18n import tr
from . import tags as tagsmod

logger = logging.getLogger(__name__)

HASH_CHUNK_SIZE = 1024 * 1024


def iter_audio_files(root: Path) -> Iterator[Path]:
    # os.walk rather than rglob: it does not follow symlinked directories
    # (followlinks defaults to False), so a link pointing back up the tree --
    # easy to create by accident with a "Music" shortcut inside the library --
    # can not send the scan round in circles, and it hands us its own errors
    # instead of raising out of the generator mid-iteration.
    for dir_path, _dir_names, file_names in os.walk(root, followlinks=False, onerror=_log_walk_error):
        for file_name in file_names:
            if os.path.splitext(file_name)[1].lower() in AUDIO_EXTENSIONS:
                yield Path(dir_path) / file_name


def _log_walk_error(exc: OSError) -> None:
    logger.warning(tr("log_scan_file_error", path=exc.filename, error=exc))


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def _scan_file(path: Path, rel_path: str, db: MusicDatabase, existing: Track | None) -> Track:
    """Bring one file's DB row up to date and return it. Raises OSError if
    the file can't be stat'ed or read -- scan_directory turns that into a
    skipped file rather than a failed scan."""
    stat = path.stat()

    if existing and existing.size == stat.st_size and existing.mtime == stat.st_mtime:
        return existing

    file_hash = hash_file(path)

    # size/mtime can drift across remounts on FAT/exFAT cards even though
    # the file itself hasn't changed (timestamp precision isn't reliably
    # preserved). When the content hash still matches, trust the known-good
    # tags already in the DB instead of re-reading them from a card that
    # may not have fully settled right after being plugged in -- a failed
    # or partial read there would otherwise permanently overwrite good tags
    # with blanks.
    if existing and existing.hash == file_hash:
        existing.size = stat.st_size
        existing.mtime = stat.st_mtime
        db.upsert_track(existing)
        return existing

    tag_values = tagsmod.read_tags(path)

    track = Track(
        id=None,
        path=rel_path,
        filename=path.name,
        hash=file_hash,
        source_hash=file_hash,
        artist=tag_values["artist"],
        album=tag_values["album"],
        title=tag_values["title"],
        track_number=tag_values["track_number"],
        track_total=tag_values["track_total"],
        disc_number=tag_values["disc_number"],
        year=tag_values["year"],
        genre=tag_values["genre"],
        format=path.suffix.lower().lstrip("."),
        size=stat.st_size,
        mtime=stat.st_mtime,
    )
    track.id = db.upsert_track(track)
    return track


def scan_directory(
    root: Path,
    db: MusicDatabase,
    progress_callback: Callable[[int, int, Path], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Track]:
    root = Path(root)
    files = list(iter_audio_files(root))
    total = len(files)
    existing_paths: set[str] = set()
    scanned: list[Track] = []
    stopped = False

    # One query for the whole table instead of a get_by_path() per file, and
    # one commit for the whole scan instead of one per row -- on a library or
    # card with thousands of tracks that is the difference between a scan
    # dominated by SQLite round-trips and one dominated by hashing.
    known_tracks = db.tracks_by_path()

    with db.batch():
        for index, path in enumerate(files, start=1):
            if should_stop and should_stop():
                stopped = True
                break

            rel_path = str(path.relative_to(root))
            # Recorded as still present before anything can fail below: a
            # file that couldn't be read is not a file that was deleted, and
            # remove_missing() would otherwise drop its perfectly good row
            # over a single unreadable byte.
            existing_paths.add(rel_path)
            existing = known_tracks.get(rel_path)

            try:
                track = _scan_file(path, rel_path, db, existing)
            except OSError as exc:
                # One unreadable file (permissions, a broken symlink, a card
                # yanked mid-scan, a file deleted while we walked the tree)
                # must not abort the whole scan and lose every row processed
                # after it. Keep whatever the DB already knows about it.
                logger.warning(tr("log_scan_file_error", path=path, error=exc))
                track = existing

            if track is not None:
                scanned.append(track)

            if progress_callback:
                progress_callback(index, total, path)

    if not stopped:
        db.remove_missing(existing_paths)
    return scanned
