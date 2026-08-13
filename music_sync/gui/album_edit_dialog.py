from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
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
)

from .. import tags as tagsmod
from ..db import Track
from ..i18n import tr
from ..settings import Settings

COVER_SIZE = 150
DIALOG_MIN_SIZE = (650, 480)

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

        self.resize(*DIALOG_MIN_SIZE)
        self._build_ui()
        self._load_index(self.index)

    @property
    def tracks(self) -> list[Track]:
        return self.albums[self.index]

    def _build_ui(self):
        layout = QVBoxLayout(self)

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
        layout.addLayout(top_row)

        self.info_label = QLabel()
        layout.addWidget(self.info_label)

        cover_btn = QPushButton(tr("btn_change_cover_album"))
        cover_btn.clicked.connect(self._choose_cover)
        layout.addWidget(cover_btn)

        nav_row = QHBoxLayout()
        self.prev_btn = QPushButton(tr("btn_prev_album"))
        self.prev_btn.clicked.connect(self._go_previous)
        nav_row.addWidget(self.prev_btn)

        self.position_label = QLabel()
        self.position_label.setAlignment(Qt.AlignCenter)
        nav_row.addWidget(self.position_label)

        self.next_btn = QPushButton(tr("btn_next_album"))
        self.next_btn.clicked.connect(self._go_next)
        nav_row.addWidget(self.next_btn)
        layout.addLayout(nav_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(tr("btn_save_close"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("btn_cancel"))
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_index(self, index: int):
        self.index = index
        tracks = self.albums[index]
        first = tracks[0]
        self._new_cover_bytes = None
        self._new_cover_mime = "image/jpeg"

        album_name = first.album or tr("unknown_album")
        self.setWindowTitle(tr("dialog_title_edit_album", album=album_name, count=len(tracks)))
        self.artist_edit.setText(first.artist)
        self.album_edit.setText(first.album)
        self.track_total_edit.setText(first.track_total)
        self.year_edit.setText(first.year)
        self.genre_edit.setText(first.genre)
        self.info_label.setText(tr("info_apply_to_all", count=len(tracks)))
        self._load_cover_preview()

        self.position_label.setText(f"{index + 1} / {len(self.albums)}")
        self.prev_btn.setEnabled(index > 0)
        self.next_btn.setEnabled(index < len(self.albums) - 1)

    def _load_cover_preview(self):
        file_path = self.source_root / self.tracks[0].path
        data = tagsmod.read_cover_art(file_path)
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
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        cover_bytes = image_path.read_bytes()
        self._new_cover_bytes = cover_bytes
        self._new_cover_mime = mime
        pixmap = QPixmap()
        pixmap.loadFromData(cover_bytes)
        self.cover_label.setPixmap(
            pixmap.scaled(COVER_SIZE, COVER_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.cover_size_label.setText(f"{pixmap.width()}x{pixmap.height()}")

    def _save_current(self) -> bool:
        artist = self.artist_edit.text()
        album = self.album_edit.text()
        track_total = self.track_total_edit.text()
        year = self.year_edit.text()
        genre = self.genre_edit.text()

        errors: list[str] = []
        updated_tracks: list[Track] = []
        for track in self.tracks:
            file_path = self.source_root / track.path
            fields = {
                "artist": artist,
                "album": album,
                "title": track.title,
                "track_number": track.track_number,
                "track_total": track_total,
                "disc_number": track.disc_number,
                "year": year,
                "genre": genre,
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

        if errors:
            QMessageBox.critical(self, tr("error_save_title"), "\n".join(errors))
            return False

        self.albums[self.index] = updated_tracks
        return True

    def _go_previous(self):
        if self.index == 0:
            return
        if not self._save_current():
            return
        self._load_index(self.index - 1)

    def _go_next(self):
        if self.index == len(self.albums) - 1:
            return
        if not self._save_current():
            return
        self._load_index(self.index + 1)

    def _save_and_close(self):
        if not self._save_current():
            return
        self.accept()
