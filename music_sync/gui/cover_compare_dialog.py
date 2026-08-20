from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout

from ..i18n import tr

PREVIEW_SIZE = 200


def _cover_column(title: str, pixmap: QPixmap | None) -> QVBoxLayout:
    column = QVBoxLayout()
    title_label = QLabel(title)
    title_label.setAlignment(Qt.AlignCenter)
    column.addWidget(title_label)

    image_label = QLabel()
    image_label.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
    image_label.setStyleSheet("border: 1px solid gray;")
    image_label.setAlignment(Qt.AlignCenter)

    size_label = QLabel()
    size_label.setAlignment(Qt.AlignCenter)

    if pixmap is not None and not pixmap.isNull():
        image_label.setPixmap(
            pixmap.scaled(PREVIEW_SIZE, PREVIEW_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        size_label.setText(f"{pixmap.width()}x{pixmap.height()}")
    else:
        image_label.setText(tr("cover_none"))

    column.addWidget(image_label)
    column.addWidget(size_label)
    return column


class CoverCompareDialog(QDialog):
    """Shown after a successful Tidal cover lookup, side-by-side with the
    track/album's current cover, so the user confirms the match before it
    replaces anything."""

    def __init__(self, old_pixmap: QPixmap | None, new_pixmap: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialog_title_confirm_cover"))

        root = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addLayout(_cover_column(tr("cover_current"), old_pixmap))
        row.addLayout(_cover_column(tr("cover_found_tidal"), new_pixmap))
        root.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
        buttons.button(QDialogButtonBox.Yes).setText(tr("btn_use_cover"))
        buttons.button(QDialogButtonBox.No).setText(tr("btn_cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
