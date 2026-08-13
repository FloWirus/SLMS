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
from .cover_utils import resize_cover_bytes

COVER_SIZE = 150
DIALOG_MIN_SIZE = (650, 520)

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

        self.resize(*DIALOG_MIN_SIZE)
        self._build_ui()
        self._load_index(self.index)

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
        layout.addLayout(top_row)

        cover_btn = QPushButton(tr("btn_change_cover"))
        cover_btn.clicked.connect(self._choose_cover)
        layout.addWidget(cover_btn)

        nav_row = QHBoxLayout()
        self.prev_btn = QPushButton(tr("btn_prev"))
        self.prev_btn.clicked.connect(self._go_previous)
        nav_row.addWidget(self.prev_btn)

        self.position_label = QLabel()
        self.position_label.setAlignment(Qt.AlignCenter)
        nav_row.addWidget(self.position_label)

        self.next_btn = QPushButton(tr("btn_next"))
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
        track = self.tracks[index]
        self.file_path = self.source_root / track.path
        self._new_cover_bytes = None
        self._new_cover_mime = "image/jpeg"

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
        mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        resized_bytes = resize_cover_bytes(
            image_path.read_bytes(), mime, self.settings.cover_max_size, self.settings.cover_dpi
        )
        self._new_cover_bytes = resized_bytes
        self._new_cover_mime = mime
        pixmap = QPixmap()
        pixmap.loadFromData(resized_bytes)
        self.cover_label.setPixmap(
            pixmap.scaled(COVER_SIZE, COVER_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.cover_size_label.setText(f"{pixmap.width()}x{pixmap.height()}")

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

    def _save_current(self) -> bool:
        fields = self._current_fields()
        try:
            tagsmod.write_tags(self.file_path, fields)
            if self._new_cover_bytes is not None:
                tagsmod.write_cover_art(self.file_path, self._new_cover_bytes, self._new_cover_mime)

            new_stem = self.filename_edit.text().strip()
            if new_stem and new_stem != self.file_path.stem:
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
        if not self._save_current():
            return
        self._load_index(self.index - 1)

    def _go_next(self):
        if self.index == len(self.tracks) - 1:
            return
        if not self._save_current():
            return
        self._load_index(self.index + 1)

    def _save_and_close(self):
        if not self._save_current():
            return
        self.accept()
