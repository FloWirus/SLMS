from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QStyle, QApplication

from ..db import Track
from ..i18n import tr

COLUMN_KEYS = [
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


def _columns():
    return [(field, tr(label_key) if label_key else "") for field, label_key in COLUMN_KEYS]


def format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


class TrackTableModel(QAbstractTableModel):
    def __init__(self, tracks: list[Track] | None = None, device_hashes: set[str] | None = None):
        super().__init__()
        self._tracks: list[Track] = tracks or []
        self._device_hashes: set[str] = device_hashes or set()

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

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMN_KEYS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return _columns()[section][1]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        track = self._tracks[index.row()]
        key, _ = COLUMN_KEYS[index.column()]

        if key == "on_device":
            if role == Qt.DecorationRole and track.hash in self._device_hashes:
                style = QApplication.instance().style()
                return style.standardIcon(QStyle.SP_DialogApplyButton)
            return None

        if role == Qt.DisplayRole:
            if key == "size":
                return format_size(track.size)
            return getattr(track, key)

        if role == Qt.UserRole:
            return getattr(track, key)

        return None

    def sort(self, column: int, order=Qt.AscendingOrder) -> None:
        key, _ = COLUMN_KEYS[column]
        reverse = order == Qt.DescendingOrder
        self.layoutAboutToBeChanged.emit()

        def sort_key(track: Track):
            value = getattr(track, key)
            if key in ("track_number", "track_total", "disc_number", "year", "size"):
                try:
                    return (0, float(value))
                except (ValueError, TypeError):
                    return (1, 0.0)
            return str(value).lower()

        self._tracks.sort(key=sort_key, reverse=reverse)
        self.layoutChanged.emit()
