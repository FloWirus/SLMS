from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import tags as tagsmod
from ..db import Track
from ..i18n import tr
from ..settings import Settings
from ..templating import sanitize_component
from .tag_dialog_base import CoverTagDialogBase

DIALOG_MIN_SIZE = (650, 520)

OnSavedCallback = Callable[[Track, dict], Track | None]


class TagEditDialog(CoverTagDialogBase):
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

        top_row.addLayout(self._build_cover_box())

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
        action_row.addWidget(self._build_more_tags_button())
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

        # Wide enough for both of its captions, and never narrower than the
        # Next button beside it, so the two read as a pair.
        self._lock_more_tags_button_width(self.next_btn)

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

        self._attach_extended_panel(root_layout, "track")

    def _load_index(self, index: int):
        self.index = index
        track = self.tracks[index]
        self.file_path = self.source_root / track.path
        # Drops an unsaved cover pick and any Tidal lookup started for the
        # previous track, which must not land on this one.
        self._reset_pending_cover()

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
        self._load_cover_preview(self.file_path)

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
                loose_errors = self._write_loose_covers([self.file_path.parent])
                if loose_errors:
                    raise OSError("; ".join(loose_errors))

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
        # Same dirtiness check the Prev/Next navigation does: pressing Save
        # on a track nobody touched must not rewrite the file. Rewriting it
        # changes its bytes, hence its hash -- which is what "already on the
        # device" is matched on -- so an untouched track would come back as
        # missing and be copied all over again on the next sync.
        if self._is_dirty() and not self._save_current():
            return
        self.accept()
