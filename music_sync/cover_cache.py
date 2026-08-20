import hashlib
import sqlite3
from pathlib import Path

from .constants import DB_DIRNAME

COVERS_DB_FILENAME = "covers.db"
COVERS_DIRNAME = "[Covers]"


def covers_db_path(source_root: Path) -> Path:
    return Path(source_root) / DB_DIRNAME / COVERS_DB_FILENAME


def hash_cover(cover_bytes: bytes) -> str:
    return hashlib.sha256(cover_bytes).hexdigest()


class CoverCache:
    """Persists resized cover art as files under `[Covers]/`, placed directly
    inside whatever directory the source audio file actually lives in --
    independent of the library's folder layout (artist/album, artist-album,
    flat album folders, ...). Indexed by (raw cover hash, resize params) in a
    small sqlite db kept alongside the PC library. Resizing the same artwork
    -- across tracks in an album, repeat syncs, force-resync, or different
    target devices -- is then a file read instead of a decode+resize.

    `[Covers]/` holds resize output and nothing else. Artwork the user put in
    an album directory (cover.jpg, folder.png) is a *source*: it is read in
    place by album_covers.read_loose_cover() and never moved, renamed or
    deleted.

    Lives on the PC library side only: resize output doesn't depend on the
    sync target, and keeping it next to the source files means it survives
    moving the whole library folder.
    """

    def __init__(self, source_root: Path, db_path: Path):
        self.source_root = Path(source_root)
        self.db_path = Path(db_path)
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
            return (self.source_root / rel_path).read_bytes(), mime
        except OSError:
            # Cached file was deleted/moved out from under us -- fall back to
            # recomputing rather than erroring the sync.
            return None

    def put(self, source_hash: str, max_size: int, dpi: int, mime: str, resized_bytes: bytes, album_dir: Path) -> None:
        covers_dir = Path(album_dir) / COVERS_DIRNAME
        covers_dir.mkdir(parents=True, exist_ok=True)
        ext = "png" if mime == "image/png" else "jpg"
        file_path = covers_dir / f"{source_hash[:16]}_{max_size}_{dpi}.{ext}"
        file_path.write_bytes(resized_bytes)
        rel_path = file_path.relative_to(self.source_root)
        self.conn.execute(
            "INSERT OR REPLACE INTO cover_cache (source_hash, max_size, dpi, mime, file_path) VALUES (?, ?, ?, ?, ?)",
            (source_hash, max_size, dpi, mime, str(rel_path)),
        )
        self.conn.commit()
