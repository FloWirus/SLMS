import ctypes
import os
import sqlite3
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
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
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
        self._deferred_commits += 1
        try:
            yield
        finally:
            self._deferred_commits -= 1
            if not self._deferred_commits:
                self.conn.commit()

    def _commit(self) -> None:
        if not self._deferred_commits:
            self.conn.commit()

    def _migrate(self):
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
        self.conn.execute("UPDATE tracks SET source_hash = hash WHERE source_hash IS NULL OR source_hash = ''")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def upsert_track(self, track: Track) -> int:
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
        self.conn.execute("DELETE FROM tracks WHERE path = ?", (path,))
        self._commit()

    def remove_missing(self, existing_paths: set[str]):
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
        rows = self.conn.execute("SELECT * FROM tracks").fetchall()
        return {row["path"]: Track.from_row(row) for row in rows}

    def all_tracks(self) -> list[Track]:
        rows = self.conn.execute(
            "SELECT * FROM tracks ORDER BY artist, album, CAST(disc_number AS INTEGER), CAST(track_number AS INTEGER)"
        ).fetchall()
        return [Track.from_row(r) for r in rows]

    def source_hashes(self) -> set[str]:
        rows = self.conn.execute("SELECT source_hash FROM tracks").fetchall()
        return {r["source_hash"] for r in rows}

    def get_by_source_hash(self, hash_: str) -> Track | None:
        row = self.conn.execute("SELECT * FROM tracks WHERE source_hash = ?", (hash_,)).fetchone()
        return Track.from_row(row) if row else None

    def get_all_by_source_hash(self, hash_: str) -> list[Track]:
        """Every row for a given source hash. source_hash is not unique --
        normally there is at most one, but a template change followed by a
        force re-sync can leave the same track registered under more than
        one path until that duplicate is cleaned up."""
        rows = self.conn.execute("SELECT * FROM tracks WHERE source_hash = ?", (hash_,)).fetchall()
        return [Track.from_row(row) for row in rows]

    def get_by_path(self, path: str) -> Track | None:
        row = self.conn.execute("SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()
        return Track.from_row(row) if row else None


def library_db_path(project_root: Path) -> Path:
    return Path(project_root) / DB_DIRNAME / LIBRARY_DB_FILENAME


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
