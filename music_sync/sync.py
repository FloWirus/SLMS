import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from . import tags as tagsmod
from .converter import ConversionSettings, CoverResizeSettings, convert_file, decide_conversion
from .cover_utils import resize_cover_bytes
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
    conversion: ConversionSettings | None = None,
    cover_resize: CoverResizeSettings | None = None,
    track_no_fix: bool = False,
) -> SyncResult:
    device_mountpoint = Path(device_mountpoint)
    device_db = MusicDatabase(device_db_path(device_mountpoint))
    result = _copy_tracks(
        source_root,
        tracks,
        device_mountpoint,
        device_db,
        dir_template,
        filename_template,
        on_conflict,
        on_progress,
        conversion,
        cover_resize,
        track_no_fix,
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
    conversion: ConversionSettings | None = None,
    cover_resize: CoverResizeSettings | None = None,
    track_no_fix: bool = False,
) -> SyncResult:
    source_root = Path(source_root)
    target_root = Path(target_root)
    result = SyncResult()

    target_source_hashes = target_db.source_hashes()

    total = len(tracks)
    for index, track in enumerate(tracks, start=1):
        if on_progress:
            on_progress(index, total, track)

        if track.hash in target_source_hashes:
            result.already_present += 1
            continue

        source_path = source_root / track.path
        spec = decide_conversion(track, source_path, conversion.target_key) if conversion else None

        rel_target = build_relative_target_path(dir_template, filename_template, track)
        if spec is not None:
            rel_target = str(Path(rel_target).with_suffix(f".{spec.extension}"))
        target_path = target_root / rel_target

        if target_path.exists():
            existing_entry = target_db.get_by_path(rel_target)
            if existing_entry and existing_entry.source_hash == track.hash:
                target_source_hashes.add(track.hash)
                result.already_present += 1
                continue
            resolution = on_conflict(track, target_path)
            if resolution == ConflictResolution.SKIP:
                result.skipped += 1
                continue

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if spec is not None:
                convert_file(source_path, target_path, spec, conversion.use_libsoxr)
                file_format = spec.extension
            else:
                shutil.copy2(source_path, target_path)
                file_format = track.format

            cover_changed = _resize_target_cover(target_path, cover_resize) if cover_resize is not None else False
            track_no_fixed = _fix_target_track_number(target_path) if track_no_fix else False
            file_hash = (
                hash_file(target_path) if (spec is not None or cover_changed or track_no_fixed) else track.hash
            )
            target_stat = target_path.stat()

            _register_track(
                target_db, track, rel_target, file_hash, track.hash, file_format, target_stat.st_size, target_stat.st_mtime
            )
            target_source_hashes.add(track.hash)
            result.copied += 1
        except (OSError, subprocess.CalledProcessError) as exc:
            result.errors.append(f"{track.path}: {exc}")

    return result


def _resize_target_cover(target_path: Path, cover_resize: CoverResizeSettings) -> bool:
    try:
        cover_bytes = tagsmod.read_cover_art(target_path)
        if not cover_bytes:
            return False
        mime = tagsmod.sniff_image_mime(cover_bytes)
        resized_bytes = resize_cover_bytes(cover_bytes, mime, cover_resize.max_size, cover_resize.dpi)
        tagsmod.write_cover_art(target_path, resized_bytes, mime)
        return True
    except Exception:
        return False


def _fix_target_track_number(target_path: Path) -> bool:
    try:
        current = tagsmod.read_tags(target_path)
        fixed = tagsmod.fix_track_number(current["track_number"])
        if fixed == current["track_number"]:
            return False
        tagsmod.write_tags(target_path, {"track_number": fixed, "track_total": current["track_total"]})
        return True
    except Exception:
        return False


def _register_track(
    target_db: MusicDatabase,
    track: Track,
    rel_target: str,
    file_hash: str,
    source_hash: str,
    file_format: str,
    file_size: int,
    file_mtime: float,
) -> None:
    registered_track = Track(
        id=None,
        path=rel_target,
        filename=Path(rel_target).name,
        hash=file_hash,
        source_hash=source_hash,
        artist=track.artist,
        album=track.album,
        title=track.title,
        track_number=track.track_number,
        year=track.year,
        genre=track.genre,
        format=file_format,
        size=file_size,
        mtime=file_mtime,
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
