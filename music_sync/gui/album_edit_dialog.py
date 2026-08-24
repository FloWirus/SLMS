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
from .tag_dialog_base import CoverTagDialogBase

# Separator between per-file values when a field isn't the same across the
# edited tracks: "artist1;artist2;artist3". Editing one segment retargets
# that one file; replacing the whole list with a single value applies it to
# all of them (see _values_for_field).
MULTI_VALUE_SEPARATOR = ";"
DIALOG_MIN_SIZE = (650, 480)

OnSavedCallback = Callable[[Track, dict], Track | None]


class AlbumEditDialog(CoverTagDialogBase):
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

        top_row.addLayout(self._build_cover_box())

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
        action_row.addWidget(self._build_more_tags_button())
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

        # Wide enough for both of its captions, and never narrower than the
        # Next album button beside it, so the two read as a pair.
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

        self._attach_extended_panel(root_layout, "album")

    @staticmethod
    def _joined_value(values: list[str]) -> str:
        """What to show in a field: the plain value when every track agrees,
        otherwise each track's own value in tree order, ";"-separated. The
        differing values stay visible and editable instead of being hidden
        behind whichever track happened to load first."""
        if len(set(values)) == 1:
            return values[0]
        return MULTI_VALUE_SEPARATOR.join(values)

    @staticmethod
    def _is_splittable(values: list[str]) -> bool:
        """Whether the joined text above can be mapped back onto one value per
        track.

        Only mixed fields are joined in the first place, and only if none of
        the values contains the separator itself: a genre of "Rock;Metal" (or
        an artist with a semicolon in the name) would otherwise produce more
        segments than there are tracks -- and, worse, with the right number of
        tracks it would silently deal each fragment out to a different file.
        A field that fails this test is still shown joined, but editing it
        applies the whole text to every track, which is the safe reading of
        "the user replaced this field"."""
        return len(set(values)) > 1 and not any(MULTI_VALUE_SEPARATOR in value for value in values)

    def _load_index(self, index: int):
        self.index = index
        tracks = self.albums[index]
        first = tracks[0]
        # Drops an unsaved cover pick and any Tidal lookup started for the
        # previous album, which must not land on this one.
        self._reset_pending_cover()

        album_name = first.album or tr("unknown_album")
        self.setWindowTitle(tr("dialog_title_edit_album", album=album_name, count=len(tracks)))
        # Which fields the ";"-joined text can be split back apart on -- see
        # _is_splittable. Recomputed per album, since it depends on the values.
        self._splittable_fields: set[str] = set()
        for edit, attr in (
            (self.artist_edit, "artist"),
            (self.album_edit, "album"),
            (self.track_total_edit, "track_total"),
            (self.year_edit, "year"),
            (self.genre_edit, "genre"),
        ):
            values = [getattr(track, attr) for track in tracks]
            edit.setText(self._joined_value(values))
            if self._is_splittable(values):
                self._splittable_fields.add(attr)
        self.info_label.setText(tr("info_apply_to_all", count=len(tracks)))
        self._load_cover_preview(self.source_root / first.path)

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

    def _values_for_field(self, key: str, text: str) -> list[str]:
        """One value per edited track, from what the field now holds.

        A ";"-separated list with exactly one segment per track keeps the
        per-file mapping, so correcting a single entry of
        "artist1;artist2;artist3" only touches that file. Anything else --
        most importantly the whole list replaced by one name -- is applied
        to every track, which is how a mixed selection gets unified.

        Splitting only ever happens for fields loaded as a splittable list
        (see _is_splittable); for every other field the text is one value that
        goes to all of them, semicolons and all."""
        count = len(self.tracks)
        if key not in self._splittable_fields:
            return [text] * count
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
        resolved = {key: self._values_for_field(key, album_fields[key]) for key in changed}
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

        errors.extend(
            self._write_loose_covers((self.source_root / track.path).parent for track in self.tracks)
        )

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
        # Same dirtiness check the Prev/Next navigation does. It matters even
        # more here than in TagEditDialog: this dialog writes the form's values
        # into *every* track of the album, so an unconditional save would
        # rewrite (and rehash) a whole album that nobody edited, making all of
        # it look missing on the device again.
        if self._is_dirty() and not self._save_current():
            return
        self.accept()
