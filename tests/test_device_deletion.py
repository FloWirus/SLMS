"""Deleting tracks from a connected device through the context menu.

A multi-row selection (Ctrl/Shift-click, then right-click one of the
highlighted rows) used to only delete the single row that was right-clicked
-- the menu action ignored the rest of the selection entirely, so deleting
"the selected tracks" silently left most of them behind. This pins the
consolidated confirm-and-delete helpers both context menus now go through.
"""

import pytest

from music_sync import devices as devicesmod, tags as tagsmod

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from music_sync.gui.main_window import MainWindow  # noqa: E402

from conftest import write_wav  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def fake_device(tmp_path, monkeypatch):
    mountpoint = tmp_path / "device"
    mountpoint.mkdir()
    device = devicesmod.StorageDevice(
        name="fake", path="/dev/null", mountpoint=str(mountpoint), label="FAKE",
        size="1G", removable=True, disk_path="/dev/null",
    )
    monkeypatch.setattr(devicesmod, "list_storage_devices", lambda: [device])
    return mountpoint


@pytest.fixture
def synced_window(qt_app, tmp_path, fake_device, monkeypatch):
    """A window with a library of three tracks already synced to the fake
    device, ready to have some of them deleted."""
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))

    library_root = tmp_path / "library"
    for index in (1, 2, 3):
        path = write_wav(library_root / "Artist" / "Album" / f"{index:02d}.wav", frequency=400 + 30 * index)
        tagsmod.write_tags(path, {
            "artist": "Artist", "album": "Album", "title": f"Track {index}",
            "track_number": str(index), "track_total": "3", "disc_number": "1",
            "year": "2001", "genre": "Rock",
        })

    window = MainWindow(tmp_path / "data")
    window.source_root = library_root
    window.library_db = window._open_library_db(library_root)
    window._rescan_source()
    window._refresh_devices()
    window.device_combo.setCurrentIndex(1)
    window._run_sync()
    assert len(list(fake_device.rglob("*.wav"))) == 3
    return window


def test_deleting_two_selected_tracks_from_the_device_removes_both(synced_window, fake_device):
    device_tracks = synced_window.device_db.all_tracks()
    selected = device_tracks[:2]

    synced_window._confirm_and_delete_device_tracks(selected)

    remaining_files = list(fake_device.rglob("*.wav"))
    remaining_rows = synced_window.device_db.all_tracks()
    assert len(remaining_files) == 1
    assert len(remaining_rows) == 1
    assert remaining_rows[0].title == device_tracks[2].title


def test_deleting_a_single_track_still_works(synced_window, fake_device):
    device_tracks = synced_window.device_db.all_tracks()

    synced_window._confirm_and_delete_device_tracks([device_tracks[0]])

    assert len(list(fake_device.rglob("*.wav"))) == 2
    assert len(synced_window.device_db.all_tracks()) == 2


def test_declining_the_confirmation_deletes_nothing(synced_window, fake_device, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))

    synced_window._confirm_and_delete_device_tracks(synced_window.device_db.all_tracks())

    assert len(list(fake_device.rglob("*.wav"))) == 3


def test_the_confirmation_names_the_count_for_multiple_tracks(synced_window, monkeypatch):
    seen = {}

    def question(self, title, text, *args, **kwargs):
        seen["text"] = text
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", question)
    synced_window._confirm_and_delete_device_tracks(synced_window.device_db.all_tracks()[:2])
    assert "2" in seen["text"]


def test_deleting_from_the_library_panels_context_menu_resolves_by_source_hash(synced_window, fake_device):
    """The library panel's "delete from device" (single or multi) has to map
    library tracks to their device copies through source_hash, since the
    device Track objects it starts from are different rows entirely."""
    library_tracks = synced_window.library_db.all_tracks()

    synced_window._delete_from_device_tracks(library_tracks)

    assert list(fake_device.rglob("*.wav")) == []
    assert synced_window.device_db.all_tracks() == []


def test_a_library_track_never_synced_is_silently_skipped(synced_window, fake_device):
    """Deleting-from-device for a track that isn't actually on the device
    (e.g. a mixed selection) must not error -- it's just not there to delete."""
    unsynced = write_wav(synced_window.source_root / "Artist" / "Album" / "99.wav", frequency=999)
    tagsmod.write_tags(unsynced, {
        "artist": "Artist", "album": "Album", "title": "Unsynced",
        "track_number": "99", "track_total": "3", "disc_number": "1",
        "year": "2001", "genre": "Rock",
    })
    synced_window._rescan_source()
    unsynced_track = next(t for t in synced_window.library_db.all_tracks() if t.title == "Unsynced")

    before = len(list(fake_device.rglob("*.wav")))
    synced_window._delete_from_device_tracks([unsynced_track])
    assert len(list(fake_device.rglob("*.wav"))) == before


def test_a_multi_selection_right_click_shows_exactly_one_delete_action(synced_window, monkeypatch):
    """Two delete entries for one gesture ("Delete from device" and "Delete
    N selected from device") is confusing when the user already expressed
    intent by selecting several rows and right-clicking one of them -- there
    is exactly one sensible reading of "delete" for that gesture."""
    from PySide6.QtWidgets import QMenu

    from music_sync.gui import main_window as mw

    captured = []

    class RecordingMenu(QMenu):
        def exec(self, *args, **kwargs):
            captured.append([action.text() for action in self.actions()])
            return None

    monkeypatch.setattr(mw, "QMenu", RecordingMenu)

    device_tracks = synced_window.device_db.all_tracks()
    library_tracks = synced_window.library_db.all_tracks()
    synced_window.device_hashes = synced_window.device_db.source_hashes()

    def delete_labels():
        return [label for label in captured[-1] if "delete" in label.lower() or "usu" in label.lower()]

    synced_window._show_device_track_menu(device_tracks[0], device_tracks, 0, synced_window.pos(), device_tracks[:2])
    labels = delete_labels()
    assert len(labels) == 1
    assert "2" in labels[0]

    synced_window._show_device_track_menu(device_tracks[0], device_tracks, 0, synced_window.pos(), None)
    assert len(delete_labels()) == 1

    synced_window._show_track_menu(library_tracks[0], library_tracks, 0, synced_window.pos(), library_tracks[:2])
    labels = delete_labels()
    device_labels = [label for label in labels if "device" in label.lower()]
    library_labels = [label for label in labels if label not in device_labels]
    assert len(device_labels) == 1 and "2" in device_labels[0]
    assert len(library_labels) == 1 and "2" in library_labels[0]
