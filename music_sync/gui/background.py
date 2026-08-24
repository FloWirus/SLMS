"""Running the long file operations (scan, sync, delete) off the GUI thread.

Every one of them is a blocking loop over files: hashing a library, copying
to an SD card, running ffmpeg. On the GUI thread that means a frozen window,
and the QApplication.processEvents() calls that used to paper over it are
worse than they look -- they re-enter the event loop from inside the
operation, so a stray click could start a second one on top of the first.

Here the work runs on a QThread and talks back through signals:

  * progress   -- fire-and-forget, the dialog just repaints;
  * conflict   -- a real question, so the worker blocks on an Event until the
                  GUI thread has shown the message box and answered;
  * cancel     -- a plain threading.Event the work polls between files.

The GUI side waits in a nested event loop (see MainWindow._run_background),
which is what keeps the window painting and the Cancel button alive while
the operation runs.
"""

import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..db import Track
from ..sync import ConflictResolution


class TaskContext(QObject):
    """The worker's half of the conversation, handed to the work function.

    Its progress()/ask_conflict()/should_stop() are called from the worker
    thread; cancel() and answer_conflict() from the GUI thread.
    """

    progress_reported = Signal(int, int, str)
    conflict_raised = Signal(object, str)

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()
        self._answered = threading.Event()
        self._answer = ConflictResolution.SKIP

    # -- called on the worker thread --

    def progress(self, index: int, total: int, name: str) -> None:
        self.progress_reported.emit(index, total, name)

    def should_stop(self) -> bool:
        return self._stop.is_set()

    def ask_conflict(self, track: Track, target_path: Path) -> ConflictResolution:
        """Ask the GUI thread what to do about a file that already exists,
        and block until it says. Cancelling counts as an answer (skip), so a
        user who hits Cancel instead of answering doesn't leave the worker
        waiting forever on a dialog that is no longer on screen."""
        self._answered.clear()
        self.conflict_raised.emit(track, str(target_path))
        while not self._answered.wait(0.1):
            if self._stop.is_set():
                return ConflictResolution.SKIP
        return self._answer

    # -- called on the GUI thread --

    def cancel(self) -> None:
        self._stop.set()
        # Releases a worker parked in ask_conflict() above.
        self._answered.set()

    def answer_conflict(self, resolution: ConflictResolution) -> None:
        self._answer = resolution
        self._answered.set()


class TaskWorker(QObject):
    """Runs one work function on the thread it has been moved to."""

    finished = Signal(object, object)  # (result, exception or None)

    def __init__(self, work: Callable[[TaskContext], object], context: TaskContext):
        super().__init__()
        self._work = work
        self._context = context

    def run(self) -> None:
        try:
            result = self._work(self._context)
        except Exception as exc:  # noqa: BLE001 - reported to the GUI verbatim
            # Never let an exception escape into the worker thread's event
            # loop: it would take the thread down with no trace in the UI.
            self.finished.emit(None, exc)
            return
        self.finished.emit(result, None)
