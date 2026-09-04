"""Remembering which artists are collapsed and which albums are open.

The state is stored as the deviations from the default shape (artists open,
albums closed), which is what lets a freshly scanned album take the default
while everything the user arranged stays put -- across a tree rebuild and
across a restart.
"""

import types

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem  # noqa: E402

from music_sync.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def window():
    """Only the tree helpers are exercised; they touch nothing else on self."""
    stub = types.SimpleNamespace()
    stub._tree_node_key = MainWindow._tree_node_key
    return stub


def build_tree(structure: dict[str, list[str]]) -> QTreeWidget:
    """A tree shaped like the one _populate_tree builds, in its default state."""
    tree = QTreeWidget()
    for artist, albums in structure.items():
        artist_item = QTreeWidgetItem([artist])
        artist_item.setData(0, Qt.UserRole, {"type": "artist", "artist": artist})
        artist_item.setExpanded(True)
        for album in albums:
            album_item = QTreeWidgetItem([album])
            album_item.setData(0, Qt.UserRole, {"type": "album", "artist": artist, "album": album})
            artist_item.addChild(album_item)
        tree.addTopLevelItem(artist_item)
        artist_item.setExpanded(True)
    return tree


def shape(tree: QTreeWidget):
    return [
        (
            tree.topLevelItem(i).text(0),
            tree.topLevelItem(i).isExpanded(),
            [
                (tree.topLevelItem(i).child(j).text(0), tree.topLevelItem(i).child(j).isExpanded())
                for j in range(tree.topLevelItem(i).childCount())
            ],
        )
        for i in range(tree.topLevelItemCount())
    ]


LIBRARY = {"Alpha": ["One", "Two"], "Beta": ["Three"]}


def test_an_untouched_tree_has_nothing_to_remember(qt_app, window):
    tree = build_tree(LIBRARY)
    assert MainWindow._tree_expansion_state(window, tree) == (set(), set())


def test_state_records_collapsed_artists_and_opened_albums(qt_app, window):
    tree = build_tree(LIBRARY)
    tree.topLevelItem(0).setExpanded(False)
    tree.topLevelItem(1).child(0).setExpanded(True)
    assert MainWindow._tree_expansion_state(window, tree) == ({"Alpha"}, {("Beta", "Three")})


def test_restoring_onto_a_rebuilt_tree(qt_app, window):
    state = ({"Alpha"}, {("Beta", "Three")})
    rebuilt = build_tree(LIBRARY)
    MainWindow._restore_tree_expansion(window, rebuilt, state)
    assert shape(rebuilt) == [
        ("Alpha", False, [("One", False), ("Two", False)]),
        ("Beta", True, [("Three", True)]),
    ]


def test_a_node_the_state_never_saw_gets_the_default(qt_app, window):
    """A newly scanned album opens closed; a new artist opens expanded."""
    state = ({"Alpha"}, {("Beta", "Three")})
    grown = build_tree({"Alpha": ["One", "Two", "Brand New"], "Beta": ["Three"], "Gamma": ["Solo"]})
    MainWindow._restore_tree_expansion(window, grown, state)
    result = dict((artist, (expanded, dict(albums))) for artist, expanded, albums in shape(grown))
    assert result["Alpha"][0] is False and result["Alpha"][1]["Brand New"] is False
    assert result["Gamma"][0] is True and result["Gamma"][1]["Solo"] is False


def test_a_round_trip_through_the_tree_is_lossless(qt_app, window):
    tree = build_tree(LIBRARY)
    tree.topLevelItem(0).setExpanded(False)
    tree.topLevelItem(0).child(1).setExpanded(True)
    state = MainWindow._tree_expansion_state(window, tree)

    rebuilt = build_tree(LIBRARY)
    MainWindow._restore_tree_expansion(window, rebuilt, state)
    assert shape(rebuilt) == shape(tree)


def test_settings_round_trip():
    state = ({"Alpha", "Beta"}, {("Beta", "Three"), ("Alpha", "One")})
    collapsed, expanded = MainWindow._expansion_to_settings(state)
    # Sorted, so saving twice doesn't rewrite settings.json differently.
    assert collapsed == ["Alpha", "Beta"]
    assert expanded == [["Alpha", "One"], ["Beta", "Three"]]
    assert MainWindow._expansion_from_settings(collapsed, expanded) == state


def test_malformed_settings_entries_are_ignored():
    collapsed, expanded = MainWindow._expansion_from_settings(
        ["Alpha", 7, None],
        [["Beta", "Three"], ["only-one"], "nonsense", ["a", "b", "c"], [1, 2]],
    )
    assert collapsed == {"Alpha"}
    assert expanded == {("Beta", "Three")}
