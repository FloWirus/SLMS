import hashlib
from collections.abc import Callable, Iterator
from pathlib import Path

from .constants import AUDIO_EXTENSIONS
from .db import MusicDatabase, Track
from . import tags as tagsmod

HASH_CHUNK_SIZE = 1024 * 1024


def iter_audio_files(root: Path) -> Iterator[Path]:
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            yield path


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


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
            existing_paths.add(rel_path)
            stat = path.stat()

            existing = known_tracks.get(rel_path)
            if existing and existing.size == stat.st_size and existing.mtime == stat.st_mtime:
                scanned.append(existing)
                if progress_callback:
                    progress_callback(index, total, path)
                continue

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
                scanned.append(existing)
                if progress_callback:
                    progress_callback(index, total, path)
                continue

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
            track_id = db.upsert_track(track)
            track.id = track_id
            scanned.append(track)

            if progress_callback:
                progress_callback(index, total, path)

    if not stopped:
        db.remove_missing(existing_paths)
    return scanned
