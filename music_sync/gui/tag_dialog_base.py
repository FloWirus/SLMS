"""Shared machinery for the two tag editors.

TagEditDialog (one track at a time) and AlbumEditDialog (a whole album at
once) differ only in which fields they show and what saving means. Everything
around that -- the cover preview and its two buttons, the Tidal lookup and
its threading, the collapsible extended-tags panel -- was duplicated line for
line in both files, which is how the two copies had already started to drift.
It lives here once instead.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .. import album_covers
from .. import tags as tagsmod
from ..cover_utils import normalize_manual_cover
from ..i18n import tr
from .cover_compare_dialog import CoverCompareDialog
from .extended_tags_panel import PANEL_WIDTH as EXTENDED_PANEL_WIDTH
from .extended_tags_panel import ExtendedTagsPanel
from .tidal_cover_worker import TidalCoverRequest

COVER_SIZE = 150
TIDAL_COVER_SIZE = 1280


class CoverTagDialogBase(QDialog):
    """Cover preview + Tidal lookup + extended-tags panel.

    Subclasses build their own form, then call _build_cover_box(),
    _build_more_tags_button() and _attach_extended_panel() to place these
    pieces, and _load_cover_preview(path) whenever the edited file changes.
    They are expected to provide `artist_edit` and `album_edit` line edits --
    the Tidal lookup searches on whatever those currently hold, not on what
    was loaded, so a corrected artist name finds the right album.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._new_cover_bytes: bytes | None = None
        self._new_cover_mime = "image/jpeg"
        # Full-resolution copy of whatever the preview is showing.
        # cover_label only ever holds a COVER_SIZE thumbnail, so reading the
        # pixmap back off the label reports COVER_SIZE for every cover, no
        # matter how large the artwork actually is.
        self._cover_pixmap: QPixmap | None = None
        # Only covers fetched from Tidal are also written out as cover.jpg;
        # a manually picked file is left alone, since the user already has
        # that image on disk wherever they chose it from.
        self._new_cover_write_loose = False
        # The in-flight Tidal lookup, if any. Owned by the request itself
        # (see TidalCoverRequest) -- this is only a handle for cancelling it.
        self._tidal_request: TidalCoverRequest | None = None
        # The (artist, album) the running lookup was started with.
        self._tidal_query: tuple[str, str] = ("", "")

    # ---------- construction ----------

    def _build_cover_box(self) -> QVBoxLayout:
        cover_box = QVBoxLayout()
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(COVER_SIZE, COVER_SIZE)
        self.cover_label.setStyleSheet("border: 1px solid gray;")
        self.cover_label.setAlignment(Qt.AlignCenter)
        cover_box.addWidget(self.cover_label)

        self.cover_size_label = QLabel()
        self.cover_size_label.setAlignment(Qt.AlignCenter)
        cover_box.addWidget(self.cover_size_label)

        cover_btn = QPushButton(tr("btn_change_cover"))
        cover_btn.clicked.connect(self._choose_cover)
        cover_box.addWidget(cover_btn)

        self.tidal_cover_btn = QPushButton(tr("btn_download_cover_tidal"))
        self.tidal_cover_btn.clicked.connect(self._download_cover_from_tidal)
        cover_box.addWidget(self.tidal_cover_btn)

        cover_box.addStretch(1)
        return cover_box

    def _build_more_tags_button(self) -> QPushButton:
        self.more_tags_btn = QPushButton(tr("btn_more_tags"))
        self.more_tags_btn.setCheckable(True)
        self.more_tags_btn.toggled.connect(self._toggle_extended_panel)
        return self.more_tags_btn

    def _lock_more_tags_button_width(self, peer: QPushButton) -> None:
        """Give the More/Fewer tags button one fixed width that fits both of
        its labels, and at least matches `peer` (the nav button it sits under,
        so the two line up).

        Sizing it to the peer alone -- which is what this used to do -- cut
        the text off: "More tags >>>" and "Więcej tagów >>>" are both wider
        than "Next". Measuring both captions, in whatever language is loaded,
        also keeps the button from resizing as it toggles."""
        metrics = self.more_tags_btn.fontMetrics()
        widest_label = max(
            metrics.horizontalAdvance(tr("btn_more_tags")),
            metrics.horizontalAdvance(tr("btn_fewer_tags")),
        )
        # Whatever the current style puts around the label (frame, margins);
        # taken from the button itself rather than guessed at.
        chrome = self.more_tags_btn.sizeHint().width() - metrics.horizontalAdvance(self.more_tags_btn.text())
        self.more_tags_btn.setFixedWidth(max(peer.sizeHint().width(), widest_label + chrome))

    def _attach_extended_panel(self, root_layout, scope: str) -> None:
        # Hidden until "More tags" is toggled on -- widening the window only
        # then, rather than always reserving the space, keeps the common
        # case (editing the handful of basic fields) uncluttered. As a
        # sibling of the main panel (not nested inside it), showing/hiding it
        # can never affect that panel's own geometry.
        self.extended_panel = ExtendedTagsPanel(scope)
        self.extended_panel.setFixedWidth(EXTENDED_PANEL_WIDTH)
        self.extended_panel.setVisible(False)
        root_layout.addWidget(self.extended_panel)

    def _toggle_extended_panel(self, checked: bool):
        self.more_tags_btn.setText(tr("btn_fewer_tags") if checked else tr("btn_more_tags"))
        self.extended_panel.setVisible(checked)
        # Safe to just fit the window to its new sizeHint (rather than
        # guessing a resize delta): the main panel is a fixed-width sibling of
        # the panel, not something the layout can stretch, so this only ever
        # grows/shrinks the window on the right where the panel lives.
        self.adjustSize()

    # ---------- cover preview ----------

    def _show_cover_preview(self, pixmap: QPixmap | None):
        """Put `pixmap` in the cover preview, keeping the unscaled original
        on self. Everything that needs the artwork's real dimensions -- the
        size label, the Tidal comparison -- has to read that copy rather
        than cover_label's downscaled thumbnail."""
        self._cover_pixmap = pixmap
        if pixmap is None or pixmap.isNull():
            self.cover_label.clear()
            self.cover_label.setText(tr("cover_none"))
            self.cover_size_label.clear()
            return
        self.cover_label.setPixmap(
            pixmap.scaled(COVER_SIZE, COVER_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.cover_size_label.setText(f"{pixmap.width()}x{pixmap.height()}")

    def _load_cover_preview(self, path: Path):
        data = tagsmod.read_cover_art(path)
        if data:
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            self._show_cover_preview(pixmap)
        else:
            self._show_cover_preview(None)

    def _reset_pending_cover(self) -> None:
        """Forget an unsaved cover pick and any lookup still running for the
        previous track/album. Called by subclasses when they load another
        one."""
        self._new_cover_bytes = None
        self._new_cover_mime = "image/jpeg"
        self._new_cover_write_loose = False
        self._cancel_tidal_request()

    def _choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dialog_choose_cover_title"), "", tr("images_filter")
        )
        if not path:
            return
        image_path = Path(path)
        try:
            raw = image_path.read_bytes()
        except OSError as exc:
            QMessageBox.critical(self, tr("error_save_title"), str(exc))
            return
        cover_bytes, mime = normalize_manual_cover(raw, image_path.suffix)
        self._new_cover_bytes = cover_bytes
        self._new_cover_mime = mime
        self._new_cover_write_loose = False
        pixmap = QPixmap()
        pixmap.loadFromData(cover_bytes)
        self._show_cover_preview(pixmap)

    # ---------- Tidal lookup ----------

    def _download_cover_from_tidal(self):
        artist = self.artist_edit.text().strip()
        album = self.album_edit.text().strip()
        if not artist or not album:
            QMessageBox.information(
                self, tr("msg_tidal_missing_fields_title"), tr("msg_tidal_missing_fields_text")
            )
            return

        # A second lookup replaces the first: the button below is disabled
        # while one runs, but navigation can still have started another.
        self._cancel_tidal_request()
        self.tidal_cover_btn.setEnabled(False)
        self.tidal_cover_btn.setText(tr("btn_downloading_cover_tidal"))

        # Not kept on self as a raw QThread: the lookup has to survive this
        # dialog being closed mid-request (see TidalCoverRequest), and the
        # result is dropped rather than applied if it no longer belongs to
        # what is on screen.
        # Kept so the confirmation dialog can show what the search actually
        # ran with, and offer to re-run it with something else.
        self._tidal_query = (artist, album)
        self._tidal_request = TidalCoverRequest(
            artist, album, TIDAL_COVER_SIZE, self._on_tidal_cover_finished
        )

    def _cancel_tidal_request(self) -> None:
        """Detach from a lookup still in flight and put the button back.

        Called when the user navigates to another track/album or closes the
        dialog: the artwork that comes back was searched for with the
        previous one's artist/album, so applying it here would silently stamp
        the wrong cover onto the file now being edited."""
        if self._tidal_request is None:
            return
        self._tidal_request.cancel()
        self._tidal_request = None
        self.tidal_cover_btn.setEnabled(True)
        self.tidal_cover_btn.setText(tr("btn_download_cover_tidal"))

    def done(self, result: int) -> None:
        # Covers every way out of the dialog (Save, Cancel, Esc, window
        # close), all of which funnel through QDialog.done().
        self._cancel_tidal_request()
        super().done(result)

    def _on_tidal_cover_finished(self, data: bytes, error: str):
        self._tidal_request = None
        self.tidal_cover_btn.setEnabled(True)
        self.tidal_cover_btn.setText(tr("btn_download_cover_tidal"))

        if error:
            QMessageBox.warning(self, tr("error_tidal_cover_title"), tr("error_tidal_cover_text", error=error))
            return

        artist, album = self._tidal_query
        confirm = CoverCompareDialog(self._cover_pixmap, data, artist, album, TIDAL_COVER_SIZE, self)
        if confirm.exec() != QDialog.Accepted:
            return

        # Not necessarily the cover that was handed to the dialog: its own
        # "Search again" may have replaced the candidate with a better match.
        cover_bytes, mime = normalize_manual_cover(confirm.cover_bytes, ".jpg")
        new_pixmap = QPixmap()
        new_pixmap.loadFromData(cover_bytes)

        self._new_cover_bytes = cover_bytes
        self._new_cover_mime = mime
        self._new_cover_write_loose = True
        self._show_cover_preview(new_pixmap)

    def _write_loose_covers(self, album_dirs) -> list[str]:
        """Drop the fetched cover.jpg into each directory holding an edited
        file, so file managers and players that read cover.jpg (rather than
        embedded art) see it too. Returns the errors, if any.

        Once per directory rather than once per track: a multi-disc album
        spread over CD1/, CD2/, ... needs one in each, but writing it twenty
        times into the same folder is pointless."""
        errors: list[str] = []
        if self._new_cover_bytes is None or not self._new_cover_write_loose:
            return errors
        for album_dir in sorted(set(album_dirs)):
            try:
                album_covers.write_loose_cover(album_dir, self._new_cover_bytes)
            except OSError as exc:
                errors.append(f"{album_dir}: {exc}")
        return errors
