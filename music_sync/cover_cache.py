import hashlib
import sqlite3
from pathlib import Path

from .constants import DB_DIRNAME, app_data_dir

COVERS_DB_FILENAME = "covers.db"
# Where the cache keeps its files when it lives in the app's data directory.
APP_COVERS_DIRNAME = "covers"
# ...and the per-album folder it uses when it lives next to the music. Named
# in brackets so it sorts to the top of an album directory and reads as "not
# one of the music files".
ALBUM_COVERS_DIRNAME = "[Covers]"


def app_covers_dir() -> Path:
    return app_data_dir() / APP_COVERS_DIRNAME


def library_covers_db_path(source_root: Path) -> Path:
    return Path(source_root) / DB_DIRNAME / COVERS_DB_FILENAME


def hash_cover(cover_bytes: bytes) -> str:
    return hashlib.sha256(cover_bytes).hexdigest()


class CoverCache:
    """Resized cover art, kept as files and indexed by (raw cover hash, resize
    params) in a small sqlite database.

    Resizing the same artwork again -- across the tracks of an album, repeat
    syncs, a forced re-sync, or a second device -- then costs a file read
    instead of a decode plus a rescale. Keyed by the *source artwork's* hash,
    so a cached result is equally valid for every sync target.

    Two layouts, chosen by the "keep the cache with the music library"
    setting:

    * next to the library (the default): the index goes to
      `<library>/music_db/covers.db` and each resized file into a
      `[Covers]/` folder inside whichever directory holds that album's audio
      -- so the results sit with the music they belong to, visible in a file
      manager, and travel with the library if it is copied elsewhere;
    * in the app's data directory: `~/.local/share/SLMS/covers/`, leaving the
      music library untouched by anything this app writes.

    Either way, artwork the user put in an album directory (cover.jpg,
    folder.png) is a *source*: read in place by
    album_covers.read_loose_cover() and never moved, renamed or deleted.
    `[Covers]/` holds resize output and nothing else.
    """

    def __init__(self, root: Path, db_path: Path, per_album: bool = False):
        self.root = Path(root)
        self.db_path = Path(db_path)
        self.per_album = per_album
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cover_cache (
                source_hash TEXT NOT NULL,
                max_size INTEGER NOT NULL,
                dpi INTEGER NOT NULL,
                mime TEXT NOT NULL,
                file_path TEXT NOT NULL,
                PRIMARY KEY (source_hash, max_size, dpi)
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get(self, source_hash: str, max_size: int, dpi: int) -> tuple[bytes, str] | None:
        row = self.conn.execute(
            "SELECT mime, file_path FROM cover_cache WHERE source_hash = ? AND max_size = ? AND dpi = ?",
            (source_hash, max_size, dpi),
        ).fetchone()
        if row is None:
            return None
        mime, rel_path = row
        try:
            return (self.root / rel_path).read_bytes(), mime
        except OSError:
            # Cached file was deleted/moved out from under us -- fall back to
            # recomputing rather than erroring the sync.
            return None

    def put(
        self,
        source_hash: str,
        max_size: int,
        dpi: int,
        mime: str,
        resized_bytes: bytes,
        album_dir: Path | None = None,
    ) -> Path | None:
        """Store one resized cover and return where it was written.

        `album_dir` is where the source album's audio lives; it decides the
        `[Covers]/` location in per-album mode and is ignored otherwise.
        Returns None if the file could not be written -- a read-only library,
        say -- since the sync itself must not fail over a cache miss.
        """
        directory = self._directory_for(source_hash, album_dir)
        ext = "png" if mime == "image/png" else "jpg"
        file_path = directory / f"{source_hash[:16]}_{max_size}_{dpi}.{ext}"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(resized_bytes)
            rel_path = file_path.relative_to(self.root)
        except (OSError, ValueError):
            return None
        self.conn.execute(
            "INSERT OR REPLACE INTO cover_cache (source_hash, max_size, dpi, mime, file_path) VALUES (?, ?, ?, ?, ?)",
            (source_hash, max_size, dpi, mime, str(rel_path)),
        )
        self.conn.commit()
        return file_path

    def _directory_for(self, source_hash: str, album_dir: Path | None) -> Path:
        if self.per_album and album_dir is not None:
            return Path(album_dir) / ALBUM_COVERS_DIRNAME
        # Fanned out over one level of subdirectories named by the hash's
        # first two characters: a whole library resized at a couple of
        # settings puts thousands of files in one place otherwise, and both
        # file managers and filesystems are happier with 256 directories.
        return self.root / source_hash[:2]


def open_cover_cache(source_root: Path, in_library: bool) -> CoverCache:
    """The cache for a sync out of `source_root`, in whichever of the two
    layouts the setting asks for (see CoverCache)."""
    if in_library:
        return CoverCache(source_root, library_covers_db_path(source_root), per_album=True)
    root = app_covers_dir()
    return CoverCache(root, root / COVERS_DB_FILENAME)
