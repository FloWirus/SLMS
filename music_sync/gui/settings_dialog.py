from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import available_languages, tr
from ..settings import Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dialog_title_settings"))
        self.resize(560, 320)
        self._settings = settings
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.dir_template_edit = QLineEdit(self._settings.dir_template)
        self.filename_template_edit = QLineEdit(self._settings.filename_template)
        form.addRow(tr("label_dir_template"), self.dir_template_edit)
        form.addRow(tr("label_filename_template"), self.filename_template_edit)

        self.cover_size_edit = QLineEdit(str(self._settings.cover_max_size))
        self.cover_size_edit.setValidator(QIntValidator(1, 10000, self))
        form.addRow(tr("label_cover_size"), self.cover_size_edit)

        self.cover_dpi_edit = QLineEdit(str(self._settings.cover_dpi))
        self.cover_dpi_edit.setValidator(QIntValidator(1, 2400, self))
        form.addRow(tr("label_cover_dpi"), self.cover_dpi_edit)

        self.language_combo = QComboBox()
        for code in available_languages():
            self.language_combo.addItem(_language_display_name(code), code)
        current_index = self.language_combo.findData(self._settings.language)
        if current_index >= 0:
            self.language_combo.setCurrentIndex(current_index)
        form.addRow(tr("label_language"), self.language_combo)

        self.theme_combo = QComboBox()
        for code, label_key in (("light", "theme_light"), ("dark", "theme_dark"), ("auto", "theme_auto")):
            self.theme_combo.addItem(tr(label_key), code)
        theme_index = self.theme_combo.findData(self._settings.theme)
        if theme_index >= 0:
            self.theme_combo.setCurrentIndex(theme_index)
        form.addRow(tr("label_theme"), self.theme_combo)

        layout.addLayout(form)

        help_row = QHBoxLayout()
        help_row.addStretch()
        help_btn = QPushButton(tr("btn_tag_help"))
        help_btn.clicked.connect(self._show_tag_help)
        help_row.addWidget(help_btn)
        layout.addLayout(help_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(tr("btn_save"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("btn_cancel"))
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show_tag_help(self):
        QMessageBox.information(self, tr("tag_help_title"), tr("tag_help_text"))

    def _on_save(self):
        if self.language_combo.currentData() != self._settings.language:
            QMessageBox.information(self, tr("dialog_title_settings"), tr("msg_restart_required"))
        self.accept()

    def updated_settings(self) -> Settings:
        return Settings(
            dir_template=self.dir_template_edit.text().strip(),
            filename_template=self.filename_template_edit.text().strip(),
            last_source_root=self._settings.last_source_root,
            language=self.language_combo.currentData(),
            theme=self.theme_combo.currentData(),
            cover_max_size=int(self.cover_size_edit.text() or self._settings.cover_max_size),
            cover_dpi=int(self.cover_dpi_edit.text() or self._settings.cover_dpi),
        )


def _language_display_name(code: str) -> str:
    names = {"en": "English", "pl": "Polski"}
    return names.get(code, code)
