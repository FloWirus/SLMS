from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from ..i18n import tr


def _format_duration(seconds: float | None) -> str:
    if not seconds:
        return "-"
    total = int(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_size(num_bytes: int | None) -> str:
    if not num_bytes:
        return "-"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{num_bytes} B"


class MediaInfoDialog(QDialog):
    def __init__(self, file_path: Path, info: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialog_title_media_info", filename=file_path.name))

        layout = QVBoxLayout(self)
        form = QFormLayout()

        form.addRow(tr("field_mi_path"), self._value_label(str(file_path)))
        form.addRow(tr("field_mi_codec"), self._value_label(info.get("codec") or "-"))
        sample_rate = info.get("sample_rate")
        form.addRow(tr("field_mi_sample_rate"), self._value_label(f"{sample_rate} Hz" if sample_rate else "-"))
        bit_depth = info.get("bit_depth")
        form.addRow(tr("field_mi_bit_depth"), self._value_label(f"{bit_depth}-bit" if bit_depth else "-"))
        form.addRow(tr("field_mi_channels"), self._value_label(str(info.get("channels") or "-")))
        form.addRow(tr("field_mi_duration"), self._value_label(_format_duration(info.get("duration"))))
        bitrate = info.get("bitrate")
        form.addRow(tr("field_mi_bitrate"), self._value_label(f"{bitrate // 1000} kbps" if bitrate else "-"))
        form.addRow(tr("field_mi_file_size"), self._value_label(_format_size(info.get("file_size"))))

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _value_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label
