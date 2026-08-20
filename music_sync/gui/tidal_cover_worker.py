from PySide6.QtCore import QObject, Signal

from .. import tidal_cover


class TidalCoverWorker(QObject):
    """Looks up an album's cover art on Tidal in a background thread, so the
    network round-trip doesn't freeze the dialog. Mirrors the pattern used
    for device eject in main_window._EjectWorker."""

    finished = Signal(bytes, str)  # (cover_bytes or b"", error message or "")

    def __init__(self, artist: str, album: str, size: int):
        super().__init__()
        self.artist = artist
        self.album = album
        self.size = size

    def run(self):
        try:
            data = tidal_cover.download_cover_bytes(self.artist, self.album, self.size)
        except Exception as exc:
            self.finished.emit(b"", str(exc))
            return
        self.finished.emit(data, "")
