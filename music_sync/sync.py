import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from .db import MusicDatabase, Track, device_db_path
from .scanner import hash_file
from .templating import build_relative_target_path


class ConflictResolution(Enum):
    OVERWRITE = auto()
    SKIP = auto()


@dataclass
class SyncResult:
    copied: int = 0
    skipped: int = 0
    already_present: int = 0
    errors: list[str] | None = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


ConflictCallback = Callable[[Track, Path], ConflictResolution]
ProgressCallback = Callable[[int, int, Track], None]


def sync_to_device(
    source_root: Path,
    tracks: list[Track],
    device_mountpoint: Path,
    dir_template: str,
    filename_template: str,
    on_conflict: ConflictCallback,
    on_progress: ProgressCallback | None = None,
) -> SyncResult:
    device_mountpoint = Path(device_mountpoint)
    device_db = MusicDatabase(device_db_path(device_mountpoint))
    result = _copy_tracks(
        source_root, tracks, device_mountpoint, device_db, dir_template, filename_template, on_conflict, on_progress
    )
    device_db.close()
    return result


def sync_from_device(
    device_mountpoint: Path,
    tracks: list[Track],
    target_root: Path,
    target_db: MusicDatabase,
    dir_template: str,
    filename_template: str,
    on_conflict: ConflictCallback,
    on_progress: ProgressCallback | None = None,
) -> SyncResult:
    return _copy_tracks(
        device_mountpoint, tracks, target_root, target_db, dir_template, filename_template, on_conflict, on_progress
    )


def _copy_tracks(
    source_root: Path,
    tracks: list[Track],
    target_root: Path,
    target_db: MusicDatabase,
    dir_template: str,
    filename_template: str,
    on_conflict: ConflictCallback,
    on_progress: ProgressCallback | None = None,
) -> SyncResult:
    source_root = Path(source_root)
    target_root = Path(target_root)
    result = SyncResult()

    target_hashes = target_db.hashes()

    total = len(tracks)
    for index, track in enumerate(tracks, start=1):
        if on_progress:
            on_progress(index, total, track)

        if track.hash in target_hashes:
            result.already_present += 1
            continue

        rel_target = build_relative_target_path(dir_template, filename_template, track)
        target_path = target_root / rel_target

        if target_path.exists():
            existing_hash = hash_file(target_path)
            if existing_hash == track.hash:
                _register_track(target_db, track, rel_target, existing_hash)
                target_hashes.add(track.hash)
                result.already_present += 1
                continue
            resolution = on_conflict(track, target_path)
            if resolution == ConflictResolution.SKIP:
                result.skipped += 1
                continue

        try:
            source_path = source_root / track.path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
            _register_track(target_db, track, rel_target, track.hash)
            target_hashes.add(track.hash)
            result.copied += 1
        except OSError as exc:
            result.errors.append(f"{track.path}: {exc}")

    return result


def _register_track(target_db: MusicDatabase, track: Track, rel_target: str, file_hash: str) -> None:
    registered_track = Track(
        id=None,
        path=rel_target,
        filename=Path(rel_target).name,
        hash=file_hash,
        artist=track.artist,
        album=track.album,
        title=track.title,
        track_number=track.track_number,
        year=track.year,
        genre=track.genre,
        format=track.format,
        size=track.size,
        mtime=track.mtime,
    )
    target_db.upsert_track(registered_track)


def delete_from_device(device_mountpoint: Path, device_track: Track) -> None:
    device_mountpoint = Path(device_mountpoint)
    file_path = device_mountpoint / device_track.path
    if file_path.exists():
        file_path.unlink()
    device_db = MusicDatabase(device_db_path(device_mountpoint))
    device_db.delete_by_path(device_track.path)
    device_db.close()
