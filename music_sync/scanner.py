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

    for index, path in enumerate(files, start=1):
        if should_stop and should_stop():
            stopped = True
            break

        rel_path = str(path.relative_to(root))
        existing_paths.add(rel_path)
        stat = path.stat()

        existing = db.get_by_path(rel_path)
        if existing and existing.size == stat.st_size and existing.mtime == stat.st_mtime:
            scanned.append(existing)
            if progress_callback:
                progress_callback(index, total, path)
            continue

        file_hash = hash_file(path)
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
