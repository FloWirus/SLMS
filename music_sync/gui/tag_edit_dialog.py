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
from ..templating import sanitize_component
from .cover_compare_dialog import CoverCompareDialog
from .extended_tags_panel import PANEL_WIDTH as EXTENDED_PANEL_WIDTH
from .extended_tags_panel import ExtendedTagsPanel
from .tidal_cover_worker import TidalCoverWorker

COVER_SIZE = 150
DIALOG_MIN_SIZE = (650, 520)
TIDAL_COVER_SIZE = 1280

OnSavedCallback = Callable[[Track, dict], Track | None]


class TagEditDialog(QDialog):
    def __init__(
        self,
        source_root: Path,
        tracks: list[Track],
        start_index: int,
        on_saved: OnSavedCallback,
        settings: Settings,
        parent=None,
    ):
        super().__init__(parent)
        self.source_root = Path(source_root)
        self.tracks = list(tracks)
        self.index = start_index
        self.on_saved = on_saved
        self.settings = settings
        self._new_cover_bytes: bytes | None = None
        self._new_cover_mime = "image/jpeg"
        # Only covers fetched from Tidal are also written out as cover.jpg;
        # a manually picked file is left alone, since the user already has
        # that image on disk wherever they chose it from.
        self._new_cover_write_loose = False
        self._tidal_thread: QThread | None = None
        self._tidal_worker: TidalCoverWorker | None = None

        self._build_ui()
        self._load_index(self.index)
        self.adjustSize()

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
        self.title_edit = QLineEdit()
        self.track_edit = QLineEdit()
        self.track_total_edit = QLineEdit()
        self.disc_edit = QLineEdit()
        self.year_edit = QLineEdit()
        self.genre_edit = QLineEdit()
        self.filename_edit = QLineEdit()

        form_layout.addRow(tr("field_artist"), self.artist_edit)
        form_layout.addRow(tr("field_album"), self.album_edit)
        form_layout.addRow(tr("field_title"), self.title_edit)
        form_layout.addRow(tr("field_track"), self.track_edit)
        form_layout.addRow(tr("field_track_total"), self.track_total_edit)
        form_layout.addRow(tr("field_disc"), self.disc_edit)
        form_layout.addRow(tr("field_year"), self.year_edit)
        form_layout.addRow(tr("field_genre"), self.genre_edit)
        form_layout.addRow(tr("field_filename"), self.filename_edit)

        top_row.addLayout(form_layout)
        left_column.addLayout(top_row)

        action_row = QHBoxLayout()
        self.more_tags_btn = QPushButton(tr("btn_more_tags"))
        self.more_tags_btn.setCheckable(True)
        self.more_tags_btn.toggled.connect(self._toggle_extended_panel)
        action_row.addWidget(self.more_tags_btn)
        left_column.addLayout(action_row)

        content_row.addLayout(left_column)
        main_layout.addLayout(content_row)

        nav_row = QHBoxLayout()
        self.prev_btn = QPushButton(tr("btn_prev"))
        self.prev_btn.clicked.connect(self._go_previous)
        nav_row.addWidget(self.prev_btn)

        self.position_label = QLabel()
        self.position_label.setAlignment(Qt.AlignCenter)
        # Stretch factor on the label (not the buttons) so Prev/Next keep
        # their natural size instead of growing to fill leftover width.
        nav_row.addWidget(self.position_label, 1)

        self.next_btn = QPushButton(tr("btn_next"))
        self.next_btn.clicked.connect(self._go_next)
        nav_row.addWidget(self.next_btn)
        main_layout.addLayout(nav_row)

        # Matches the Next button's width regardless of language/text length,
        # per the request that this button look like a peer of the nav row.
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
        self.extended_panel = ExtendedTagsPanel("track")
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

    def _load_index(self, index: int):
        self.index = index
        track = self.tracks[index]
        self.file_path = self.source_root / track.path
        self._new_cover_bytes = None
        self._new_cover_mime = "image/jpeg"
        self._new_cover_write_loose = False

        self.setWindowTitle(tr("dialog_title_edit_tags", filename=track.filename))
        self.artist_edit.setText(track.artist)
        self.album_edit.setText(track.album)
        self.title_edit.setText(track.title)
        self.track_edit.setText(track.track_number)
        self.track_total_edit.setText(track.track_total)
        self.disc_edit.setText(track.disc_number)
        self.year_edit.setText(track.year)
        self.genre_edit.setText(track.genre)
        self.filename_edit.setText(self.file_path.stem)
        self._load_cover_preview()

        # Rebuilds the field list if this track's format differs from the
        # previous one (e.g. an mp3 next to a flac in the same sequence),
        # then loads this track's current extended-tag values regardless of
        # whether the panel is currently visible, so toggling it on later
        # always shows correct data.
        self.extended_panel.set_format(track.format)
        self.extended_panel.load_values(self.file_path)

        # Snapshot of what was loaded, taken once every widget holds its
        # value: navigation compares against this to decide whether the file
        # needs writing at all.
        self._loaded_fields = self._current_fields()
        self._loaded_stem = self.file_path.stem
        self._loaded_extended_fields = self.extended_panel.current_values()

        self.position_label.setText(f"{index + 1} / {len(self.tracks)}")
        self.prev_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index < len(self.tracks) - 1)

    def _load_cover_preview(self):
        data = tagsmod.read_cover_art(self.file_path)
        if data:
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            self.cover_label.setPixmap(
                pixmap.scaled(COVER_SIZE, COVER_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self.cover_size_label.setText(f"{pixmap.width()}x{pixmap.height()}")
        else:
            self.cover_label.clear()
            self.cover_label.setText(tr("cover_none"))
            self.cover_size_label.clear()

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
        self.cover_label.setPixmap(
            pixmap.scaled(COVER_SIZE, COVER_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.cover_size_label.setText(f"{pixmap.width()}x{pixmap.height()}")

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

        old_pixmap = self.cover_label.pixmap()
        confirm = CoverCompareDialog(old_pixmap, new_pixmap, self)
        if confirm.exec() != QDialog.Accepted:
            return

        self._new_cover_bytes = cover_bytes
        self._new_cover_mime = mime
        self._new_cover_write_loose = True
        self.cover_label.setPixmap(
            new_pixmap.scaled(COVER_SIZE, COVER_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.cover_size_label.setText(f"{new_pixmap.width()}x{new_pixmap.height()}")

    def _current_fields(self) -> dict:
        return {
            "artist": self.artist_edit.text(),
            "album": self.album_edit.text(),
            "title": self.title_edit.text(),
            "track_number": self.track_edit.text(),
            "track_total": self.track_total_edit.text(),
            "disc_number": self.disc_edit.text(),
            "year": self.year_edit.text(),
            "genre": self.genre_edit.text(),
        }

    def _new_stem(self) -> str | None:
        """The filename field's value, sanitized the same way template
        rendering sanitizes path components -- strips characters that are
        invalid in a filename (including "/" and "\\", so a value like
        "../evil" can't escape the album directory) and collapses a result
        that is otherwise empty or only dots/spaces down to "_".

        A blank field is left as None rather than sanitized to "_": clearing
        the field means "leave the filename alone", not "rename to _"."""
        raw = self.filename_edit.text().strip()
        return sanitize_component(raw) if raw else None

    def _is_dirty(self) -> bool:
        """Whether anything the user can edit here actually differs from what
        was loaded -- tags, extended tags, filename or cover."""
        if self._new_cover_bytes is not None:
            return True
        if self._current_fields() != self._loaded_fields:
            return True
        if self.extended_panel.current_values() != self._loaded_extended_fields:
            return True
        new_stem = self._new_stem()
        return new_stem is not None and new_stem != self._loaded_stem

    def _save_current(self) -> bool:
        fields = self._current_fields()
        fields.update(self.extended_panel.current_values())
        try:
            tagsmod.write_tags(self.file_path, fields)
            if self._new_cover_bytes is not None:
                tagsmod.write_cover_art(self.file_path, self._new_cover_bytes, self._new_cover_mime)
                if self._new_cover_write_loose:
                    album_covers.write_loose_cover(self.file_path.parent, self._new_cover_bytes)

            new_stem = self._new_stem()
            if new_stem is not None and new_stem != self.file_path.stem:
                new_path = self.file_path.with_name(new_stem + self.file_path.suffix)
                if new_path.exists():
                    raise FileExistsError(tr("error_file_exists", name=new_path.name))
                self.file_path.rename(new_path)
                self.file_path = new_path
        except Exception as exc:
            QMessageBox.critical(self, tr("error_save_title"), str(exc))
            return False

        fields["filename"] = self.file_path.name
        fields["path"] = str(self.file_path.relative_to(self.source_root))

        old_track = self.tracks[self.index]
        updated_track = self.on_saved(old_track, fields)
        if updated_track is not None:
            self.tracks[self.index] = updated_track
        return True

    def _go_previous(self):
        if self.index == 0:
            return
        # Merely paging through tracks must not rewrite them: an untouched
        # file keeps its bytes, its mtime and therefore its hash -- which is
        # what "already on the device" is matched on.
        if self._is_dirty() and not self._save_current():
            return
        self._load_index(self.index - 1)

    def _go_next(self):
        if self.index == len(self.tracks) - 1:
            return
        if self._is_dirty() and not self._save_current():
            return
        self._load_index(self.index + 1)

    def _save_and_close(self):
        if not self._save_current():
            return
        self.accept()
