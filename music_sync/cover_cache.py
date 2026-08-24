import hashlib
import sqlite3
from pathlib import Path

from .constants import app_data_dir

COVERS_DB_FILENAME = "covers.db"
COVERS_DIRNAME = "covers"


def covers_dir() -> Path:
    """Where resized cover art is cached.

    Under the app's own data directory, not inside the music library. The
    cache used to write a music_db/covers.db plus a "[Covers]/" folder into
    every album directory it touched -- files the user never asked for, in a
    library this app otherwise only reads from, and left behind for good once
    the resize settings changed. Nothing about a resized cover depends on
    where the source file lives (it is keyed by the artwork's own hash), so
    it belongs with the app's data.
    """
    return app_data_dir() / COVERS_DIRNAME


def hash_cover(cover_bytes: bytes) -> str:
    return hashlib.sha256(cover_bytes).hexdigest()


class CoverCache:
    """Resized cover art, cached as files under the app's data directory and
    indexed by (raw cover hash, resize params) in a small sqlite database.

    Resizing the same artwork again -- across the tracks of an album, repeat
    syncs, a forced re-sync, or a second device -- then costs a file read
    instead of a decode plus a rescale.

    Keyed by the *source artwork's* hash, so the cache is equally valid for
    every device and survives the library being moved or renamed. Artwork the
    user put in an album directory (cover.jpg, folder.png) is a source: it is
    read in place by album_covers.read_loose_cover() and never moved, renamed
    or deleted.
    """

    def __init__(self, root: Path | None = None):
        self.source_root = Path(root) if root is not None else covers_dir()
        self.db_path = self.source_root / COVERS_DB_FILENAME
        self.source_root.mkdir(parents=True, exist_ok=True)
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
            return (self.source_root / rel_path).read_bytes(), mime
        except OSError:
            # Cached file was deleted/moved out from under us -- fall back to
            # recomputing rather than erroring the sync.
            return None

    def put(self, source_hash: str, max_size: int, dpi: int, mime: str, resized_bytes: bytes) -> None:
        # Fanned out over one level of subdirectories named by the hash's
        # first two characters: a large library resized at a couple of
        # settings puts thousands of files here, and every file manager and
        # filesystem is happier with 256 directories than one huge one.
        bucket = self.source_root / source_hash[:2]
        bucket.mkdir(parents=True, exist_ok=True)
        ext = "png" if mime == "image/png" else "jpg"
        file_path = bucket / f"{source_hash[:16]}_{max_size}_{dpi}.{ext}"
        file_path.write_bytes(resized_bytes)
        rel_path = file_path.relative_to(self.source_root)
        self.conn.execute(
            "INSERT OR REPLACE INTO cover_cache (source_hash, max_size, dpi, mime, file_path) VALUES (?, ?, ?, ?, ?)",
            (source_hash, max_size, dpi, mime, str(rel_path)),
        )
        self.conn.commit()
