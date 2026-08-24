import ctypes
import hashlib
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .constants import DB_DIRNAME, DEVICE_DB_DIRNAME, LIBRARY_DB_FILENAME, DEVICE_DB_FILENAME

FILE_ATTRIBUTE_HIDDEN = 0x02

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    hash TEXT NOT NULL,
    artist TEXT,
    album TEXT,
    title TEXT,
    track_number TEXT,
    track_total TEXT,
    disc_number TEXT,
    year TEXT,
    genre TEXT,
    format TEXT,
    size INTEGER,
    mtime REAL,
    source_hash TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tracks_hash ON tracks(hash);
"""


@dataclass
class Track:
    id: int | None
    path: str
    filename: str
    hash: str
    source_hash: str = ""
    artist: str = ""
    album: str = ""
    title: str = ""
    track_number: str = ""
    track_total: str = ""
    disc_number: str = ""
    year: str = ""
    genre: str = ""
    format: str = ""
    size: int = 0
    mtime: float = 0.0

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Track":
        return Track(
            id=row["id"],
            path=row["path"],
            filename=row["filename"],
            hash=row["hash"],
            source_hash=row["source_hash"] or "",
            artist=row["artist"] or "",
            album=row["album"] or "",
            title=row["title"] or "",
            track_number=row["track_number"] or "",
            track_total=row["track_total"] or "",
            disc_number=row["disc_number"] or "",
            year=row["year"] or "",
            genre=row["genre"] or "",
            format=row["format"] or "",
            size=row["size"] or 0,
            mtime=row["mtime"] or 0.0,
        )


class MusicDatabase:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.parent.name == DEVICE_DB_DIRNAME:
            _hide_dir_windows(self.db_path.parent)
        # check_same_thread=False plus the lock below: the long operations
        # (scan, sync, delete) run on a worker thread while the connection
        # itself is created here on the GUI thread -- see gui/background.py.
        # sqlite3's own thread check would reject that outright, and the lock
        # is what makes allowing it actually safe rather than merely quiet.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._deferred_commits = 0
        self._migrate()

    @contextmanager
    def batch(self):
        """Group many writes into a single transaction: the individual write
        methods below skip their own commit while this is active, and one
        commit happens on exit. Committing per row is what makes bulk work
        (a full scan, deleting a whole album) slow, especially on removable
        media where every commit means a real flush to the card.

        Partial work is still committed if the body raises or breaks out
        early, matching the per-row behaviour it replaces -- a scan that
        errors halfway keeps the rows it already processed."""
        with self._lock:
            self._deferred_commits += 1
        try:
            yield
        finally:
            with self._lock:
                self._deferred_commits -= 1
                if not self._deferred_commits:
                    self.conn.commit()

    def _commit(self) -> None:
        with self._lock:
            if not self._deferred_commits:
                self.conn.commit()

    def _migrate(self):
        with self._lock:
            columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(tracks)")}
            if "track_total" not in columns:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN track_total TEXT")
                self.conn.commit()
            if "disc_number" not in columns:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN disc_number TEXT")
                self.conn.commit()
            if "source_hash" not in columns:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN source_hash TEXT DEFAULT ''")
                self.conn.commit()
            # Backfill only when there is something to backfill. This used to
            # run unconditionally: a full table write (and commit) on every
            # single open of every database, including the one living on an
            # SD card, to fix rows a version-old migration had already fixed.
            needs_backfill = self.conn.execute(
                "SELECT 1 FROM tracks WHERE source_hash IS NULL OR source_hash = '' LIMIT 1"
            ).fetchone()
            if needs_backfill:
                self.conn.execute(
                    "UPDATE tracks SET source_hash = hash WHERE source_hash IS NULL OR source_hash = ''"
                )
                self.conn.commit()

    def close(self):
        with self._lock:
            self.conn.close()

    def upsert_track(self, track: Track) -> int:
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO tracks (path, filename, hash, source_hash, artist, album, title,
                                     track_number, track_total, disc_number, year, genre, format, size, mtime)
                VALUES (:path, :filename, :hash, :source_hash, :artist, :album, :title,
                        :track_number, :track_total, :disc_number, :year, :genre, :format, :size, :mtime)
                ON CONFLICT(path) DO UPDATE SET
                    filename=excluded.filename,
                    hash=excluded.hash,
                    source_hash=excluded.source_hash,
                    artist=excluded.artist,
                    album=excluded.album,
                    title=excluded.title,
                    track_number=excluded.track_number,
                    track_total=excluded.track_total,
                    disc_number=excluded.disc_number,
                    year=excluded.year,
                    genre=excluded.genre,
                    format=excluded.format,
                    size=excluded.size,
                    mtime=excluded.mtime
                RETURNING id
                """,
                track.__dict__,
            )
            row = cur.fetchone()
            self._commit()
            return row["id"]

    def delete_by_path(self, path: str):
        with self._lock:
            self.conn.execute("DELETE FROM tracks WHERE path = ?", (path,))
            self._commit()

    def remove_missing(self, existing_paths: set[str]):
        with self._lock:
            # Staged through a temp table rather than a "path NOT IN (...)" with
            # one parameter per path -- large libraries would blow past SQLite's
            # bound-parameter limit, and this stays a single DELETE either way.
            self.conn.execute("CREATE TEMP TABLE IF NOT EXISTS existing_paths (path TEXT PRIMARY KEY)")
            self.conn.execute("DELETE FROM existing_paths")
            self.conn.executemany(
                "INSERT OR IGNORE INTO existing_paths (path) VALUES (?)", ((p,) for p in existing_paths)
            )
            self.conn.execute("DELETE FROM tracks WHERE path NOT IN (SELECT path FROM existing_paths)")
            self.conn.execute("DELETE FROM existing_paths")
            self._commit()

    def tracks_by_path(self) -> dict[str, Track]:
        """Every row keyed by its relative path, for callers that would
        otherwise run one get_by_path() query per file on disk."""
        with self._lock:
            rows = self.conn.execute("SELECT * FROM tracks").fetchall()
            return {row["path"]: Track.from_row(row) for row in rows}

    def all_tracks(self) -> list[Track]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM tracks ORDER BY artist, album, CAST(disc_number AS INTEGER), CAST(track_number AS INTEGER)"
            ).fetchall()
            return [Track.from_row(r) for r in rows]

    def source_hashes(self) -> set[str]:
        with self._lock:
            rows = self.conn.execute("SELECT source_hash FROM tracks").fetchall()
            return {r["source_hash"] for r in rows}

    def get_by_source_hash(self, hash_: str) -> Track | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM tracks WHERE source_hash = ?", (hash_,)).fetchone()
            return Track.from_row(row) if row else None

    def get_all_by_source_hash(self, hash_: str) -> list[Track]:
        """Every row for a given source hash. source_hash is not unique --
        normally there is at most one, but a template change followed by a
        force re-sync can leave the same track registered under more than
        one path until that duplicate is cleaned up."""
        with self._lock:
            rows = self.conn.execute("SELECT * FROM tracks WHERE source_hash = ?", (hash_,)).fetchall()
            return [Track.from_row(row) for row in rows]

    def reassign_source_hash(self, old_hash: str, new_hash: str) -> int:
        """Re-point rows registered against `old_hash` at `new_hash`.

        A device row's source_hash is the only link back to the library file
        it was copied from, and that link is the file's content hash -- which
        changes the moment its tags are edited. Without this, editing a tag
        on the PC makes every copy already on a device look like it came from
        some other, now-missing track: the presence tick disappears, "delete
        from device" stops finding it, and the next sync writes a second copy
        alongside the first."""
        with self._lock:
            cur = self.conn.execute(
                "UPDATE tracks SET source_hash = ? WHERE source_hash = ?", (new_hash, old_hash)
            )
            self._commit()
            return cur.rowcount

    def get_by_path(self, path: str) -> Track | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()
            return Track.from_row(row) if row else None


def library_db_path(data_dir: Path, source_root: Path | None = None) -> Path:
    """Where the library index for `source_root` lives.

    One database per source directory, named after a hash of its absolute
    path. A single shared library.db meant that pointing the app at a second
    folder wiped the first one's index (the scan drops every row whose file
    is "missing"), so switching back and forth re-hashed the whole library
    every time.

    With no source_root this is the pre-existing shared path, which is what
    the one-time migration in MainWindow._open_library_db renames.
    """
    directory = Path(data_dir) / DB_DIRNAME
    if source_root is None:
        return directory / LIBRARY_DB_FILENAME
    digest = hashlib.sha1(str(Path(source_root).resolve()).encode("utf-8")).hexdigest()[:12]
    return directory / f"library-{digest}.db"


def device_db_path(device_mountpoint: Path) -> Path:
    device_mountpoint = Path(device_mountpoint)
    db_dir = device_mountpoint / DEVICE_DB_DIRNAME

    # Migrate the old, visible directory name from earlier versions.
    old_db_dir = device_mountpoint / DB_DIRNAME
    if old_db_dir.is_dir() and not db_dir.exists():
        old_db_dir.rename(db_dir)

    _hide_dir_windows(db_dir)
    return db_dir / DEVICE_DB_FILENAME


def _hide_dir_windows(path: Path) -> None:
    """Best-effort: set the Windows Hidden attribute so the folder stays
    tucked away even in file managers that ignore dot-prefixes."""
    if os.name != "nt":
        return
    try:
        if path.is_dir():
            ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass
