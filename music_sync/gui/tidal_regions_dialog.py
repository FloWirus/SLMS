"""Which regional Tidal catalogues cover lookups query."""

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..tidal_cover import COUNTRY_REGIONS, DEFAULT_SEARCH_COUNTRIES, country_name

# Checkboxes per row inside a region's box, before wrapping to the next
# column. Europe alone has 33 entries; one flat column would be taller than
# most screens.
COLUMNS = 4


class TidalRegionsDialog(QDialog):
    """Pick the regional catalogues Tidal cover lookups are pooled from.

    Tidal's search only returns albums licensed in the region asked for, so
    which regions are queried decides what can be found at all -- a Polish
    release is invisible to a US-only search. Each additional region is also
    one more sequential HTTP request per lookup, which is why this is a
    choice rather than "query everything": the dialog shows the count so the
    cost of a wide selection is visible while making it.
    """

    def __init__(self, selected: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialog_title_tidal_regions"))
        self._boxes: dict[str, QCheckBox] = {}

        root = QVBoxLayout(self)

        intro = QLabel(tr("tidal_regions_intro"))
        intro.setWordWrap(True)
        root.addWidget(intro)

        # Scrolled, so the dialog stays a sane height on a small screen even
        # though the full catalogue is 56 countries.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        chosen = set(selected)
        for region_key, codes in COUNTRY_REGIONS.items():
            group = QGroupBox(tr(region_key))
            grid = QGridLayout(group)
            for index, code in enumerate(codes):
                box = QCheckBox(country_name(code))
                box.setChecked(code in chosen)
                box.toggled.connect(self._update_state)
                self._boxes[code] = box
                grid.addWidget(box, index // COLUMNS, index % COLUMNS)
            content_layout.addWidget(group)
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        shortcut_row = QHBoxLayout()
        for label_key, handler in (
            ("btn_select_all", self._select_all),
            ("btn_select_none", self._select_none),
            ("btn_restore_defaults", self._restore_defaults),
        ):
            button = QPushButton(tr(label_key))
            button.clicked.connect(handler)
            shortcut_row.addWidget(button)
        shortcut_row.addStretch(1)
        self.count_label = QLabel()
        shortcut_row.addWidget(self.count_label)
        root.addLayout(shortcut_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("btn_save"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("btn_cancel"))
        self._ok_button = buttons.button(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        # Wide enough for the widest region box, so the country labels aren't
        # pushed behind a horizontal scrollbar ("DO — Dominican Republic" is
        # what sets the width); height is capped and scrolls vertically.
        margins = root.contentsMargins()
        chrome = 2 * scroll.frameWidth() + scroll.verticalScrollBar().sizeHint().width()
        self.resize(content.sizeHint().width() + chrome + margins.left() + margins.right(), 560)
        self._update_state()

    def selected_countries(self) -> list[str]:
        """The checked codes, in catalogue order rather than click order, so
        the saved setting doesn't churn just because the boxes were ticked in
        a different sequence."""
        return [code for code in self._boxes if self._boxes[code].isChecked()]

    def _set_all(self, codes) -> None:
        wanted = set(codes)
        for code, box in self._boxes.items():
            box.blockSignals(True)
            box.setChecked(code in wanted)
            box.blockSignals(False)
        self._update_state()

    def _select_all(self):
        self._set_all(self._boxes)

    def _select_none(self):
        self._set_all(())

    def _restore_defaults(self):
        self._set_all(DEFAULT_SEARCH_COUNTRIES)

    def _update_state(self):
        count = len(self.selected_countries())
        self.count_label.setText(tr("tidal_regions_count", count=count))
        # Saving an empty selection would mean "search nowhere". Rather than
        # silently substituting the defaults behind the user's back, the
        # dialog just won't close until at least one region is picked.
        self._ok_button.setEnabled(count > 0)
