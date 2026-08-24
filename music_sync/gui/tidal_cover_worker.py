from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal

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


# Requests still in flight. A lookup outlives the dialog that started it:
# the HTTP round-trip can take seconds and there is no way to interrupt it,
# so closing the dialog cancels the *callback*, not the thread. Holding the
# request here (rather than on the dialog) is what keeps the QThread alive
# and un-garbage-collected until it actually finishes -- a QThread destroyed
# while still running aborts the process.
_ACTIVE: set["TidalCoverRequest"] = set()


CoverCallback = Callable[[bytes, str], None]


class TidalCoverRequest(QObject):
    """One in-flight Tidal cover lookup.

    Deliberately parentless: a dialog that owned it would take the running
    thread down with it on close. `cancel()` detaches the callback instead,
    so a finished lookup whose dialog is gone (or which has moved on to
    another track) is simply dropped.
    """

    def __init__(self, artist: str, album: str, size: int, on_finished: CoverCallback):
        super().__init__()
        self._callback: CoverCallback | None = on_finished
        self._thread = QThread()
        self._worker = TidalCoverWorker(artist, album, size)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # This object lives on the GUI thread and the worker doesn't, so Qt
        # queues the call and _on_finished (and with it the callback) runs on
        # the GUI thread -- never touching widgets from the worker thread.
        self._worker.finished.connect(self._on_finished)
        _ACTIVE.add(self)
        self._thread.start()

    def cancel(self) -> None:
        """Stop caring about the result. The thread runs to completion (the
        request can't be aborted mid-flight) but nothing is called back."""
        self._callback = None

    def _on_finished(self, data: bytes, error: str):
        self._thread.quit()
        self._thread.wait()
        callback = self._callback
        self._callback = None
        _ACTIVE.discard(self)
        if callback is not None:
            callback(data, error)
