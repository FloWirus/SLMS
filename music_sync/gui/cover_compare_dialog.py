from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from .tidal_cover_worker import TidalCoverRequest

PREVIEW_SIZE = 200


class _CoverPreview(QWidget):
    """One titled column of the comparison: a fixed-size thumbnail with the
    artwork's real pixel dimensions underneath."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        column.addWidget(title_label)

        self._image_label = QLabel()
        self._image_label.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        self._image_label.setStyleSheet("border: 1px solid gray;")
        self._image_label.setAlignment(Qt.AlignCenter)
        column.addWidget(self._image_label)

        self._size_label = QLabel()
        self._size_label.setAlignment(Qt.AlignCenter)
        column.addWidget(self._size_label)

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self._image_label.clear()
            self._image_label.setText(tr("cover_none"))
            self._size_label.clear()
            return
        self._image_label.setPixmap(
            pixmap.scaled(PREVIEW_SIZE, PREVIEW_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        # The unscaled size, not the thumbnail's: which of the two covers is
        # actually higher resolution is the whole point of this dialog.
        self._size_label.setText(f"{pixmap.width()}x{pixmap.height()}")


class CoverCompareDialog(QDialog):
    """Shown after a Tidal cover lookup, side-by-side with the track/album's
    current cover, so the user confirms the match before it replaces anything.

    Also where a wrong match gets corrected: the artist/album the search ran
    with are shown in two editable fields, and "Search again" re-runs the
    lookup with whatever they now hold. Those fields are search terms and
    nothing else -- they are never written to the file's tags. The dialog is
    often the moment you find out the tags are misspelled, and fixing the
    spelling here to find the right cover must not quietly rewrite the tag
    you were only using as a hint; correcting the tags themselves stays a
    deliberate edit in the form behind this dialog.

    `cover_bytes` holds the candidate currently on the right -- the initial
    one, or whatever the last successful re-search returned.
    """

    def __init__(
        self,
        old_pixmap: QPixmap | None,
        cover_bytes: bytes,
        artist: str,
        album: str,
        size: int,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("dialog_title_confirm_cover"))
        self.cover_bytes = cover_bytes
        self._size = size
        self._request: TidalCoverRequest | None = None

        root = QVBoxLayout(self)

        row = QHBoxLayout()
        current = _CoverPreview(tr("cover_current"))
        current.set_pixmap(old_pixmap)
        row.addWidget(current)
        self._found = _CoverPreview(tr("cover_found_tidal"))
        row.addWidget(self._found)
        root.addLayout(row)
        self._show_candidate(cover_bytes)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel(tr("field_artist")))
        self.artist_edit = QLineEdit(artist)
        search_row.addWidget(self.artist_edit, 1)
        search_row.addWidget(QLabel(tr("field_album")))
        self.album_edit = QLineEdit(album)
        search_row.addWidget(self.album_edit, 1)
        self.search_btn = QPushButton(tr("btn_search_again"))
        self.search_btn.clicked.connect(self._search_again)
        # Fixed to fit both of its captions, so starting a search doesn't
        # resize the button (and with it the row) under the cursor.
        metrics = self.search_btn.fontMetrics()
        chrome = self.search_btn.sizeHint().width() - metrics.horizontalAdvance(self.search_btn.text())
        self.search_btn.setFixedWidth(
            chrome
            + max(
                metrics.horizontalAdvance(tr("btn_search_again")),
                metrics.horizontalAdvance(tr("btn_searching_cover_tidal")),
            )
        )
        search_row.addWidget(self.search_btn)
        root.addLayout(search_row)

        # Enter in either field searches rather than accepting the dialog,
        # which is what a text field next to a search button should do (and
        # QDialog would otherwise treat it as pressing the default button).
        self.artist_edit.returnPressed.connect(self._search_again)
        self.album_edit.returnPressed.connect(self._search_again)

        self.status_label = QLabel(tr("cover_search_hint"))
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        self._accept_button = buttons.button(QDialogButtonBox.Yes)
        self._accept_button.setText(tr("btn_use_cover"))
        buttons.button(QDialogButtonBox.No).setText(tr("btn_cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _show_candidate(self, data: bytes | None) -> None:
        pixmap = QPixmap()
        if data:
            pixmap.loadFromData(data)
        self._found.set_pixmap(pixmap if data else None)

    def _search_again(self):
        artist = self.artist_edit.text().strip()
        album = self.album_edit.text().strip()
        if not artist or not album:
            self.status_label.setText(tr("msg_tidal_missing_fields_text"))
            return
        if self._request is not None:
            return

        self.search_btn.setEnabled(False)
        self.search_btn.setText(tr("btn_searching_cover_tidal"))
        self.status_label.setText(tr("cover_search_running"))
        # Accepting mid-search would take the cover currently on the right,
        # which is not the one being looked up.
        self._accept_button.setEnabled(False)
        self._request = TidalCoverRequest(artist, album, self._size, self._on_search_finished)

    def _on_search_finished(self, data: bytes, error: str):
        self._request = None
        self.search_btn.setEnabled(True)
        self.search_btn.setText(tr("btn_search_again"))
        self._accept_button.setEnabled(True)

        if error:
            # The previous candidate stays on screen and stays acceptable:
            # a failed re-search must not cost the user the cover they
            # already had in front of them.
            self.status_label.setText(tr("error_tidal_cover_text", error=error))
            return

        self.cover_bytes = data
        self._show_candidate(data)
        self.status_label.setText(tr("cover_search_hint"))

    def done(self, result: int) -> None:
        # Covers Yes/No, Esc and the window close button. The lookup itself
        # runs to completion on its own thread (see TidalCoverRequest); this
        # only detaches from its result.
        if self._request is not None:
            self._request.cancel()
            self._request = None
        super().done(result)
