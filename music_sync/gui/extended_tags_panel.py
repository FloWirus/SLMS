from pathlib import Path

from PySide6.QtWidgets import QFormLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from .. import tags as tagsmod
from ..i18n import tr

PANEL_WIDTH = 320


class ExtendedTagsPanel(QWidget):
    """Inline panel of extended tags (see tags.EXTENDED_FIELDS) for a given
    scope ("track" or "album"), meant to be embedded directly next to a
    TagEditDialog/AlbumEditDialog's basic form -- not a separate popup --
    so cover art, basic tags, extended tags and Next/Previous navigation
    all belong to the same editing session and save together in one write.

    Which fields are shown depends on the current track's audio format
    (different formats support different extended tags -- see
    tags.editable_extended_fields), so the field list is rebuilt via
    set_format() whenever the format changes, not just once at construction.
    The embedding dialog owns load_values()/current_values() timing --
    this widget holds no state beyond its own line edits.
    """

    def __init__(self, scope: str, parent=None):
        super().__init__(parent)
        self.scope = scope
        self._format: str | None = None
        self._edits: dict[str, QLineEdit] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        outer.addWidget(self._scroll)
        self._rebuild(None)

    def set_format(self, fmt: str) -> None:
        if fmt == self._format:
            return
        self._format = fmt
        self._rebuild(fmt)

    def _rebuild(self, fmt: str | None) -> None:
        self._edits = {}
        content = QWidget()
        fields = tagsmod.editable_extended_fields(fmt, self.scope) if fmt else []
        if fields:
            form = QFormLayout(content)
            for field in fields:
                edit = QLineEdit()
                self._edits[field.key] = edit
                form.addRow(tr(field.label_i18n_key), edit)
        else:
            layout = QVBoxLayout(content)
            layout.addWidget(QLabel(tr("extended_tags_none_available")))
            layout.addStretch(1)
        self._scroll.setWidget(content)

    def load_values(self, path: Path) -> None:
        values = tagsmod.read_extended_tags(path, list(self._edits.keys())) if self._edits else {}
        for key, edit in self._edits.items():
            edit.setText(values.get(key, ""))

    def current_values(self) -> dict[str, str]:
        return {key: edit.text() for key, edit in self._edits.items()}
