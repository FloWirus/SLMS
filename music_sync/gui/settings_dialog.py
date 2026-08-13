from PySide6.QtWidgets import (
    QCheckBox,
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

from .. import converter
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

        self.libsoxr_checkbox = QCheckBox(tr("chk_use_libsoxr"))
        self.libsoxr_checkbox.setChecked(self._settings.use_libsoxr)
        if converter.libsoxr_available():
            self.libsoxr_checkbox.setToolTip(tr("chk_use_libsoxr_tooltip"))
        else:
            self.libsoxr_checkbox.setChecked(False)
            self.libsoxr_checkbox.setEnabled(False)
            self.libsoxr_checkbox.setToolTip(tr("chk_use_libsoxr_unavailable_tooltip"))
        form.addRow("", self.libsoxr_checkbox)

        self.track_no_fix_checkbox = QCheckBox(tr("chk_track_no_fix"))
        self.track_no_fix_checkbox.setChecked(self._settings.track_no_fix)
        self.track_no_fix_checkbox.setToolTip(tr("chk_track_no_fix_tooltip"))
        form.addRow("", self.track_no_fix_checkbox)

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
            use_libsoxr=self.libsoxr_checkbox.isChecked(),
            track_no_fix=self.track_no_fix_checkbox.isChecked(),
            profiles=self._settings.profiles,
            last_profile_name=self._settings.last_profile_name,
            table_header_state=self._settings.table_header_state,
            device_table_header_state=self._settings.device_table_header_state,
            tree_header_state=self._settings.tree_header_state,
            device_tree_header_state=self._settings.device_tree_header_state,
        )


def _language_display_name(code: str) -> str:
    names = {"en": "English", "pl": "Polski"}
    return names.get(code, code)
