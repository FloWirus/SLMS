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
from .album_covers import read_loose_cover
from .constants import AUDIO_EXTENSIONS
from .converter import ConversionSettings, CoverResizeSettings, convert_file, decide_conversion
from .cover_cache import CoverCache, hash_cover
from .cover_utils import read_image_info, resize_cover_bytes
from .db import MusicDatabase, Track, device_db_path
from .i18n import tr
from .scanner import hash_file
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
    duplicates_removed: int = 0
    errors: list[str] | None = None
    cancelled: bool = False

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


ConflictCallback = Callable[[Track, Path], ConflictResolution]
ProgressCallback = Callable[[int, int, Track], None]
ScanProgressCallback = Callable[[int, int, Path], None]
ShouldStopCallback = Callable[[], bool]
DeleteProgressCallback = Callable[[int, int, Track], None]

COPY_CHUNK_SIZE = 256 * 1024
COPY_FSYNC_INTERVAL = 0.15
# Suffix for the in-progress file written on the target before it's renamed to
# its real name. Deliberately not an audio extension so a leftover one (e.g.
# after a crash or pulled card) is invisible to scan_directory/iter_audio_files
# and never mistaken for a real track.
TARGET_TEMP_SUFFIX = ".musicsync-tmp"


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


def _write_to_target_atomically(source: Path, target: Path) -> None:
    # Never write partial data at the real destination path: stage it under a
    # temp name in the same directory (so the rename is a same-filesystem,
    # atomic operation) and only rename it into place once fully written and
    # fsynced. A crash/pulled card mid-write leaves only the temp file behind,
    # never a half-written file at `target` that could be mistaken for good.
    tmp_target = target.with_name(target.name + TARGET_TEMP_SUFFIX)
    if tmp_target.exists():
        tmp_target.unlink()
    _copy_file_durably(source, tmp_target)
    os.replace(tmp_target, target)
    _fsync_dir(target.parent)


def _is_present_on_target(path: Path) -> bool:
    """Whether the target already holds a usable copy: the file exists and
    isn't empty. Reads the size through one stat() with its own error
    handling -- the card can be pulled between the check and the read, and an
    OSError here is a "not present", not a failed sync."""
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _cleanup_stray_temp_files(root: Path) -> None:
    for stray in root.rglob(f"*{TARGET_TEMP_SUFFIX}"):
        try:
            stray.unlink()
        except OSError:
            logger.warning(tr("log_sync_stray_temp_cleanup_failed", path=stray))


def _copy_file_durably(source: Path, target: Path) -> None:
    """Copy source to target in chunks, periodically flushing and fsyncing
    what has been written so far out of the OS page cache and onto the
    device. There is no per-file progress UI for this (deliberately -- one
    progress bar for the whole sync is enough), but the durability behaviour
    below is unconditional: it is what limits how much data could be lost if
    a card is pulled mid-copy, and must run on every copy regardless of
    whether anything is watching progress."""
    total = source.stat().st_size
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
                dst.flush()
                os.fsync(dst.fileno())
                last_fsync_time = now
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
    force: bool = False,
    should_stop: ShouldStopCallback | None = None,
) -> SyncResult:
    device_mountpoint = Path(device_mountpoint)
    device_db = MusicDatabase(device_db_path(device_mountpoint))
    try:
        return _copy_tracks(
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
            force,
            should_stop,
        )
    finally:
        # In a finally, not after the call: a sync that dies half-way (a card
        # pulled mid-copy) must still release the sqlite handle, or the next
        # attempt opens a second connection to a database the first one still
        # has open on removable media.
        device_db.close()


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
    should_stop: ShouldStopCallback | None = None,
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
        should_stop=should_stop,
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
    force: bool = False,
    should_stop: ShouldStopCallback | None = None,
) -> SyncResult:
    source_root = Path(source_root)
    target_root = Path(target_root)
    result = SyncResult()

    logger.info(tr("log_sync_start", count=len(tracks), target=target_root))

    # Files can be deleted from the target outside the app; drop their stale
    # DB rows so they aren't mistaken for "already synced". Reports progress
    # separately from on_progress since this walks the whole target database,
    # not the (usually smaller) list of tracks being synced.
    _prune_missing_targets(target_root, target_db, on_scan_progress, should_stop)
    if should_stop and should_stop():
        result.cancelled = True
        return result
    _cleanup_stray_temp_files(target_root)

    target_source_hashes = target_db.source_hashes()

    # Only relevant for PC -> device syncs (sync_from_device never passes
    # cover_resize). Keyed by source cover hash and kept in the app's own
    # data directory, so it is shared across tracks, albums, repeat syncs
    # and devices -- see CoverCache.
    cover_cache = CoverCache() if cover_resize is not None else None
    # Which cover to resize from is picked per album (best quality found
    # across all of the album's tracks' own tags plus any loose [Covers]
    # file), not just the one track currently being written -- cache that
    # choice so it's only computed once per album instead of once per track.
    best_album_cover = _LastAlbumCoverCache()

    try:
        total = len(tracks)
        for index, track in enumerate(tracks, start=1):
            if should_stop and should_stop():
                result.cancelled = True
                break

            if on_progress:
                on_progress(index, total, track)

            if not force and track.hash in target_source_hashes:
                existing_entry = target_db.get_by_source_hash(track.hash)
                if existing_entry and _is_present_on_target(target_root / existing_entry.path):
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
                            _resize_target_cover(local_path, cover_resize, cover_cache, source_path.parent, best_album_cover)
                            if cover_resize is not None
                            else False
                        )
                        track_no_fixed = _fix_target_track_number(local_path) if track_no_fix else False
                        file_hash = (
                            hash_file(local_path) if (spec is not None or cover_changed or track_no_fixed) else track.hash
                        )

                        _write_to_target_atomically(local_path, target_path)
                else:
                    _write_to_target_atomically(source_path, target_path)
                    file_format = track.format
                    file_hash = track.hash

                target_stat = target_path.stat()

                _register_track(
                    target_db, track, rel_target, file_hash, track.hash, file_format, target_stat.st_size, target_stat.st_mtime
                )
                target_source_hashes.add(track.hash)
                result.copied += 1
                logger.info(tr("log_sync_copied", index=index, total=total, source=track.path, target=rel_target))

                # A dir/filename template change followed by a forced re-sync
                # copies this track to its new rel_target above, but without
                # this, its old copy under the previous template's path would
                # never be removed -- force is meant to converge the device onto
                # the current template, not accumulate one stale copy per past
                # template. Best-effort and separate from the try/except above:
                # a failure here must not be reported as the copy itself having
                # failed, since it didn't.
                if force:
                    result.duplicates_removed += _remove_stale_duplicates(target_db, target_root, track.hash, rel_target)
            except (OSError, subprocess.CalledProcessError) as exc:
                logger.error(tr("log_sync_copy_error", path=track.path, error=exc))
                result.errors.append(f"{track.path}: {exc}")

    finally:
        # In a finally so a raised error still releases the cache's sqlite
        # handle -- the loop above touches removable media and file
        # conversion, both of which can fail in ways the per-track
        # try/except doesn't cover.
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


def _prune_missing_targets(
    target_root: Path,
    target_db: MusicDatabase,
    on_progress: ScanProgressCallback | None,
    should_stop: ShouldStopCallback | None,
) -> int:
    """Drop rows for files that are no longer on the target, so they aren't
    counted as "already synced".

    Deliberately *not* a full scan_directory(): that re-hashes every file on
    the card before a sync can start, which on a full SD card is minutes of
    reading before the first byte is written -- and it is wasted, because
    what the sync needs to know is only which registered files still exist.
    Adopting files that appeared on the target by other means is the "Scan
    device" button's job, not something every sync should pay for.
    """
    removed = 0
    rows = target_db.all_tracks()
    total = len(rows)
    with target_db.batch():
        for index, row in enumerate(rows, start=1):
            if should_stop and should_stop():
                break
            path = target_root / row.path
            if on_progress:
                on_progress(index, total, path)
            if not _is_present_on_target(path):
                target_db.delete_by_path(row.path)
                removed += 1
    if removed:
        logger.info(tr("log_sync_pruned_missing", count=removed))
    return removed


def _album_cover_quality(data: bytes) -> tuple[int, int] | None:
    info = read_image_info(data)
    if info is None:
        return None
    width, height, dpi = info
    return (width * height, dpi)


def _find_best_album_cover(album_dir: Path) -> tuple[bytes, str] | None:
    """Pick the highest-quality cover available for an album: the loose cover
    file sitting in `album_dir` (cover.jpg, folder.png, ...) plus the
    embedded art of every track there, compared by pixel count (then dpi as
    a tiebreaker). Resizing always starts from this one, regardless of which
    track is currently being written, so a track with a poor/missing
    embedded cover still ends up with the album's best artwork.

    The loose file is read in place and comes first, so on equal quality the
    file the user put in the directory is the one that wins."""
    candidates: list[tuple[bytes, str]] = []

    loose = read_loose_cover(album_dir)
    if loose is not None:
        candidates.append(loose)

    try:
        album_entries = list(album_dir.iterdir())
    except OSError:
        album_entries = []
    for audio_path in album_entries:
        if not audio_path.is_file() or audio_path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        cover_bytes = tagsmod.read_cover_art(audio_path)
        if cover_bytes:
            candidates.append((cover_bytes, tagsmod.sniff_image_mime(cover_bytes)))

    best: tuple[bytes, str] | None = None
    best_quality: tuple[int, int] | None = None
    for data, mime in candidates:
        quality = _album_cover_quality(data)
        if quality is None:
            continue
        if best_quality is None or quality > best_quality:
            best_quality = quality
            best = (data, mime)
    return best


class _LastAlbumCoverCache:
    """Caches the best available source cover for only the most recently
    seen album directory, not every album touched during the sync.

    Tracks arrive grouped by album (all_tracks() is sorted by artist, then
    album), so in practice this is a single-slot cache with a 100% hit rate.
    A dict keyed by every album dir instead -- as this used to be -- would
    hold every album's *raw, undownscaled* cover bytes in memory for the
    whole sync and never release them: a library of a few hundred albums at
    a few MB of embedded art each adds up to real memory. If tracks were
    ever handed over in some other order, a cache miss just means
    recomputing that album's best cover -- never a wrong one."""

    def __init__(self) -> None:
        self._album_dir: Path | None = None
        self._best: tuple[bytes, str] | None = None
        self._loaded = False

    def get(self, album_dir: Path) -> tuple[bytes, str] | None:
        if not self._loaded or album_dir != self._album_dir:
            self._album_dir = album_dir
            self._best = _find_best_album_cover(album_dir)
            self._loaded = True
        return self._best


def _resize_target_cover(
    target_path: Path,
    cover_resize: CoverResizeSettings,
    cover_cache: CoverCache | None,
    album_dir: Path,
    best_album_cover: _LastAlbumCoverCache,
) -> bool:
    try:
        best = best_album_cover.get(album_dir)
        if best is None:
            return False
        cover_bytes, mime = best

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
                cover_cache.put(source_hash, cover_resize.max_size, cover_resize.dpi, mime, resized_bytes)

        tagsmod.write_cover_art(target_path, resized_bytes, mime)
        return True
    except Exception as exc:
        # Still best-effort -- a cover that can't be resized must not fail the
        # copy -- but logged, because silently shipping the original artwork
        # for a whole library is exactly the kind of thing nobody notices.
        logger.warning(tr("log_sync_cover_resize_failed", path=target_path, error=exc))
        return False


def _fix_target_track_number(target_path: Path) -> bool:
    try:
        current = tagsmod.read_tags(target_path)
        fixed = tagsmod.fix_track_number(current["track_number"])
        if fixed == current["track_number"]:
            return False
        tagsmod.write_tags(target_path, {"track_number": fixed, "track_total": current["track_total"]})
        return True
    except Exception as exc:
        logger.warning(tr("log_sync_track_no_fix_failed", path=target_path, error=exc))
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
        track_total=track.track_total,
        disc_number=track.disc_number,
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


def _remove_stale_duplicates(target_db: MusicDatabase, target_root: Path, source_hash: str, current_rel_target: str) -> int:
    """After a forced re-sync just (re)wrote `source_hash`'s track to
    `current_rel_target`, delete every other copy of it still registered on
    the target -- left behind by an earlier sync under a dir/filename
    template that has since changed. Without this, a template change plus
    Force leaves both the old and the new copy on the device instead of
    converging on one.

    Best-effort: a missing file or a locked/unwritable one is logged and
    skipped rather than raised, since the copy this follows already
    succeeded and must not be reported as failed over cleanup of a leftover."""
    removed = 0
    for stale in target_db.get_all_by_source_hash(source_hash):
        if stale.path == current_rel_target:
            continue
        stale_path = target_root / stale.path
        try:
            if stale_path.exists():
                stale_path.unlink()
                remove_empty_parent_dirs(stale_path.parent, target_root)
            target_db.delete_by_path(stale.path)
            removed += 1
            logger.info(tr("log_sync_removed_stale_duplicate", old_path=stale.path, new_path=current_rel_target))
        except OSError as exc:
            logger.warning(tr("log_sync_stale_duplicate_cleanup_failed", path=stale.path, error=exc))
    return removed


def delete_many_from_device(
    device_mountpoint: Path,
    device_tracks: list[Track],
    on_progress: DeleteProgressCallback | None = None,
) -> None:
    """Delete the given tracks from the device and drop their rows from its
    database. The database is opened once and committed once for the whole
    batch -- doing that per track meant an open/commit/close cycle (each a
    real flush) against removable media for every single file."""
    device_mountpoint = Path(device_mountpoint)
    if not device_tracks:
        return

    device_db = MusicDatabase(device_db_path(device_mountpoint))
    try:
        with device_db.batch():
            total = len(device_tracks)
            for index, device_track in enumerate(device_tracks, start=1):
                if on_progress:
                    on_progress(index, total, device_track)
                file_path = device_mountpoint / device_track.path
                if file_path.exists():
                    file_path.unlink()
                    remove_empty_parent_dirs(file_path.parent, device_mountpoint)
                device_db.delete_by_path(device_track.path)
                logger.info(tr("log_deleted_from_device", path=device_track.path))
    finally:
        device_db.close()
