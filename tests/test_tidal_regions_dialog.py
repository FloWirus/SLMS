"""The region picker behind Settings → cover search regions."""

import pytest

from music_sync import tidal_cover

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from music_sync.gui.tidal_regions_dialog import TidalRegionsDialog  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


def test_every_catalogue_country_has_a_checkbox(qt_app):
    dialog = TidalRegionsDialog([])
    assert set(dialog._boxes) == set(tidal_cover.KNOWN_COUNTRIES)
    dialog.done(QDialog.Rejected)


def test_starts_from_the_saved_selection(qt_app):
    dialog = TidalRegionsDialog(["PL", "US"])
    assert dialog.selected_countries() == ["PL", "US"]
    dialog.done(QDialog.Rejected)


def test_selection_comes_back_in_catalogue_order(qt_app):
    """Not click order -- otherwise the saved setting churns for no reason."""
    dialog = TidalRegionsDialog([])
    dialog._boxes["US"].setChecked(True)
    dialog._boxes["DE"].setChecked(True)
    assert dialog.selected_countries() == ["DE", "US"]
    dialog.done(QDialog.Rejected)


def test_saving_nothing_is_not_allowed(qt_app):
    dialog = TidalRegionsDialog(["PL"])
    dialog._select_none()
    assert not dialog._ok_button.isEnabled()
    dialog._boxes["PL"].setChecked(True)
    assert dialog._ok_button.isEnabled()
    dialog.done(QDialog.Rejected)


def test_shortcut_buttons(qt_app):
    dialog = TidalRegionsDialog([])
    dialog._select_all()
    assert len(dialog.selected_countries()) == len(tidal_cover.KNOWN_COUNTRIES)
    dialog._restore_defaults()
    assert set(dialog.selected_countries()) == set(tidal_cover.DEFAULT_SEARCH_COUNTRIES)
    dialog.done(QDialog.Rejected)
