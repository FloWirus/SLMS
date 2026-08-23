from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import album_covers
from .. import tags as tagsmod
from ..cover_utils import normalize_manual_cover
from ..db import Track
from ..i18n import tr
from ..settings import Settings
from .cover_compare_dialog import CoverCompareDialog
from .extended_tags_panel import PANEL_WIDTH as EXTENDED_PANEL_WIDTH
from .extended_tags_panel import ExtendedTagsPanel
from .tidal_cover_worker import TidalCoverWorker

# Separator between per-file values when a field isn't the same across the
# edited tracks: "artist1;artist2;artist3". Editing one segment retargets
# that one file; replacing the whole list with a single value applies it to
# all of them (see _values_for_field).
MULTI_VALUE_SEPARATOR = ";"
COVER_SIZE = 150
DIALOG_MIN_SIZE = (650, 480)
TIDAL_COVER_SIZE = 1280

OnSavedCallback = Callable[[Track, dict], Track | None]


class AlbumEditDialog(QDialog):
    """Edits album-wide tags (artist, album, year, genre, track total, cover)
    for every track in an album at once. Per-track fields (title, track
    number, disc number) are left untouched. Next/Previous switch between
    whole albums, not individual tracks."""

    def __init__(
        self,
        source_root: Path,
        albums: list[list[Track]],
        start_index: int,
        on_saved: OnSavedCallback,
        settings: Settings,
        parent=None,
    ):
        super().__init__(parent)
        self.source_root = Path(source_root)
        self.albums = [list(tracks) for tracks in albums]
        self.index = start_index
        self.on_saved = on_saved
        self.settings = settings
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
        self._tidal_thread: QThread | None = None
        self._tidal_worker: TidalCoverWorker | None = None

        self._build_ui()
        self._load_index(self.index)
        self.adjustSize()

    @property
    def tracks(self) -> list[Track]:
        return self.albums[self.index]

    def _build_ui(self):
        # Root is horizontal: a fixed-width "main panel" (everything below,
        # unchanged) plus the extended-tags panel. Because main_panel never
        # resizes, nothing inside it (fields, cover buttons, nav/Save/Cancel)
        # ever moves when the extended panel is shown/hidden -- toggling it
        # only grows or shrinks the window on the right, where the panel is.
        root_layout = QHBoxLayout(self)
        main_panel = QWidget()
        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(0, 0, 0, 0)
        # Top-aligned so a taller extended panel (it has far more fields than
        # the base form) grows the window's height without stretching
        # main_panel to match -- that would shift Prev/Next/Save downward.
        root_layout.addWidget(main_panel, 0, Qt.AlignTop)

        content_row = QHBoxLayout()
        left_column = QVBoxLayout()

        top_row = QHBoxLayout()

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
        top_row.addLayout(cover_box)

        form_layout = QFormLayout()
        self.artist_edit = QLineEdit()
        self.album_edit = QLineEdit()
        self.track_total_edit = QLineEdit()
        self.year_edit = QLineEdit()
        self.genre_edit = QLineEdit()

        form_layout.addRow(tr("field_artist"), self.artist_edit)
        form_layout.addRow(tr("field_album"), self.album_edit)
        form_layout.addRow(tr("field_track_total"), self.track_total_edit)
        form_layout.addRow(tr("field_year"), self.year_edit)
        form_layout.addRow(tr("field_genre"), self.genre_edit)

        top_row.addLayout(form_layout)
        left_column.addLayout(top_row)

        self.info_label = QLabel()
        left_column.addWidget(self.info_label)

        action_row = QHBoxLayout()
        self.more_tags_btn = QPushButton(tr("btn_more_tags"))
        self.more_tags_btn.setCheckable(True)
        self.more_tags_btn.toggled.connect(self._toggle_extended_panel)
        action_row.addWidget(self.more_tags_btn)
        left_column.addLayout(action_row)

        content_row.addLayout(left_column)
        main_layout.addLayout(content_row)

        nav_row = QHBoxLayout()
        self.prev_btn = QPushButton(tr("btn_prev_album"))
        self.prev_btn.clicked.connect(self._go_previous)
        nav_row.addWidget(self.prev_btn)

        self.position_label = QLabel()
        self.position_label.setAlignment(Qt.AlignCenter)
        # Stretch factor on the label (not the buttons) so Prev/Next keep
        # their natural size instead of growing to fill leftover width.
        nav_row.addWidget(self.position_label, 1)

        self.next_btn = QPushButton(tr("btn_next_album"))
        self.next_btn.clicked.connect(self._go_next)
        nav_row.addWidget(self.next_btn)
        main_layout.addLayout(nav_row)

        # Matches the Next album button's width regardless of language/text
        # length, per the request that this button look like a peer of the
        # nav row.
        self.more_tags_btn.setFixedWidth(self.next_btn.sizeHint().width())

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(tr("btn_save_close"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("btn_cancel"))
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

        # main_panel's width is locked (at least DIALOG_MIN_SIZE wide, or
        # more if the content genuinely needs it) right after layout, so
        # root_layout's QHBoxLayout can never stretch or shrink it -- the
        # only thing that can change the window's total width afterwards is
        # the extended panel beside it.
        main_panel.setFixedWidth(max(main_panel.sizeHint().width(), DIALOG_MIN_SIZE[0]))

        # Hidden until "More tags" is toggled on -- widening the window only
        # then, rather than always reserving the space, keeps the common
        # case (editing the handful of basic fields) uncluttered. As a
        # sibling of main_panel (not nested inside it), showing/hiding it
        # can never affect main_panel's own geometry.
        self.extended_panel = ExtendedTagsPanel("album")
        self.extended_panel.setFixedWidth(EXTENDED_PANEL_WIDTH)
        self.extended_panel.setVisible(False)
        root_layout.addWidget(self.extended_panel)

    def _toggle_extended_panel(self, checked: bool):
        self.more_tags_btn.setText(tr("btn_fewer_tags") if checked else tr("btn_more_tags"))
        self.extended_panel.setVisible(checked)
        # Safe to just fit the window to its new sizeHint (rather than
        # guessing a resize delta): main_panel is a fixed-width sibling of
        # the panel, not something the layout can stretch, so this only ever
        # grows/shrinks the window on the right where the panel lives.
        self.adjustSize()

    @staticmethod
    def _joined_value(tracks: list[Track], attr: str) -> str:
        """What to show in a field: the plain value when every track agrees,
        otherwise each track's own value in tree order, ";"-separated. The
        differing values stay visible and editable instead of being hidden
        behind whichever track happened to load first."""
        values = [getattr(track, attr) for track in tracks]
        if len(set(values)) == 1:
            return values[0]
        return MULTI_VALUE_SEPARATOR.join(values)

    def _load_index(self, index: int):
        self.index = index
        tracks = self.albums[index]
        first = tracks[0]
        self._new_cover_bytes = None
        self._new_cover_mime = "image/jpeg"
        self._new_cover_write_loose = False

        album_name = first.album or tr("unknown_album")
        self.setWindowTitle(tr("dialog_title_edit_album", album=album_name, count=len(tracks)))
        for edit, attr in (
            (self.artist_edit, "artist"),
            (self.album_edit, "album"),
            (self.track_total_edit, "track_total"),
            (self.year_edit, "year"),
            (self.genre_edit, "genre"),
        ):
            edit.setText(self._joined_value(tracks, attr))
        self.info_label.setText(tr("info_apply_to_all", count=len(tracks)))
        self._load_cover_preview()

        # See the identical reasoning in TagEditDialog._load_index -- rebuild
        # the field list if the album's format changed, then load values
        # from the first track (album-scope fields are shared across the
        # whole album, so any track is representative) regardless of whether
        # the panel is currently visible.
        self.extended_panel.set_format(first.format)
        self.extended_panel.load_values(self.source_root / first.path)

        # Snapshot of what was loaded, taken once every widget holds its
        # value: navigation compares against this to decide whether the
        # album's files need writing at all.
        self._loaded_fields = self._current_album_fields()
        self._loaded_extended_fields = self.extended_panel.current_values()

        self.position_label.setText(f"{index + 1} / {len(self.albums)}")
        self.prev_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index < len(self.albums) - 1)

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

    def _load_cover_preview(self):
        file_path = self.source_root / self.tracks[0].path
        data = tagsmod.read_cover_art(file_path)
        if data:
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            self._show_cover_preview(pixmap)
        else:
            self._show_cover_preview(None)

    def _choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dialog_choose_cover_title"), "", tr("images_filter")
        )
        if not path:
            return
        image_path = Path(path)
        cover_bytes, mime = normalize_manual_cover(image_path.read_bytes(), image_path.suffix)
        self._new_cover_bytes = cover_bytes
        self._new_cover_mime = mime
        self._new_cover_write_loose = False
        pixmap = QPixmap()
        pixmap.loadFromData(cover_bytes)
        self._show_cover_preview(pixmap)

    def _download_cover_from_tidal(self):
        artist = self.artist_edit.text().strip()
        album = self.album_edit.text().strip()
        if not artist or not album:
            QMessageBox.information(
                self, tr("msg_tidal_missing_fields_title"), tr("msg_tidal_missing_fields_text")
            )
            return

        self.tidal_cover_btn.setEnabled(False)
        self.tidal_cover_btn.setText(tr("btn_downloading_cover_tidal"))

        # Kept alive on self so they aren't garbage-collected while the
        # background thread runs -- same pattern as MainWindow._EjectWorker.
        self._tidal_thread = QThread(self)
        self._tidal_worker = TidalCoverWorker(artist, album, TIDAL_COVER_SIZE)
        self._tidal_worker.moveToThread(self._tidal_thread)
        self._tidal_thread.started.connect(self._tidal_worker.run)
        self._tidal_worker.finished.connect(self._on_tidal_cover_finished)
        self._tidal_thread.start()

    def _on_tidal_cover_finished(self, data: bytes, error: str):
        self._tidal_thread.quit()
        self._tidal_thread.wait()
        self._tidal_thread = None
        self._tidal_worker = None
        self.tidal_cover_btn.setEnabled(True)
        self.tidal_cover_btn.setText(tr("btn_download_cover_tidal"))

        if error:
            QMessageBox.warning(self, tr("error_tidal_cover_title"), tr("error_tidal_cover_text", error=error))
            return

        cover_bytes, mime = normalize_manual_cover(data, ".jpg")
        new_pixmap = QPixmap()
        new_pixmap.loadFromData(cover_bytes)

        confirm = CoverCompareDialog(self._cover_pixmap, new_pixmap, self)
        if confirm.exec() != QDialog.Accepted:
            return

        self._new_cover_bytes = cover_bytes
        self._new_cover_mime = mime
        self._new_cover_write_loose = True
        self._show_cover_preview(new_pixmap)

    def _current_album_fields(self) -> dict:
        return {
            "artist": self.artist_edit.text(),
            "album": self.album_edit.text(),
            "track_total": self.track_total_edit.text(),
            "year": self.year_edit.text(),
            "genre": self.genre_edit.text(),
        }

    def _is_dirty(self) -> bool:
        """Whether the album-wide fields, extended tags, or the cover
        actually differ from what was loaded."""
        if self._new_cover_bytes is not None:
            return True
        if self._current_album_fields() != self._loaded_fields:
            return True
        return self.extended_panel.current_values() != self._loaded_extended_fields

    def _values_for_field(self, text: str) -> list[str]:
        """One value per edited track, from what the field now holds.

        A ";"-separated list with exactly one segment per track keeps the
        per-file mapping, so correcting a single entry of
        "artist1;artist2;artist3" only touches that file. Anything else --
        most importantly the whole list replaced by one name -- is applied
        to every track, which is how a mixed selection gets unified."""
        count = len(self.tracks)
        parts = [part.strip() for part in text.split(MULTI_VALUE_SEPARATOR)]
        if count > 1 and len(parts) == count:
            return parts
        return [text] * count

    def _save_current(self) -> bool:
        album_fields = self._current_album_fields()
        # Only fields the user actually edited get applied to every track.
        # An untouched field keeps each file's own value, which is what makes
        # editing a mixed selection safe: changing just the album name can't
        # overwrite three different artists with whichever one happened to
        # load into the box.
        changed = {key for key, value in album_fields.items() if value != self._loaded_fields.get(key)}
        resolved = {key: self._values_for_field(album_fields[key]) for key in changed}
        # Same rule for extended tags, via omission: write_tags leaves out
        # any key absent from `fields`, so an unedited extended tag is never
        # written and each file keeps whatever it already had. (These load
        # from the first track only -- reading every file to detect mixed
        # values would mean re-parsing the whole selection on open -- so
        # "don't write what wasn't edited" is what protects them.)
        loaded_extended = self._loaded_extended_fields
        extended_fields = {
            key: value
            for key, value in self.extended_panel.current_values().items()
            if value != loaded_extended.get(key)
        }

        errors: list[str] = []
        updated_tracks: list[Track] = []
        for position, track in enumerate(self.tracks):
            file_path = self.source_root / track.path

            def value_of(key: str, own: str, position=position) -> str:
                return resolved[key][position] if key in resolved else own

            fields = {
                "artist": value_of("artist", track.artist),
                "album": value_of("album", track.album),
                "title": track.title,
                "track_number": track.track_number,
                "track_total": value_of("track_total", track.track_total),
                "disc_number": track.disc_number,
                "year": value_of("year", track.year),
                "genre": value_of("genre", track.genre),
                **extended_fields,
            }
            try:
                tagsmod.write_tags(file_path, fields)
                if self._new_cover_bytes is not None:
                    tagsmod.write_cover_art(file_path, self._new_cover_bytes, self._new_cover_mime)
            except Exception as exc:
                errors.append(f"{track.filename}: {exc}")
                updated_tracks.append(track)
                continue

            fields["filename"] = file_path.name
            fields["path"] = track.path
            updated = self.on_saved(track, fields)
            updated_tracks.append(updated if updated is not None else track)

        if self._new_cover_bytes is not None and self._new_cover_write_loose:
            # Once per directory rather than once per track: a multi-disc
            # album spread over CD1/, CD2/, ... needs a cover.jpg in each,
            # but writing it 20 times into the same folder is pointless.
            album_dirs = {(self.source_root / track.path).parent for track in self.tracks}
            for album_dir in sorted(album_dirs):
                try:
                    album_covers.write_loose_cover(album_dir, self._new_cover_bytes)
                except OSError as exc:
                    errors.append(f"{album_dir}: {exc}")

        if errors:
            QMessageBox.critical(self, tr("error_save_title"), "\n".join(errors))
            return False

        self.albums[self.index] = updated_tracks
        return True

    def _go_previous(self):
        if self.index == 0:
            return
        # Paging between albums must not rewrite them. This dialog writes the
        # form's values into *every* track of the album, so saving on each
        # step would rewrite (and rehash) whole albums the user only looked
        # at. Save still applies them on demand -- that is what it is for.
        if self._is_dirty() and not self._save_current():
            return
        self._load_index(self.index - 1)

    def _go_next(self):
        if self.index == len(self.albums) - 1:
            return
        if self._is_dirty() and not self._save_current():
            return
        self._load_index(self.index + 1)

    def _save_and_close(self):
        if not self._save_current():
            return
        self.accept()
