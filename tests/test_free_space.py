"""The pre-flight free-space check for library -> device syncs.

Exercised through the unbound methods with a stand-in for the window: the
logic only reads `device_hashes` and `selected_device`, and building a real
MainWindow would drag in devices, databases and a display for no extra
coverage.
"""

import types

import pytest

from music_sync.db import Track

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from music_sync.gui import main_window as mw  # noqa: E402


def track(hash_: str, size: int) -> Track:
    return Track(id=None, path=f"{hash_}.wav", filename=f"{hash_}.wav", hash=hash_,
                 source_hash=hash_, size=size, format="wav")


@pytest.fixture
def window():
    stub = types.SimpleNamespace(
        device_hashes=set(),
        selected_device=types.SimpleNamespace(mountpoint="/nowhere"),
    )
    # _confirm_free_space calls this on self, so the stub needs it bound too.
    stub._tracks_to_be_copied = types.MethodType(mw.MainWindow._tracks_to_be_copied, stub)
    return stub


@pytest.fixture
def free_space(monkeypatch):
    """Pretend the device has exactly this many bytes free."""

    def set_free(value):
        monkeypatch.setattr(
            mw.shutil, "disk_usage",
            lambda path: types.SimpleNamespace(total=value * 2, used=value, free=value),
        )

    return set_free


@pytest.fixture
def warnings(monkeypatch):
    """Records the warnings shown; answers "no, don't sync"."""
    shown = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: (shown.append(args[2]), QMessageBox.No)[1]),
    )
    return shown


def confirm(window, tracks, force=False):
    return mw.MainWindow._confirm_free_space(window, tracks, force)


def test_everything_counts_when_the_device_is_empty(window):
    tracks = [track("a", 10), track("b", 20)]
    assert mw.MainWindow._tracks_to_be_copied(window, tracks, False) == tracks


def test_tracks_already_on_the_device_are_not_counted(window):
    window.device_hashes = {"a"}
    tracks = [track("a", 10), track("b", 20)]
    assert [t.hash for t in mw.MainWindow._tracks_to_be_copied(window, tracks, False)] == ["b"]


def test_force_counts_everything_again(window):
    window.device_hashes = {"a", "b"}
    tracks = [track("a", 10), track("b", 20)]
    assert mw.MainWindow._tracks_to_be_copied(window, tracks, True) == tracks


def test_a_sync_that_fits_asks_nothing(window, free_space, warnings):
    free_space(10 * mw.FREE_SPACE_RESERVE)
    assert confirm(window, [track("a", 1000)]) is True
    assert warnings == []


def test_a_sync_that_does_not_fit_warns(window, free_space, warnings):
    free_space(500)
    assert confirm(window, [track("a", 1000)]) is False
    assert len(warnings) == 1


def test_the_reserve_keeps_the_card_from_being_filled_to_the_last_byte(window, free_space, warnings):
    free_space(1000 + mw.FREE_SPACE_RESERVE // 2)
    assert confirm(window, [track("a", 1000)]) is False


def test_nothing_left_to_copy_never_warns(window, free_space, warnings):
    """Re-syncing an up-to-date card writes nothing, so a full card is fine."""
    window.device_hashes = {"a"}
    free_space(1)
    assert confirm(window, [track("a", 10**9)]) is True
    assert warnings == []


def test_an_unmeasurable_device_does_not_block_the_sync(window, monkeypatch, warnings):
    def boom(path):
        raise OSError("no such device")

    monkeypatch.setattr(mw.shutil, "disk_usage", boom)
    assert confirm(window, [track("a", 10**9)]) is True
    assert warnings == []


def test_the_warning_names_the_count_and_both_sizes(window, free_space, warnings):
    free_space(1000)
    confirm(window, [track("a", 4000), track("b", 4000)])
    message = warnings[0]
    assert "2" in message and "7.8 KB" in message and "1000.0 B" in message
