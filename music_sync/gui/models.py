from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

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


def polish_sort_key(value: str):
    """Sort key placing Polish diacritic letters right after their base letter
    (a, \u0105, b, c, \u0107, ... l, \u0142, m, ... z, \u017a, \u017c) instead
    of after z. Each character contributes one rank, so words compare
    correctly position by position regardless of length."""
    lowered = value.lower()
    return tuple(_POLISH_LETTER_RANK.get(ch, ord(ch)) for ch in lowered)


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


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

    def set_tracks(self, tracks: list[Track]) -> None:
        self.beginResetModel()
        self._tracks = tracks
        self.endResetModel()

    def set_device_hashes(self, device_hashes: set[str]) -> None:
        self._device_hashes = device_hashes
        if self._tracks:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self._tracks) - 1, 0)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DecorationRole])

    def track_at(self, row: int) -> Track:
        return self._tracks[row]

    def checked_tracks(self) -> list[Track]:
        return [t for t in self._tracks if t.hash in self._checked_hashes]

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
            return None

        if key == "on_device":
            if role == Qt.DecorationRole and track.source_hash in self._device_hashes:
                return full_presence_icon()
            return None

        if role == Qt.DisplayRole:
            if key == "size":
                return format_size(track.size)
            if key == "track_number":
                return tagsmod.fix_track_number(track.track_number)
            return getattr(track, key)

        if role == Qt.UserRole:
            return getattr(track, key)

        return None

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        key, _ = COLUMN_KEYS[column]
        reverse = order == Qt.DescendingOrder
        self.layoutAboutToBeChanged.emit()

        def sort_key(track: Track):
            if key == "checked":
                return track.hash in self._checked_hashes
            if key == "on_device":
                return track.source_hash in self._device_hashes
            value = getattr(track, key)
            if key in ("track_number", "track_total", "disc_number", "year", "size"):
                try:
                    return (0, float(value))
                except (ValueError, TypeError):
                    return (1, 0.0)
            return polish_sort_key(str(value))

        self._tracks.sort(key=sort_key, reverse=reverse)
        self.layoutChanged.emit()
