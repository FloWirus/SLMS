import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from . import tags as tagsmod
from .converter import ConversionSettings, CoverResizeSettings, convert_file, decide_conversion
from .cover_cache import CoverCache, covers_db_path, hash_cover
from .cover_utils import resize_cover_bytes
from .db import MusicDatabase, Track, device_db_path
from .i18n import tr
from .scanner import hash_file, scan_directory
from .templating import build_relative_target_path

logger = logging.getLogger(__name__)


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
ScanProgressCallback = Callable[[int, int, Path], None]
# index, total tracks, track, converting, bytes_copied, total_bytes.
# For a converted file this fires once with converting=True and the byte
# fields left None (conversion runs as one blocking ffmpeg call, no byte-level
# progress available). For a plain copy it fires once at the start the same
# way, then repeatedly as bytes are copied with converting=False and real figures.
TransferProgressCallback = Callable[[int, int, Track, bool, int | None, int | None], None]

COPY_CHUNK_SIZE = 256 * 1024
COPY_FSYNC_INTERVAL = 0.15
# Suffix for the in-progress file written on the target before it's renamed to
# its real name. Deliberately not an audio extension so a leftover one (e.g.
# after a crash or pulled card) is invisible to scan_directory/iter_audio_files
# and never mistaken for a real track.
TARGET_TEMP_SUFFIX = ".musicsync-tmp"


def _fsync_path(path: Path) -> None:
    fd = os.open(str(path), os.O_RDWR if os.name != "nt" else os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    # Best-effort: makes the rename below durable against a crash/power loss
    # right after it happens. Not supported everywhere (e.g. some FAT/exFAT
    # drivers, Windows) so failures here are not fatal -- the rename itself
    # already lands atomically, this only affects worst-case crash recovery.
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _write_to_target_atomically(
    source: Path, target: Path, on_chunk: Callable[[int, int], None] | None
) -> None:
    # Never write partial data at the real destination path: stage it under a
    # temp name in the same directory (so the rename is a same-filesystem,
    # atomic operation) and only rename it into place once fully written and
    # fsynced. A crash/pulled card mid-write leaves only the temp file behind,
    # never a half-written file at `target` that could be mistaken for good.
    tmp_target = target.with_name(target.name + TARGET_TEMP_SUFFIX)
    if tmp_target.exists():
        tmp_target.unlink()
    _copy_file_with_progress(source, tmp_target, on_chunk)
    os.replace(tmp_target, target)
    _fsync_dir(target.parent)


def _cleanup_stray_temp_files(root: Path) -> None:
    for stray in root.rglob(f"*{TARGET_TEMP_SUFFIX}"):
        try:
            stray.unlink()
        except OSError:
            logger.warning(tr("log_sync_stray_temp_cleanup_failed", path=stray))


def _copy_file_with_progress(source: Path, target: Path, on_chunk: Callable[[int, int], None] | None) -> None:
    total = source.stat().st_size
    if on_chunk is None or total == 0:
        shutil.copy2(source, target)
        _fsync_path(target)
        return

    copied = 0
    last_fsync_time = time.monotonic()
    with open(source, "rb") as src, open(target, "wb") as dst:
        while True:
            chunk = src.read(COPY_CHUNK_SIZE)
            if not chunk:
                break
            dst.write(chunk)
            copied += len(chunk)
            now = time.monotonic()
            if now - last_fsync_time >= COPY_FSYNC_INTERVAL or copied == total:
                # Periodically force these bytes out of the OS page cache and
                # onto the device as we go, rather than leaving it all
                # buffered until the file is closed -- limits how much data
                # could be lost if the card is pulled mid-copy. Throttled by
                # time since it's a slow, blocking syscall on removable
                # media -- kept separate from the on_chunk() progress report
                # below, which fires every chunk so the UI shows real,
                # granular progress even for small files that finish in a
                # single fsync interval.
                dst.flush()
                os.fsync(dst.fileno())
                last_fsync_time = now
            on_chunk(copied, total)
    shutil.copystat(source, target)


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
    on_scan_progress: ScanProgressCallback | None = None,
    on_transfer_progress: TransferProgressCallback | None = None,
    force: bool = False,
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
        on_scan_progress,
        on_transfer_progress,
        force,
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
    on_scan_progress: ScanProgressCallback | None = None,
    on_transfer_progress: TransferProgressCallback | None = None,
) -> SyncResult:
    return _copy_tracks(
        device_mountpoint,
        tracks,
        target_root,
        target_db,
        dir_template,
        filename_template,
        on_conflict,
        on_progress,
        on_scan_progress=on_scan_progress,
        on_transfer_progress=on_transfer_progress,
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
    on_scan_progress: ScanProgressCallback | None = None,
    on_transfer_progress: TransferProgressCallback | None = None,
    force: bool = False,
) -> SyncResult:
    source_root = Path(source_root)
    target_root = Path(target_root)
    result = SyncResult()

    logger.info(tr("log_sync_start", count=len(tracks), target=target_root))

    # Files can be deleted from the target outside the app; drop their stale
    # DB rows so they aren't mistaken for "already synced". Reports progress
    # separately from on_progress since this scans the whole target, not the
    # (usually smaller) list of tracks being synced — without this callback
    # the GUI would sit unresponsive for however long this scan takes.
    scan_directory(target_root, target_db, progress_callback=on_scan_progress)
    _cleanup_stray_temp_files(target_root)

    target_source_hashes = target_db.source_hashes()

    # Only relevant for PC -> device syncs (sync_from_device never passes
    # cover_resize): resized cover output doesn't depend on the target, so
    # the cache lives next to the PC library, keyed by source cover hash,
    # and is shared across tracks/albums/repeat syncs/devices.
    cover_cache = CoverCache(source_root, covers_db_path(source_root)) if cover_resize is not None else None

    total = len(tracks)
    for index, track in enumerate(tracks, start=1):
        if on_progress:
            on_progress(index, total, track)

        if not force and track.hash in target_source_hashes:
            existing_entry = target_db.get_by_source_hash(track.hash)
            existing_path = target_root / existing_entry.path if existing_entry else None
            if existing_entry and existing_path.exists() and existing_path.stat().st_size > 0:
                result.already_present += 1
                continue
            # Registered but missing, or present as a zero-byte file (e.g.
            # deleted mid-sync after the rescan above, or left truncated by a
            # write that never made it to the device before it was removed):
            # drop the stale row and fall through to re-copy.
            target_source_hashes.discard(track.hash)
            if existing_entry:
                target_db.delete_by_path(existing_entry.path)

        source_path = source_root / track.path
        spec = decide_conversion(track, source_path, conversion.target_key) if conversion else None

        rel_target = build_relative_target_path(dir_template, filename_template, track)
        if spec is not None:
            rel_target = str(Path(rel_target).with_suffix(f".{spec.extension}"))
        target_path = target_root / rel_target

        if target_path.exists() and not force:
            existing_entry = target_db.get_by_path(rel_target)
            if existing_entry and existing_entry.source_hash == track.hash:
                target_source_hashes.add(track.hash)
                result.already_present += 1
                continue
            resolution = on_conflict(track, target_path)
            if resolution == ConflictResolution.SKIP:
                logger.info(tr("log_sync_skip_conflict", path=track.path))
                result.skipped += 1
                continue

        needs_staging = spec is not None or cover_resize is not None or track_no_fix

        if on_transfer_progress and spec is not None:
            on_transfer_progress(index, total, track, True, None, None)

        def _on_chunk(copied: int, total_bytes: int) -> None:
            on_transfer_progress(index, total, track, False, copied, total_bytes)

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if needs_staging:
                # Do all mutation (convert / resize cover / fix track number) on a
                # temp file on local disk first -- these steps read-modify-write
                # the file repeatedly, and the device (SD card / MTP mount) has
                # much higher access latency than local SSD. Only the finished,
                # ready-to-go file is written to the device, in one streamed pass.
                with tempfile.TemporaryDirectory(prefix="music_sync_") as tmp_dir_name:
                    file_format = spec.extension if spec is not None else track.format
                    local_path = Path(tmp_dir_name) / f"staged.{file_format}"

                    if spec is not None:
                        convert_file(source_path, local_path, spec, conversion.use_libsoxr)
                    else:
                        shutil.copy2(source_path, local_path)

                    cover_changed = (
                        _resize_target_cover(local_path, cover_resize, cover_cache, source_path.parent)
                        if cover_resize is not None
                        else False
                    )
                    track_no_fixed = _fix_target_track_number(local_path) if track_no_fix else False
                    file_hash = (
                        hash_file(local_path) if (spec is not None or cover_changed or track_no_fixed) else track.hash
                    )

                    if on_transfer_progress:
                        on_transfer_progress(index, total, track, False, None, None)
                    _write_to_target_atomically(local_path, target_path, _on_chunk if on_transfer_progress else None)
            else:
                if on_transfer_progress:
                    on_transfer_progress(index, total, track, False, None, None)
                _write_to_target_atomically(source_path, target_path, _on_chunk if on_transfer_progress else None)
                file_format = track.format
                file_hash = track.hash

            target_stat = target_path.stat()

            _register_track(
                target_db, track, rel_target, file_hash, track.hash, file_format, target_stat.st_size, target_stat.st_mtime
            )
            target_source_hashes.add(track.hash)
            result.copied += 1
            logger.info(tr("log_sync_copied", index=index, total=total, source=track.path, target=rel_target))
        except (OSError, subprocess.CalledProcessError) as exc:
            logger.error(tr("log_sync_copy_error", path=track.path, error=exc))
            result.errors.append(f"{track.path}: {exc}")

    if cover_cache is not None:
        cover_cache.close()

    logger.info(
        tr(
            "log_sync_done",
            copied=result.copied,
            skipped=result.skipped,
            present=result.already_present,
            errors=len(result.errors),
        )
    )
    return result


def _resize_target_cover(
    target_path: Path,
    cover_resize: CoverResizeSettings,
    cover_cache: CoverCache | None,
    album_dir: Path,
) -> bool:
    try:
        cover_bytes = tagsmod.read_cover_art(target_path)
        if not cover_bytes:
            return False
        mime = tagsmod.sniff_image_mime(cover_bytes)

        cached = None
        source_hash = None
        if cover_cache is not None:
            source_hash = hash_cover(cover_bytes)
            cached = cover_cache.get(source_hash, cover_resize.max_size, cover_resize.dpi)

        if cached is not None:
            resized_bytes, mime = cached
        else:
            resized_bytes = resize_cover_bytes(cover_bytes, mime, cover_resize.max_size, cover_resize.dpi)
            if cover_cache is not None:
                cover_cache.put(source_hash, cover_resize.max_size, cover_resize.dpi, mime, resized_bytes, album_dir)

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


def remove_empty_parent_dirs(start_dir: Path, root: Path) -> None:
    """Remove start_dir and any of its now-empty ancestors, stopping at (and
    never removing) root itself."""
    root = root.resolve()
    current = start_dir.resolve()
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def delete_from_device(device_mountpoint: Path, device_track: Track) -> None:
    device_mountpoint = Path(device_mountpoint)
    file_path = device_mountpoint / device_track.path
    if file_path.exists():
        file_path.unlink()
        remove_empty_parent_dirs(file_path.parent, device_mountpoint)
    device_db = MusicDatabase(device_db_path(device_mountpoint))
    device_db.delete_by_path(device_track.path)
    device_db.close()
    logger.info(tr("log_deleted_from_device", path=device_track.path))
