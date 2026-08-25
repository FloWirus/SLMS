"""The cover confirmation dialog's re-search.

Its artist/album fields are search terms: the whole point is being able to
correct a misspelled tag *for the lookup* without that correction leaking
into the file. This pins that down, plus the states around a running search.
"""

import time

import pytest

from music_sync import tags as tagsmod, tidal_cover
from music_sync.gui import tidal_cover_worker

pytest.importorskip("PySide6")

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from music_sync.gui.cover_compare_dialog import CoverCompareDialog  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def fake_tidal(monkeypatch):
    """Records what was searched for and answers with identifiable bytes."""
    queries = []

    def download(artist, album, size=1280):
        queries.append((artist, album))
        if artist == "missing":
            raise RuntimeError("Album not found on Tidal")
        return f"cover for {artist}/{album}".encode()

    monkeypatch.setattr(tidal_cover, "download_cover_bytes", download)
    return queries


def pump(app, predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def make_dialog(artist="tagged artist", album="tagged album", cover=b"initial cover"):
    return CoverCompareDialog(QPixmap(), cover, artist, album, 1280)


def test_fields_start_from_what_was_searched_for(qt_app, fake_tidal):
    dialog = make_dialog()
    assert (dialog.artist_edit.text(), dialog.album_edit.text()) == ("tagged artist", "tagged album")
    dialog.done(QDialog.Rejected)


def test_search_again_uses_the_edited_terms(qt_app, fake_tidal):
    dialog = make_dialog()
    dialog.artist_edit.setText("corrected artist")
    dialog.album_edit.setText("corrected album")
    dialog.search_btn.click()
    assert pump(qt_app, lambda: dialog._request is None)

    assert fake_tidal[-1] == ("corrected artist", "corrected album")
    assert dialog.cover_bytes == b"cover for corrected artist/corrected album"
    dialog.done(QDialog.Rejected)


def test_a_failed_search_keeps_the_previous_candidate(qt_app, fake_tidal):
    dialog = make_dialog()
    dialog.artist_edit.setText("missing")
    dialog.search_btn.click()
    assert pump(qt_app, lambda: dialog._request is None)

    assert dialog.cover_bytes == b"initial cover"
    assert dialog.search_btn.isEnabled()
    dialog.done(QDialog.Rejected)


def test_accepting_is_blocked_while_a_search_runs(qt_app, fake_tidal):
    dialog = make_dialog()
    dialog.search_btn.click()
    assert not dialog._accept_button.isEnabled()
    assert pump(qt_app, lambda: dialog._request is None)
    assert dialog._accept_button.isEnabled()
    dialog.done(QDialog.Rejected)


def test_an_empty_field_searches_for_nothing(qt_app, fake_tidal):
    dialog = make_dialog()
    dialog.album_edit.setText("   ")
    dialog.search_btn.click()
    assert fake_tidal == [] and dialog._request is None
    dialog.done(QDialog.Rejected)


def test_closing_mid_search_leaves_no_pending_request(qt_app, fake_tidal):
    dialog = make_dialog()
    dialog.search_btn.click()
    dialog.done(QDialog.Rejected)
    assert pump(qt_app, lambda: not tidal_cover_worker._ACTIVE)


def test_editing_the_search_terms_never_touches_the_tags(qt_app, fake_tidal, wav_factory):
    """The invariant this dialog exists to protect: correcting a misspelled
    artist here changes what is searched for, not what is on disk."""
    path = wav_factory("track.wav")
    original = {"artist": "tagged artist", "album": "tagged album", "title": "T",
                "track_number": "1", "track_total": "1", "disc_number": "1",
                "year": "2001", "genre": "Rock"}
    tagsmod.write_tags(path, original)

    dialog = make_dialog()
    dialog.artist_edit.setText("corrected artist")
    dialog.album_edit.setText("corrected album")
    dialog.search_btn.click()
    assert pump(qt_app, lambda: dialog._request is None)
    dialog.done(QDialog.Accepted)

    assert tagsmod.read_tags(path) == original
