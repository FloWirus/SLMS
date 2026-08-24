from functools import lru_cache

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from .. import tags as tagsmod
from ..db import Track
from ..i18n import tr
from .icons import checkbox_icon, full_presence_icon

COLUMN_KEYS = [
    ("checked", None),
    ("on_device", None),
    ("artist", "col_artist"),
    ("album", "col_album"),
    ("disc_number", "col_disc"),
    ("title", "col_title"),
    ("track_number", "col_track"),
    ("track_total", "col_track_total"),
    ("year", "col_year"),
    ("genre", "col_genre"),
    ("format", "col_format"),
    ("size", "col_size"),
]


def _columns(presence_label: str = ""):
    headers = []
    for field, label_key in COLUMN_KEYS:
        if field == "checked":
            headers.append((field, "✓"))
        elif field == "on_device":
            headers.append((field, presence_label))
        elif label_key:
            headers.append((field, tr(label_key)))
        else:
            headers.append((field, ""))
    return headers


_POLISH_LETTER_RANK = {
    "a": ord("a"),
    "\u0105": ord("a") + 0.5,  # ą
    "c": ord("c"),
    "\u0107": ord("c") + 0.5,  # ć
    "e": ord("e"),
    "\u0119": ord("e") + 0.5,  # ę
    "l": ord("l"),
    "\u0142": ord("l") + 0.5,  # ł
    "n": ord("n"),
    "\u0144": ord("n") + 0.5,  # ń
    "o": ord("o"),
    "\u00f3": ord("o") + 0.5,  # ó
    "s": ord("s"),
    "\u015b": ord("s") + 0.5,  # ś
    "z": ord("z"),
    "\u017a": ord("z") + 0.3,  # ź
    "\u017c": ord("z") + 0.6,  # ż
}


# Cached: the proxy model sorts through Qt.UserRole, and
# QSortFilterProxyModel calls data() twice per comparison -- O(n log n)
# calls per sort, with a fresh sort after every filter change. Computing
# the key from scratch each time made typing in the search box crawl on a
# few-thousand-track library. Artists/albums repeat heavily across rows, so
# the cache hits hard; the bound keeps a huge library from growing it
# without limit.
@lru_cache(maxsize=100_000)
def polish_sort_key(value: str) -> str:
    """Sort key placing Polish diacritic letters right after their base letter
    (a, \u0105, b, c, \u0107, ... l, \u0142, m, ... z, \u017a, \u017c) instead
    of after z.

    Returns a string, not the tuple-of-ranks this used to be: each rank is
    encoded as a fixed-width, zero-padded digit block, so comparing the
    strings lexicographically gives exactly the same order comparing the
    tuples position-by-position would. That matters because this key also
    feeds Qt.UserRole for table sorting (see TrackTableModel.data below) --
    QSortFilterProxyModel's default lessThan compares QVariants via a
    registered "<" operator, which plain Python tuples don't have, so a
    tuple key silently sorts as a no-op there even though sorted(key=...)
    (used elsewhere in this file) is perfectly happy with one."""
    lowered = value.lower()
    return "".join(f"{round(_POLISH_LETTER_RANK.get(ch, ord(ch)) * 10):08d}" for ch in lowered)


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# Columns whose display text ("10", "9", "9.5 MB") sorts wrong as a string;
# these are compared as numbers instead, via _numeric_sort_key below.
_NUMERIC_COLUMNS = {"track_number", "track_total", "disc_number", "year", "size"}


def _numeric_sort_key(value) -> float:
    """The value as a float for anything that parses as a number, or +inf for
    blank/non-numeric values so they consistently sort after every real
    number instead of interleaving among them the way string comparison
    would. A single float rather than a (flag, value) tuple for the same
    reason polish_sort_key returns a string now: Qt.UserRole sorting goes
    through QVariant comparison, which doesn't know how to compare tuples."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return float("inf")


# Numbers are encoded as fixed-width zero-padded digit strings rather than
# left as floats, so that every column's sort key is a string and can carry
# the tiebreaker below appended to it. Width 20 covers file sizes with the
# x1000 scaling that keeps three decimals of precision.
_NUMERIC_WIDTH = 20
_NUMERIC_MAX = 10 ** _NUMERIC_WIDTH - 2


def _numeric_sort_string(value) -> str:
    number = _numeric_sort_key(value)
    if number == float("inf"):
        # Blank/non-numeric still sorts after every real number, as before.
        return "9" * _NUMERIC_WIDTH
    return f"{min(max(int(round(number * 1000)), 0), _NUMERIC_MAX):0{_NUMERIC_WIDTH}d}"


# Separator between the parts of a composite key. Below "0", so it always
# compares less than any digit a key part can start with -- that makes a
# shorter part sort before a longer one that merely extends it.
_KEY_SEP = "\x00"


class TrackTableModel(QAbstractTableModel):
    def __init__(
        self,
        tracks: list[Track] | None = None,
        device_hashes: set[str] | None = None,
        presence_label: str = "",
        checked_hashes: set[str] | None = None,
        on_check_changed=None,
    ):
        super().__init__()
        self._tracks: list[Track] = tracks or []
        self._device_hashes: set[str] = device_hashes or set()
        self._presence_label = presence_label
        self._checked_hashes: set[str] = checked_hashes if checked_hashes is not None else set()
        self._on_check_changed = on_check_changed
        # Lazily built, one lowercase string per track, holding every field
        # the search box looks at -- see haystack_at.
        self._haystacks: list[str] | None = None
        # Sort keys, built per column on first use -- see sort_key_at.
        self._sort_keys: dict[str, list[str]] = {}

    def set_tracks(self, tracks: list[Track]) -> None:
        self.beginResetModel()
        self._tracks = tracks
        self._haystacks = None
        self._sort_keys.clear()
        self.endResetModel()

    def haystack_at(self, row: int) -> str:
        """Everything on a row that the search box matches against, as one
        lowercase string. Built once for the whole table and reused across
        keystrokes: TrackFilterProxyModel tests each row with a single
        substring check instead of pulling and regex-matching twelve
        per-column QVariants every time the query changes."""
        if self._haystacks is None:
            # Newline-joined, not space-joined: a QLineEdit can't contain a
            # newline, so no query can span two fields. That keeps the old
            # per-column matching semantics -- "beatles help" still has to
            # occur inside a single field to count as a hit.
            self._haystacks = [
                "\n".join(
                    (
                        track.artist or "",
                        track.album or "",
                        track.title or "",
                        track.genre or "",
                        track.format or "",
                        track.year or "",
                        track.disc_number or "",
                        tagsmod.fix_track_number(track.track_number) or "",
                        track.track_total or "",
                        format_size(track.size),
                    )
                ).lower()
                for track in self._tracks
            ]
        return self._haystacks[row]

    def _tiebreak_at(self, row: int) -> str:
        """The row's own position in the source model, zero-padded.

        Appended to every column's sort key so rows the sorted column can't
        tell apart still have exactly one valid order. Source position is the
        right fallback and the cheapest one: all_tracks() already returns
        rows ordered by artist, album, disc, track, so falling back to it
        reproduces that natural order, and comparing an 8-digit index beats
        comparing the concatenated artist/album/title keys it stands in for.
        """
        return f"{row:08d}"

    def sort_key_at(self, row: int, key: str) -> str:
        """The value the proxy sorts this cell on (it sorts through
        Qt.UserRole -- see MainWindow._build_table_view).

        Every key is a string ending in the same tiebreaker, giving the table
        a total order. Without it the sort has ties everywhere -- the two
        boolean columns tie for nearly every row, and so does any year or
        genre shared across a library -- and since Qt's sort is not stable,
        tied rows came back from a filter change in a different order than
        they went in: clearing a search visibly reshuffled the table.

        Built once per column and reused, so sorting reads a list rather than
        recomputing a key per comparison.
        """
        keys = self._sort_keys.get(key)
        if keys is None:
            if key == "checked":
                heads = ["1" if t.hash in self._checked_hashes else "0" for t in self._tracks]
            elif key == "on_device":
                heads = ["1" if t.source_hash in self._device_hashes else "0" for t in self._tracks]
            elif key in _NUMERIC_COLUMNS:
                heads = [_numeric_sort_string(getattr(t, key)) for t in self._tracks]
            else:
                heads = [polish_sort_key(str(getattr(t, key) or "")) for t in self._tracks]
            keys = self._sort_keys[key] = [
                head + _KEY_SEP + self._tiebreak_at(row) for row, head in enumerate(heads)
            ]
        return keys[row]

    def set_device_hashes(self, device_hashes: set[str]) -> None:
        self._device_hashes = device_hashes
        # Presence feeds that column's sort key, so it has to be rebuilt.
        self._sort_keys.pop("on_device", None)
        if self._tracks:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self._tracks) - 1, 0)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DecorationRole])

    def track_at(self, row: int) -> Track:
        return self._tracks[row]

    def all_checked(self) -> bool:
        return bool(self._tracks) and all(t.hash in self._checked_hashes for t in self._tracks)

    def refresh_checked(self) -> None:
        if self._tracks:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self._tracks) - 1, 0)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DecorationRole])

    def toggle_checked(self, row: int) -> None:
        track = self._tracks[row]
        checked = track.hash not in self._checked_hashes
        if checked:
            self._checked_hashes.add(track.hash)
        else:
            self._checked_hashes.discard(track.hash)
        checked_keys = self._sort_keys.get("checked")
        if checked_keys is not None:
            checked_keys[row] = ("1" if checked else "0") + _KEY_SEP + self._tiebreak_at(row)
        index = self.index(row, 0)
        self.dataChanged.emit(index, index, [Qt.DecorationRole])
        if self._on_check_changed:
            self._on_check_changed(track.hash, checked)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMN_KEYS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return _columns(self._presence_label)[section][1]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        track = self._tracks[index.row()]
        key, _ = COLUMN_KEYS[index.column()]

        if key == "checked":
            if role == Qt.DecorationRole:
                return checkbox_icon(track.hash in self._checked_hashes)
            if role == Qt.UserRole:
                return self.sort_key_at(index.row(), key)
            return None

        if key == "on_device":
            if role == Qt.DecorationRole and track.source_hash in self._device_hashes:
                return full_presence_icon()
            if role == Qt.UserRole:
                return self.sort_key_at(index.row(), key)
            return None

        if role == Qt.DisplayRole:
            if key == "size":
                return format_size(track.size)
            if key == "track_number":
                return tagsmod.fix_track_number(track.track_number)
            return getattr(track, key)

        if role == Qt.UserRole:
            return self.sort_key_at(index.row(), key)

        return None


class TrackFilterProxyModel(QSortFilterProxyModel):
    """Search filter for the track tables.

    Replaces setFilterFixedString + setFilterKeyColumn(-1), which made Qt
    pull data() for all twelve columns of every row and run a regular
    expression over each one on every keystroke. Here each row is matched
    against a single pre-built lowercase haystack string (see
    TrackTableModel.haystack_at) with a plain substring test."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._needle = ""

    def set_filter_text(self, text: str) -> None:
        needle = text.strip().lower()
        if needle == self._needle:
            return
        self._needle = needle
        # Safe to re-run acceptance against the mapping already built rather
        # than rebuilding it: TrackTableModel.sort_key_at gives the table a
        # total order, so rows a widened query re-admits go back to their one
        # correct position instead of landing wherever a tie left them.
        self.invalidateRowsFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._needle:
            return True
        return self._needle in self.sourceModel().haystack_at(source_row)
