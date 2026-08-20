import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, Qt, QThread, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QTableView,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QSortFilterProxyModel

from .. import devices as devicesmod
from .. import tags as tagsmod
from ..converter import (
    CONVERSION_TARGET_ORDER,
    CONVERSION_TARGETS,
    ConversionSettings,
    CoverResizeSettings,
    ffmpeg_available,
)
from ..db import MusicDatabase, Track, device_db_path, library_db_path
from ..i18n import set_language, tr
from ..scanner import hash_file, scan_directory
from ..settings import Settings
from ..sync import (
    ConflictResolution,
    SyncResult,
    delete_many_from_device,
    remove_empty_parent_dirs,
    sync_from_device,
    sync_to_device,
)
from .album_edit_dialog import AlbumEditDialog
from .icons import full_presence_icon, partial_presence_icon
from .media_info_dialog import MediaInfoDialog
from .models import COLUMN_KEYS, TrackTableModel, format_size, polish_sort_key
from .settings_dialog import SettingsDialog
from .tag_edit_dialog import TagEditDialog
from .theme import apply_theme

logger = logging.getLogger(__name__)


class _EjectWorker(QObject):
    """Runs the (potentially long, since unmount forces a cache flush to
    the device) eject in a background thread so the GUI stays responsive."""

    finished = Signal(bool, str)

    def __init__(self, device):
        super().__init__()
        self.device = device

    def run(self):
        success, error = devicesmod.eject_device(self.device)
        self.finished.emit(success, error)


class _TransferProgressDialog(QDialog):
    """Sync progress dialog tracking overall progress across all tracks."""

    def __init__(self, initial_text: str, cancel_text: str | None, minimum: int, maximum: int, parent=None):
        super().__init__(parent)
        self.setModal(True)
        layout = QVBoxLayout(self)
        self._label = QLabel(initial_text)
        layout.addWidget(self._label)
        self._bar = QProgressBar()
        self._bar.setMinimum(minimum)
        self._bar.setMaximum(maximum)
        layout.addWidget(self._bar)
        self._cancelled = False
        if cancel_text:
            button_row = QHBoxLayout()
            button_row.addStretch()
            cancel_btn = QPushButton(cancel_text)
            cancel_btn.clicked.connect(self._cancel)
            button_row.addWidget(cancel_btn)
            layout.addLayout(button_row)

    def _cancel(self):
        self._cancelled = True

    def wasCanceled(self) -> bool:
        return self._cancelled

    def setLabelText(self, text: str) -> None:
        self._label.setText(text)

    def setMaximum(self, value: int) -> None:
        self._bar.setMaximum(value)

    def setValue(self, value: int) -> None:
        self._bar.setValue(value)


APP_NAME = "SLMS"


class MainWindow(QMainWindow):
    def __init__(self, project_root: Path):
        super().__init__()
        self.project_root = Path(project_root)
        self.settings = Settings.load(self.project_root)
        set_language(self.settings.language)

        self.source_root: Path | None = None
        self.library_db: MusicDatabase | None = None
        self.selected_device: devicesmod.StorageDevice | None = None
        self.device_db: MusicDatabase | None = None
        self.device_hashes: set[str] = set()
        self.source_checked_hashes: set[str] = set()
        self.device_checked_hashes: set[str] = set()
        self._updating_checks = False

        self.setWindowTitle(APP_NAME)
        self.resize(1000, 650)

        self._build_ui()
        self._restore_header_states()

        if self.settings.last_profile_name:
            idx = self.profile_combo.findData(self.settings.last_profile_name)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)

        self._refresh_devices()
        self._restore_last_source()

    def closeEvent(self, event):
        self._save_header_states()
        self.settings.save(self.project_root)
        super().closeEvent(event)

    def _restore_header_states(self):
        self._restore_header_state(self.table_view.horizontalHeader(), self.settings.table_header_state)
        self._restore_header_state(self.device_table_view.horizontalHeader(), self.settings.device_table_header_state)
        self._restore_header_state(self.tree_widget.header(), self.settings.tree_header_state)
        self._restore_header_state(self.device_tree_widget.header(), self.settings.device_tree_header_state)

    def _save_header_states(self):
        self.settings.table_header_state = self._header_state_to_str(self.table_view.horizontalHeader())
        self.settings.device_table_header_state = self._header_state_to_str(self.device_table_view.horizontalHeader())
        self.settings.tree_header_state = self._header_state_to_str(self.tree_widget.header())
        self.settings.device_tree_header_state = self._header_state_to_str(self.device_tree_widget.header())

    @staticmethod
    def _header_state_to_str(header) -> str:
        return bytes(header.saveState()).hex()

    @staticmethod
    def _restore_header_state(header, state: str) -> None:
        if not state:
            return
        header.restoreState(QByteArray.fromHex(state.encode()))

    # ---------- UI construction ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addLayout(self._build_toolbar())

        splitter = QSplitter(Qt.Horizontal)

        self.table_view = self._build_table_view(
            tr("col_on_device"), self.source_checked_hashes, self._on_source_table_check_changed
        )
        self.table_model = self.table_view.model().sourceModel()
        self.proxy_model = self.table_view.model()
        self.tree_widget = self._build_tree_widget(self._tree_context_menu, self._edit_tree_item)
        source_pane, self.source_check_all_btn = self._build_pane(
            tr("pane_source_title"), self.table_view, self.tree_widget, self._toggle_check_all_source
        )
        self.source_stats_label = QLabel()
        source_pane.layout().addWidget(self.source_stats_label)
        splitter.addWidget(source_pane)

        self.device_table_view = self._build_table_view(
            tr("col_on_pc"), self.device_checked_hashes, self._on_device_table_check_changed
        )
        self.device_table_view.customContextMenuRequested.disconnect()
        self.device_table_view.doubleClicked.disconnect()
        self.device_table_view.customContextMenuRequested.connect(self._device_table_context_menu)
        self.device_table_view.doubleClicked.connect(self._edit_selected_device_table_track)
        self.device_table_model = self.device_table_view.model().sourceModel()
        self.device_proxy_model = self.device_table_view.model()
        self.device_tree_widget = self._build_tree_widget(self._device_tree_context_menu, self._edit_device_tree_item)
        device_pane, self.device_check_all_btn = self._build_pane(
            tr("pane_device_title"),
            self.device_table_view,
            self.device_tree_widget,
            self._toggle_check_all_device,
        )
        self.device_stats_label = QLabel()
        self.device_stats_separator = QLabel("|")
        self.device_space_bar = QProgressBar()
        self.device_space_bar.setMaximum(100)
        self.device_space_bar.setTextVisible(False)
        self.device_space_bar.setFixedHeight(8)

        self.device_space_label = QLabel()

        self._device_status_row = QHBoxLayout()
        self._device_status_row.addWidget(self.device_stats_label)
        self._device_status_row.addWidget(self.device_stats_separator)
        # Stretch factor 1 on the bar plus an equal-weight trailing stretch
        # splits the row's leftover width 50/50 between the bar and empty
        # space, instead of the bar expanding to fill the whole row. The
        # free/total text sits right after the bar, outside that split, so
        # it always hugs the bar instead of drifting off with the stretch.
        self._device_status_row.addWidget(self.device_space_bar, 1)
        self._device_status_row.addWidget(self.device_space_label)
        self._device_status_row.addStretch(1)
        device_pane.layout().addLayout(self._device_status_row)
        self._clear_device_status()
        splitter.addWidget(device_pane)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        self.status_label = QLabel()
        layout.addWidget(self.status_label, 0)

        conversion_bar = self._build_conversion_bar()
        cover_resize_bar = self._build_cover_resize_bar()
        profile_bar = self._build_profile_bar()
        force_bar = self._build_force_bar()

        layout.addLayout(profile_bar)
        layout.addLayout(conversion_bar)
        layout.addLayout(cover_resize_bar)
        layout.addLayout(force_bar)

    def _build_profile_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()

        row.addWidget(QLabel(tr("label_profile")))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(160)
        self._populate_profile_combo()
        self.profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        row.addWidget(self.profile_combo)

        add_profile_btn = QPushButton(tr("btn_add_profile"))
        add_profile_btn.clicked.connect(self._add_profile)
        row.addWidget(add_profile_btn)

        delete_profile_btn = QPushButton(tr("btn_delete_profile"))
        delete_profile_btn.clicked.connect(self._delete_profile)
        row.addWidget(delete_profile_btn)

        row.addStretch()
        return row

    def _populate_profile_combo(self, select_name: str | None = None):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem("", None)
        for profile in self.settings.profiles:
            self.profile_combo.addItem(profile["name"], profile["name"])
        if select_name:
            idx = self.profile_combo.findData(select_name)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)

    def _add_profile(self):
        name, ok = QInputDialog.getText(self, tr("dialog_add_profile_title"), tr("dialog_add_profile_label"))
        name = name.strip()
        if not ok or not name:
            return

        existing_names = {p["name"] for p in self.settings.profiles}
        if name in existing_names:
            reply = QMessageBox.question(self, tr("msg_profile_exists_title"), tr("msg_profile_exists_text", name=name))
            if reply != QMessageBox.Yes:
                return
            self.settings.profiles = [p for p in self.settings.profiles if p["name"] != name]

        profile = {
            "name": name,
            "dir_template": self.settings.dir_template,
            "filename_template": self.settings.filename_template,
            "convert_enabled": self.convert_checkbox.isChecked(),
            "convert_target": self.convert_target_combo.currentData(),
            "cover_resize_enabled": self.resize_cover_checkbox.isChecked(),
            "cover_resize_size": self.cover_size_edit.text(),
            "cover_resize_dpi": self.cover_dpi_edit.text(),
        }
        self.settings.profiles.append(profile)
        self.settings.last_profile_name = name
        self.settings.save(self.project_root)
        self._populate_profile_combo(select_name=name)

    def _delete_profile(self):
        name = self.profile_combo.currentData()
        if not name:
            return
        reply = QMessageBox.question(self, tr("msg_delete_profile_title"), tr("msg_delete_profile_text", name=name))
        if reply != QMessageBox.Yes:
            return
        self.settings.profiles = [p for p in self.settings.profiles if p["name"] != name]
        if self.settings.last_profile_name == name:
            self.settings.last_profile_name = ""
        self.settings.save(self.project_root)
        self._populate_profile_combo()

    def _on_profile_selected(self, index: int):
        name = self.profile_combo.itemData(index)
        if not name:
            return
        profile = next((p for p in self.settings.profiles if p["name"] == name), None)
        if not profile:
            return

        self.settings.dir_template = profile.get("dir_template", self.settings.dir_template)
        self.settings.filename_template = profile.get("filename_template", self.settings.filename_template)
        self.convert_checkbox.setChecked(bool(profile.get("convert_enabled", False)))
        target_idx = self.convert_target_combo.findData(profile.get("convert_target"))
        if target_idx >= 0:
            self.convert_target_combo.setCurrentIndex(target_idx)
        self.resize_cover_checkbox.setChecked(bool(profile.get("cover_resize_enabled", False)))
        self.cover_size_edit.setText(str(profile.get("cover_resize_size", "500")))
        self.cover_dpi_edit.setText(str(profile.get("cover_resize_dpi", "72")))

        self.settings.last_profile_name = name
        self.settings.save(self.project_root)

    def _build_conversion_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()

        self.convert_checkbox = QCheckBox(tr("chk_convert_on_sync"))
        self.convert_checkbox.setToolTip(tr("chk_convert_on_sync_tooltip"))
        row.addWidget(self.convert_checkbox)

        self.convert_target_combo = QComboBox()
        for key in CONVERSION_TARGET_ORDER:
            self.convert_target_combo.addItem(tr(f"conversion_target_{key}"), key)
        self.convert_target_combo.setEnabled(self.convert_checkbox.isChecked())
        self.convert_checkbox.toggled.connect(self.convert_target_combo.setEnabled)
        row.addWidget(self.convert_target_combo)

        row.addStretch()
        return row

    def _build_cover_resize_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()

        self.resize_cover_checkbox = QCheckBox(tr("chk_resize_cover_on_sync"))
        self.resize_cover_checkbox.setToolTip(tr("chk_resize_cover_on_sync_tooltip"))
        row.addWidget(self.resize_cover_checkbox)

        row.addWidget(QLabel(tr("label_cover_size")))
        self.cover_size_edit = QLineEdit("500")
        self.cover_size_edit.setValidator(QIntValidator(1, 10000, self))
        self.cover_size_edit.setMaximumWidth(70)
        row.addWidget(self.cover_size_edit)

        row.addWidget(QLabel(tr("label_cover_dpi")))
        self.cover_dpi_edit = QLineEdit("72")
        self.cover_dpi_edit.setValidator(QIntValidator(1, 2400, self))
        self.cover_dpi_edit.setMaximumWidth(70)
        row.addWidget(self.cover_dpi_edit)

        self.cover_size_edit.setEnabled(self.resize_cover_checkbox.isChecked())
        self.cover_dpi_edit.setEnabled(self.resize_cover_checkbox.isChecked())
        self.resize_cover_checkbox.toggled.connect(self.cover_size_edit.setEnabled)
        self.resize_cover_checkbox.toggled.connect(self.cover_dpi_edit.setEnabled)

        row.addStretch()
        return row

    def _build_force_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()

        self.force_checkbox = QCheckBox(tr("chk_force_sync"))
        self.force_checkbox.setToolTip(tr("chk_force_sync_tooltip"))
        row.addWidget(self.force_checkbox)

        row.addStretch()
        return row

    def _build_toolbar(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)

        left = QWidget()
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        choose_dir_btn = QPushButton(tr("btn_choose_dir"))
        choose_dir_btn.clicked.connect(self._choose_source_directory)
        left_layout.addWidget(choose_dir_btn)

        rescan_btn = QPushButton(tr("btn_rescan"))
        rescan_btn.clicked.connect(self._rescan_source)
        left_layout.addWidget(rescan_btn)
        left_layout.addStretch()

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        sync_btn = QPushButton(tr("btn_sync"))
        sync_btn.clicked.connect(self._run_sync)
        center_layout.addWidget(sync_btn)

        sync_from_device_btn = QPushButton(tr("btn_sync_from_device"))
        sync_from_device_btn.clicked.connect(self._run_reverse_sync)
        center_layout.addWidget(sync_from_device_btn)

        right = QWidget()
        right_layout = QHBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addStretch()
        right_layout.addWidget(QLabel(tr("label_target_device")))

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(220)
        self.device_combo.currentIndexChanged.connect(self._on_device_selected)
        right_layout.addWidget(self.device_combo)

        refresh_devices_btn = QPushButton(tr("btn_refresh_devices"))
        refresh_devices_btn.clicked.connect(self._refresh_devices)
        right_layout.addWidget(refresh_devices_btn)

        rescan_device_btn = QPushButton(tr("btn_rescan_device"))
        rescan_device_btn.clicked.connect(self._rescan_device)
        right_layout.addWidget(rescan_device_btn)

        eject_btn = QPushButton(tr("btn_eject"))
        eject_btn.clicked.connect(self._eject_device)
        right_layout.addWidget(eject_btn)

        right_layout.addSpacing(20)
        settings_btn = QPushButton(tr("btn_settings"))
        settings_btn.clicked.connect(self._open_settings)
        right_layout.addWidget(settings_btn)

        grid.addWidget(left, 0, 0)
        grid.addWidget(center, 0, 1, Qt.AlignHCenter)
        grid.addWidget(right, 0, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 1)

        # Force the outer columns to the same minimum width so the center
        # column (sync buttons) sits exactly in the middle regardless of how
        # much content the left/right sides hold.
        equal_width = max(left.sizeHint().width(), right.sizeHint().width())
        grid.setColumnMinimumWidth(0, equal_width)
        grid.setColumnMinimumWidth(2, equal_width)

        return grid

    def _build_pane(
        self, title: str, table_view: QTableView, tree_widget: QTreeWidget, toggle_check_all_handler
    ) -> tuple[QWidget, QPushButton]:
        pane = QWidget()
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.addWidget(QLabel(f"<b>{title}</b>"), 0)

        tabs = QTabWidget()
        tabs.addTab(table_view, tr("tab_table"))
        tabs.addTab(tree_widget, tr("tab_tree"))
        pane_layout.addWidget(tabs, 1)

        check_row = QHBoxLayout()
        check_row.addStretch()
        check_all_btn = QPushButton(tr("btn_check_all"))
        fm = check_all_btn.fontMetrics()
        width = max(fm.horizontalAdvance(tr("btn_check_all")), fm.horizontalAdvance(tr("btn_uncheck_all"))) + 40
        check_all_btn.setFixedWidth(width)
        check_all_btn.clicked.connect(toggle_check_all_handler)
        check_row.addWidget(check_all_btn)
        pane_layout.addLayout(check_row)
        return pane, check_all_btn

    def _build_table_view(self, presence_label: str, checked_hashes: set[str], on_check_changed) -> QTableView:
        view = QTableView()
        model = TrackTableModel(presence_label=presence_label, checked_hashes=checked_hashes, on_check_changed=on_check_changed)
        proxy_model = QSortFilterProxyModel()
        proxy_model.setSourceModel(model)
        # Sort by the raw value each column's data() returns under
        # Qt.UserRole (numbers as numbers, text through polish_sort_key)
        # instead of the proxy's default of comparing the formatted
        # DisplayRole strings shown in the cells.
        proxy_model.setSortRole(Qt.UserRole)
        view.setModel(proxy_model)
        view.setSortingEnabled(True)
        view.setColumnWidth(0, 28)
        view.setSelectionBehavior(QAbstractItemView.SelectRows)
        view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        view.setContextMenuPolicy(Qt.CustomContextMenu)
        view.customContextMenuRequested.connect(self._table_context_menu)
        view.doubleClicked.connect(self._edit_selected_table_track)
        view.clicked.connect(self._on_table_clicked)
        header = view.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(lambda pos, v=view: self._show_column_menu(v, pos))
        return view

    def _on_table_clicked(self, index):
        key = COLUMN_KEYS[index.column()][0]
        if key != "checked":
            return
        proxy = index.model()
        source_index = proxy.mapToSource(index)
        source_index.model().toggle_checked(source_index.row())

    def _show_column_menu(self, view: QTableView, pos):
        header = view.horizontalHeader()
        model = view.model()
        menu = QMenu(self)
        for column in range(len(COLUMN_KEYS)):
            label = model.headerData(column, Qt.Horizontal, Qt.DisplayRole)
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not view.isColumnHidden(column))
            action.toggled.connect(lambda checked, c=column, v=view: v.setColumnHidden(c, not checked))
        menu.exec(header.mapToGlobal(pos))

    def _build_tree_widget(self, context_menu_handler, double_click_handler) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setHeaderLabels([tr("tree_header_name"), tr("tree_header_year"), tr("tree_header_format")])
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(context_menu_handler)
        if double_click_handler:
            tree.itemDoubleClicked.connect(double_click_handler)
        tree.itemChanged.connect(self._on_tree_item_changed)
        return tree

    # ---------- source directory / scanning ----------

    def _choose_source_directory(self):
        directory = QFileDialog.getExistingDirectory(self, tr("dialog_choose_dir_title"))
        if not directory:
            return
        self.source_root = Path(directory)
        if self.library_db:
            self.library_db.close()
        self.library_db = MusicDatabase(library_db_path(self.project_root))
        self.settings.last_source_root = str(self.source_root)
        self.settings.save(self.project_root)
        self._rescan_source()

    def _restore_last_source(self):
        if not self.settings.last_source_root:
            return
        directory = Path(self.settings.last_source_root)
        if not directory.is_dir():
            return
        self.source_root = directory
        self.library_db = MusicDatabase(library_db_path(self.project_root))
        self._refresh_views()

    def _scan_with_progress(self, root: Path, db: MusicDatabase) -> tuple[list, bool]:
        """Scan root and reconcile db with what's actually on disk (drops
        stale rows for files removed outside the app), showing a progress
        dialog. Returns the up-to-date tracks plus whether the user cancelled."""
        progress = QProgressDialog(tr("progress_scanning_label_initial"), tr("progress_cancel"), 0, 0, self)
        progress.setWindowTitle(f"{APP_NAME} — {tr('progress_scanning_title')}")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        def on_progress(index: int, total: int, path: Path):
            if total:
                progress.setMaximum(total)
                progress.setValue(index)
            progress.setLabelText(tr("progress_scanning_label", index=index, total=total, name=path.name))
            QApplication.processEvents()

        tracks = scan_directory(
            root,
            db,
            progress_callback=on_progress,
            should_stop=progress.wasCanceled,
        )
        cancelled = progress.wasCanceled()
        progress.close()
        return tracks, cancelled

    def _rescan_source(self):
        if not self.source_root or not self.library_db:
            QMessageBox.information(self, tr("msg_no_directory_title"), tr("msg_no_directory_text"))
            return

        tracks, cancelled = self._scan_with_progress(self.source_root, self.library_db)

        self._refresh_views()
        if cancelled:
            self.status_label.setText(tr("status_scan_cancelled", count=len(tracks)))
        else:
            # Track/album/artist counts are now shown directly under each
            # panel (see _refresh_source_views), so a redundant "found N
            # tracks" status line isn't needed on a normal, uncancelled scan.
            self.status_label.clear()

    def _rescan_device(self):
        if not self.selected_device or not self.device_db:
            QMessageBox.information(self, tr("msg_no_device_title"), tr("msg_no_device_text"))
            return

        tracks, cancelled = self._scan_device_directory()

        self.device_hashes = self.device_db.source_hashes()
        self._refresh_views()
        if cancelled:
            self.status_label.setText(tr("status_scan_cancelled", count=len(tracks)))
        else:
            self.status_label.clear()

    def _scan_device_directory(self) -> tuple[list, bool]:
        return self._scan_with_progress(Path(self.selected_device.mountpoint), self.device_db)

    def _refresh_views(self):
        self._refresh_source_views()
        self._refresh_device_views()

    def _refresh_source_views(self):
        if not self.library_db:
            # Left blank rather than hidden: an empty QLabel still reserves
            # its line height, so the pane doesn't grow/shrink the moment a
            # library gets chosen -- see the identical reasoning on the
            # device side (_clear_device_status).
            self.source_stats_label.setText("")
            return
        tracks = self.library_db.all_tracks()
        self.table_model.set_tracks(tracks)
        self.table_model.set_device_hashes(self.device_hashes)
        self._populate_tree(self.tree_widget, tracks, self.device_hashes, self.source_checked_hashes)
        self._update_check_all_button(self.source_check_all_btn, self.table_model)
        self.source_stats_label.setText(tr("stats_label", **self._track_stats(tracks)))

    def _refresh_device_views(self):
        tracks = self.device_db.all_tracks() if self.device_db else []
        library_hashes = self.library_db.source_hashes() if self.library_db else set()
        self.device_table_model.set_tracks(tracks)
        self.device_table_model.set_device_hashes(library_hashes)
        self._populate_tree(self.device_tree_widget, tracks, library_hashes, self.device_checked_hashes)
        self._update_check_all_button(self.device_check_all_btn, self.device_table_model)
        self.device_stats_label.setText(tr("stats_label", **self._track_stats(tracks)) if self.device_db else "")

    @staticmethod
    def _track_stats(tracks: list[Track]) -> dict[str, int]:
        # Same artist/album grouping (with the "unknown" fallback) as the
        # tree view, so these counts match what it actually shows.
        artists: set[str] = set()
        albums: set[tuple[str, str]] = set()
        for track in tracks:
            artist = track.artist or tr("unknown_artist")
            artists.add(artist)
            albums.add((artist, track.album or tr("unknown_album")))
        return {"artists": len(artists), "albums": len(albums), "tracks": len(tracks)}

    def _toggle_check_all_source(self):
        if not self.library_db:
            return
        if self.table_model.all_checked():
            self.source_checked_hashes.clear()
        else:
            self.source_checked_hashes.update(t.hash for t in self.library_db.all_tracks())
        self._refresh_source_views()

    def _toggle_check_all_device(self):
        if not self.device_db:
            return
        if self.device_table_model.all_checked():
            self.device_checked_hashes.clear()
        else:
            self.device_checked_hashes.update(t.hash for t in self.device_db.all_tracks())
        self._refresh_device_views()

    @staticmethod
    def _update_check_all_button(button: QPushButton, table_model: TrackTableModel):
        button.setText(tr("btn_uncheck_all") if table_model.all_checked() else tr("btn_check_all"))

    def _populate_tree(
        self, tree_widget: QTreeWidget, tracks: list[Track], other_hashes: set[str], checked_hashes: set[str]
    ):
        self._updating_checks = True
        try:
            tree_widget.clear()
            by_artist: dict[str, dict[str, list[Track]]] = {}
            for track in tracks:
                artist = track.artist or tr("unknown_artist")
                album = track.album or tr("unknown_album")
                by_artist.setdefault(artist, {}).setdefault(album, []).append(track)

            for artist in sorted(by_artist, key=polish_sort_key):
                artist_item = QTreeWidgetItem([artist])
                artist_item.setData(0, Qt.UserRole, {"type": "artist", "artist": artist})
                artist_item.setFlags(artist_item.flags() | Qt.ItemIsUserCheckable)
                artist_present_flags = []
                artist_checked_flags = []
                for album in sorted(by_artist[artist], key=polish_sort_key):
                    album_item = QTreeWidgetItem([album])
                    album_item.setData(0, Qt.UserRole, {"type": "album", "artist": artist, "album": album})
                    album_item.setFlags(album_item.flags() | Qt.ItemIsUserCheckable)
                    album_present_flags = []
                    album_checked_flags = []
                    for track in by_artist[artist][album]:
                        label = (
                            f"{tagsmod.fix_track_number(track.track_number)}. {track.title}"
                            if track.track_number
                            else track.title
                        )
                        track_item = QTreeWidgetItem([label, track.year, track.format])
                        track_item.setData(0, Qt.UserRole, {"type": "track", "track": track})
                        track_item.setFlags(track_item.flags() | Qt.ItemIsUserCheckable)
                        checked = track.hash in checked_hashes
                        track_item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
                        present = track.source_hash in other_hashes
                        if present:
                            track_item.setIcon(0, full_presence_icon())
                        album_present_flags.append(present)
                        album_checked_flags.append(checked)
                        album_item.addChild(track_item)
                    album_item.setCheckState(0, self._aggregate_check_state(album_checked_flags))
                    self._set_presence_icon(album_item, album_present_flags)
                    artist_present_flags.extend(album_present_flags)
                    artist_checked_flags.extend(album_checked_flags)
                    artist_item.addChild(album_item)
                artist_item.setCheckState(0, self._aggregate_check_state(artist_checked_flags))
                self._set_presence_icon(artist_item, artist_present_flags)
                tree_widget.addTopLevelItem(artist_item)

            tree_widget.expandToDepth(0)
        finally:
            self._updating_checks = False

    def _aggregate_check_state(self, flags: list[bool]) -> Qt.CheckState:
        if flags and all(flags):
            return Qt.Checked
        if any(flags):
            return Qt.PartiallyChecked
        return Qt.Unchecked

    def _set_presence_icon(self, item: QTreeWidgetItem, present_flags: list[bool]):
        if present_flags and all(present_flags):
            item.setIcon(0, full_presence_icon())
        elif any(present_flags):
            item.setIcon(0, partial_presence_icon())

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int):
        if column != 0 or self._updating_checks:
            return
        self._updating_checks = True
        try:
            state = item.checkState(0)
            if state != Qt.PartiallyChecked:
                self._set_children_check_state(item, state)
            self._update_parent_check_state(item.parent())
            self._sync_hash_set_from_tree_item(item)
        finally:
            self._updating_checks = False

    def _set_children_check_state(self, item: QTreeWidgetItem, state: Qt.CheckState):
        for i in range(item.childCount()):
            child = item.child(i)
            child.setCheckState(0, state)
            self._set_children_check_state(child, state)

    def _update_parent_check_state(self, parent: QTreeWidgetItem | None):
        if parent is None:
            return
        states = {parent.child(i).checkState(0) for i in range(parent.childCount())}
        parent.setCheckState(0, states.pop() if len(states) == 1 else Qt.PartiallyChecked)
        self._update_parent_check_state(parent.parent())

    def _checked_state_for_tree(self, tree_widget: QTreeWidget):
        if tree_widget is self.tree_widget:
            return self.source_checked_hashes, self.table_model
        return self.device_checked_hashes, self.device_table_model

    def _sync_hash_set_from_tree_item(self, item: QTreeWidgetItem):
        checked_hashes, table_model = self._checked_state_for_tree(item.treeWidget())
        changed = False

        def walk(node: QTreeWidgetItem):
            nonlocal changed
            data = node.data(0, Qt.UserRole) or {}
            if data.get("type") == "track":
                track_hash = data["track"].hash
                is_checked = node.checkState(0) == Qt.Checked
                if is_checked and track_hash not in checked_hashes:
                    checked_hashes.add(track_hash)
                    changed = True
                elif not is_checked and track_hash in checked_hashes:
                    checked_hashes.discard(track_hash)
                    changed = True
            for i in range(node.childCount()):
                walk(node.child(i))

        walk(item)
        if changed:
            table_model.refresh_checked()
            button = self.source_check_all_btn if item.treeWidget() is self.tree_widget else self.device_check_all_btn
            self._update_check_all_button(button, table_model)

    def _sync_check_state_to_tree(self, tree_widget: QTreeWidget, track_hash: str, checked: bool):
        self._updating_checks = True
        try:
            def walk(item: QTreeWidgetItem) -> bool:
                data = item.data(0, Qt.UserRole) or {}
                if data.get("type") == "track" and data["track"].hash == track_hash:
                    item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
                    self._update_parent_check_state(item.parent())
                    return True
                for i in range(item.childCount()):
                    if walk(item.child(i)):
                        return True
                return False

            for i in range(tree_widget.topLevelItemCount()):
                if walk(tree_widget.topLevelItem(i)):
                    break
        finally:
            self._updating_checks = False

    def _on_source_table_check_changed(self, track_hash: str, checked: bool):
        self._sync_check_state_to_tree(self.tree_widget, track_hash, checked)
        self._update_check_all_button(self.source_check_all_btn, self.table_model)

    def _on_device_table_check_changed(self, track_hash: str, checked: bool):
        self._sync_check_state_to_tree(self.device_tree_widget, track_hash, checked)
        self._update_check_all_button(self.device_check_all_btn, self.device_table_model)

    # ---------- devices ----------

    def _refresh_devices(self):
        current_mount = self.selected_device.mountpoint if self.selected_device else None
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem(tr("device_none"), None)

        found_devices = devicesmod.list_storage_devices()
        restore_index = 0
        for i, dev in enumerate(found_devices, start=1):
            label = f"{dev.label or dev.name} ({dev.mountpoint}, {dev.size})"
            self.device_combo.addItem(label, dev)
            if dev.mountpoint == current_mount:
                restore_index = i

        self.device_combo.setCurrentIndex(restore_index)
        self.device_combo.blockSignals(False)
        # Re-listing the devices says nothing about their contents, so the
        # already-selected device keeps its scanned state -- only a device
        # we weren't on before is worth the full (hash-every-file) scan.
        restored = self.device_combo.itemData(restore_index)
        same_device = restored is not None and restored.mountpoint == current_mount
        self._on_device_selected(restore_index, rescan=not same_device)

    def _on_device_selected(self, index: int, rescan: bool = True):
        """Switch to the device at `index`. `rescan=False` skips the full
        device scan, for callers that just brought its database up to date
        themselves (a finished sync, a deletion) -- scanning re-hashes every
        file on the card, so it must not be the default reaction to any
        refresh."""
        self.selected_device = self.device_combo.itemData(index)
        if self.device_db:
            self.device_db.close()
            self.device_db = None
        if self.selected_device:
            logger.info(
                tr(
                    "log_device_selected",
                    label=self.selected_device.label or self.selected_device.name,
                    mount=self.selected_device.mountpoint,
                )
            )
            self.device_db = MusicDatabase(device_db_path(Path(self.selected_device.mountpoint)))
            if rescan:
                self._scan_device_directory()
            self.device_hashes = self.device_db.source_hashes()
        else:
            self.device_hashes = set()
        self._update_device_space()
        self._refresh_views()

    def _clear_device_status(self) -> None:
        # Cleared rather than hidden: this row (stats, bar, free/total text)
        # must occupy the same height whether or not a device is selected,
        # otherwise the panel resizes every time a device gets picked or
        # deselected -- a QLabel/QProgressBar left visible with blank
        # content still reserves its normal height, an invisible one doesn't.
        self.device_stats_label.setText("")
        self.device_space_bar.setValue(0)
        self.device_space_label.setText("")

    def _update_device_space(self):
        if not self.selected_device:
            self._clear_device_status()
            return
        try:
            usage = shutil.disk_usage(self.selected_device.mountpoint)
        except OSError:
            self._clear_device_status()
            return
        used_percent = round(usage.used / usage.total * 100) if usage.total else 0
        self.device_space_bar.setValue(used_percent)
        self.device_space_label.setText(
            tr("device_space_label", free=format_size(usage.free), total=format_size(usage.total))
        )

    def _eject_device(self):
        if not self.selected_device:
            QMessageBox.information(self, tr("msg_no_device_title"), tr("msg_no_device_text"))
            return

        device = self.selected_device
        if self.device_db:
            self.device_db.close()
            self.device_db = None

        logger.info(tr("log_eject_starting", mount=device.mountpoint))

        self._eject_progress = QProgressDialog(tr("progress_ejecting_label"), None, 0, 0, self)
        self._eject_progress.setWindowTitle(f"{APP_NAME} — {tr('progress_ejecting_title')}")
        self._eject_progress.setWindowModality(Qt.WindowModal)
        self._eject_progress.setMinimumDuration(0)
        self._eject_progress.setCancelButton(None)
        self._eject_progress.show()

        self._eject_device_pending = device

        # Keep the thread/worker alive on self so they aren't garbage-collected
        # while the background thread is still running.
        self._eject_thread = QThread(self)
        self._eject_worker = _EjectWorker(device)
        self._eject_worker.moveToThread(self._eject_thread)
        self._eject_thread.started.connect(self._eject_worker.run)
        # Must connect to a bound method of a QObject living on the GUI
        # thread (not a plain function/lambda) -- only then does Qt reliably
        # dispatch the slot call to the GUI thread instead of running it
        # inline on the worker thread, which would touch widgets unsafely.
        self._eject_worker.finished.connect(self._on_eject_finished)
        self._eject_thread.start()

    def _on_eject_finished(self, success: bool, error: str):
        device = self._eject_device_pending
        self._eject_progress.close()
        self._eject_thread.quit()
        self._eject_thread.wait()
        self._eject_thread = None
        self._eject_worker = None
        self._eject_progress = None
        self._eject_device_pending = None
        if success:
            if error:
                logger.warning(tr("log_eject_poweroff_failed", mount=device.mountpoint, error=error))
            else:
                logger.info(tr("log_eject_success", mount=device.mountpoint))
            QMessageBox.information(self, tr("msg_eject_success_title"), tr("msg_eject_success_text"))
        else:
            logger.error(tr("log_eject_failed", mount=device.mountpoint, error=error))
            QMessageBox.critical(self, tr("msg_eject_failed_title"), tr("msg_eject_failed_text", error=error))
        self._refresh_devices()

    # ---------- sync ----------

    def _run_sync(self):
        if not self.library_db:
            QMessageBox.information(self, tr("msg_no_library_title"), tr("msg_no_library_text"))
            return
        if self.source_checked_hashes:
            tracks = [t for t in self.library_db.all_tracks() if t.hash in self.source_checked_hashes]
            description = tr("sync_checked_description", count=len(tracks))
        else:
            tracks = self.library_db.all_tracks()
            description = tr("sync_whole_library")
        self._sync_tracks(tracks, description)

    def _run_reverse_sync(self):
        if not self.selected_device or not self.device_db:
            QMessageBox.information(self, tr("msg_no_device_title"), tr("msg_no_device_text"))
            return
        if self.device_checked_hashes:
            tracks = [t for t in self.device_db.all_tracks() if t.hash in self.device_checked_hashes]
            description = tr("sync_checked_description", count=len(tracks))
        else:
            tracks = self.device_db.all_tracks()
            description = tr("sync_whole_device")
        self._sync_tracks_from_device(tracks, description)

    def _tracks_for_artist(self, artist: str) -> list[Track]:
        return [t for t in self.library_db.all_tracks() if (t.artist or tr("unknown_artist")) == artist]

    def _tracks_for_album(self, artist: str, album: str) -> list[Track]:
        return [
            t
            for t in self.library_db.all_tracks()
            if (t.artist or tr("unknown_artist")) == artist and (t.album or tr("unknown_album")) == album
        ]

    def _sync_tracks(self, tracks: list[Track], description: str):
        if not self.source_root or not self.library_db:
            QMessageBox.information(self, tr("msg_no_library_title"), tr("msg_no_library_text"))
            return
        if not self.selected_device:
            QMessageBox.information(self, tr("msg_no_device_title"), tr("msg_no_device_text"))
            return
        if not tracks:
            return

        conversion = None
        if self.convert_checkbox.isChecked():
            if not ffmpeg_available():
                QMessageBox.critical(self, tr("msg_ffmpeg_missing_title"), tr("msg_ffmpeg_missing_text"))
                return
            conversion = ConversionSettings(
                target_key=self.convert_target_combo.currentData(),
                use_libsoxr=self.settings.use_libsoxr,
            )

        cover_resize = None
        if self.resize_cover_checkbox.isChecked():
            size_text = self.cover_size_edit.text().strip()
            dpi_text = self.cover_dpi_edit.text().strip()
            if not size_text or not dpi_text:
                QMessageBox.critical(self, tr("msg_cover_resize_invalid_title"), tr("msg_cover_resize_invalid_text"))
                return
            cover_resize = CoverResizeSettings(max_size=int(size_text), dpi=int(dpi_text))

        reply = QMessageBox.question(
            self,
            tr("confirm_sync_title"),
            tr("confirm_sync_text", description=description, mount=self.selected_device.mountpoint),
        )
        if reply != QMessageBox.Yes:
            return

        progress = _TransferProgressDialog(
            tr("progress_sync_label_initial"), tr("progress_cancel"), 0, len(tracks), self
        )
        progress.setWindowTitle(f"{APP_NAME} — {tr('progress_sync_title')}")
        progress.setWindowModality(Qt.WindowModal)
        progress.show()

        def on_scan_progress(index, total, path: Path):
            if total:
                progress.setMaximum(total)
                progress.setValue(index)
            progress.setLabelText(tr("progress_scanning_label", index=index, total=total, name=path.name))
            QApplication.processEvents()

        def on_progress(index, total, track: Track):
            progress.setMaximum(total)
            progress.setValue(index)
            progress.setLabelText(tr("progress_sync_label", index=index, total=total, name=track.filename))
            QApplication.processEvents()

        def on_conflict(track: Track, target_path: Path) -> ConflictResolution:
            reply = QMessageBox.question(
                self,
                tr("conflict_title"),
                tr("conflict_text", path=target_path),
                QMessageBox.Yes | QMessageBox.No,
            )
            return ConflictResolution.OVERWRITE if reply == QMessageBox.Yes else ConflictResolution.SKIP

        result = sync_to_device(
            self.source_root,
            tracks,
            Path(self.selected_device.mountpoint),
            self.settings.dir_template,
            self.settings.filename_template,
            on_conflict=on_conflict,
            on_progress=on_progress,
            conversion=conversion,
            cover_resize=cover_resize,
            track_no_fix=self.settings.track_no_fix,
            on_scan_progress=on_scan_progress,
            force=self.force_checkbox.isChecked(),
            should_stop=progress.wasCanceled,
        )
        progress.close()

        self.source_checked_hashes.clear()
        self.device_checked_hashes.clear()
        # sync_to_device() scanned the device up front and registered every
        # file it wrote, so its database is already current -- reopen it to
        # pick that up, but don't scan the whole card a second time.
        self._on_device_selected(self.device_combo.currentIndex(), rescan=False)

        self._show_sync_result(result)

    def _sync_tracks_from_device(self, tracks: list[Track], description: str):
        if not self.source_root or not self.library_db:
            QMessageBox.information(self, tr("msg_no_library_title"), tr("msg_no_library_text"))
            return
        if not self.selected_device or not self.device_db:
            QMessageBox.information(self, tr("msg_no_device_title"), tr("msg_no_device_text"))
            return
        if not tracks:
            return

        reply = QMessageBox.question(
            self,
            tr("confirm_sync_title"),
            tr("confirm_sync_from_device_text", description=description, mount=self.selected_device.mountpoint),
        )
        if reply != QMessageBox.Yes:
            return

        progress = _TransferProgressDialog(
            tr("progress_sync_label_initial"), tr("progress_cancel"), 0, len(tracks), self
        )
        progress.setWindowTitle(f"{APP_NAME} — {tr('progress_sync_title')}")
        progress.setWindowModality(Qt.WindowModal)
        progress.show()

        def on_scan_progress(index, total, path: Path):
            if total:
                progress.setMaximum(total)
                progress.setValue(index)
            progress.setLabelText(tr("progress_scanning_label", index=index, total=total, name=path.name))
            QApplication.processEvents()

        def on_progress(index, total, track: Track):
            progress.setMaximum(total)
            progress.setValue(index)
            progress.setLabelText(tr("progress_sync_label", index=index, total=total, name=track.filename))
            QApplication.processEvents()

        def on_conflict(track: Track, target_path: Path) -> ConflictResolution:
            reply = QMessageBox.question(
                self,
                tr("conflict_title"),
                tr("conflict_text", path=target_path),
                QMessageBox.Yes | QMessageBox.No,
            )
            return ConflictResolution.OVERWRITE if reply == QMessageBox.Yes else ConflictResolution.SKIP

        result = sync_from_device(
            Path(self.selected_device.mountpoint),
            tracks,
            self.source_root,
            self.library_db,
            self.settings.dir_template,
            self.settings.filename_template,
            on_conflict=on_conflict,
            on_progress=on_progress,
            on_scan_progress=on_scan_progress,
            should_stop=progress.wasCanceled,
        )
        progress.close()

        self.source_checked_hashes.clear()
        self.device_checked_hashes.clear()
        self._refresh_views()

        self._show_sync_result(result)

    def _show_sync_result(self, result: SyncResult) -> None:
        message = (
            f"{tr('sync_result_copied')}: {result.copied}\n"
            f"{tr('sync_result_present')}: {result.already_present}\n"
            f"{tr('sync_result_skipped')}: {result.skipped}\n"
            f"{tr('sync_result_errors')}: {len(result.errors)}"
        )
        if result.duplicates_removed:
            message += f"\n{tr('sync_result_duplicates_removed')}: {result.duplicates_removed}"
        if result.cancelled:
            message = f"{tr('sync_result_cancelled_notice')}\n\n{message}"
        if result.errors:
            message += "\n\n" + "\n".join(result.errors[:10])
        title = tr("sync_cancelled_title") if result.cancelled else tr("sync_done_title")
        QMessageBox.information(self, title, message)

    # ---------- tag editing ----------

    def _table_ordered_tracks(self) -> list[Track]:
        tracks = []
        for row in range(self.proxy_model.rowCount()):
            source_row = self.proxy_model.mapToSource(self.proxy_model.index(row, 0)).row()
            tracks.append(self.table_model.track_at(source_row))
        return tracks

    def _flatten_tree_tracks(self) -> list[Track]:
        tracks = []
        for i in range(self.tree_widget.topLevelItemCount()):
            artist_item = self.tree_widget.topLevelItem(i)
            for j in range(artist_item.childCount()):
                album_item = artist_item.child(j)
                for k in range(album_item.childCount()):
                    data = album_item.child(k).data(0, Qt.UserRole) or {}
                    if data.get("type") == "track":
                        tracks.append(data["track"])
        return tracks

    def _edit_selected_table_track(self, proxy_index):
        tracks = self._table_ordered_tracks()
        self._edit_track_sequence(tracks, proxy_index.row())

    def _edit_tree_item(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.UserRole) or {}
        if data.get("type") != "track":
            return
        tracks = self._flatten_tree_tracks()
        index = tracks.index(data["track"]) if data["track"] in tracks else 0
        self._edit_track_sequence(tracks, index)

    def _edit_track_sequence(self, tracks: list[Track], start_index: int):
        if not self.source_root or not self.library_db or not tracks:
            return
        dialog = TagEditDialog(
            self.source_root, tracks, start_index, on_saved=self._persist_track_edit, settings=self.settings, parent=self
        )
        dialog.exec()
        self._refresh_views()

    def _persist_track_edit(self, old_track: Track, fields: dict) -> Track:
        new_path = self.source_root / fields["path"]
        new_hash = hash_file(new_path)
        stat = new_path.stat()
        updated = Track(
            id=old_track.id,
            path=fields["path"],
            filename=new_path.name,
            hash=new_hash,
            source_hash=new_hash,
            artist=fields["artist"],
            album=fields["album"],
            title=fields["title"],
            track_number=fields["track_number"],
            track_total=fields["track_total"],
            disc_number=fields["disc_number"],
            year=fields["year"],
            genre=fields["genre"],
            format=old_track.format,
            size=stat.st_size,
            mtime=stat.st_mtime,
        )
        if updated.path != old_track.path:
            self.library_db.delete_by_path(old_track.path)
        self.library_db.upsert_track(updated)
        return updated

    def _device_table_ordered_tracks(self) -> list[Track]:
        tracks = []
        for row in range(self.device_proxy_model.rowCount()):
            source_row = self.device_proxy_model.mapToSource(self.device_proxy_model.index(row, 0)).row()
            tracks.append(self.device_table_model.track_at(source_row))
        return tracks

    def _flatten_device_tree_tracks(self) -> list[Track]:
        tracks = []
        for i in range(self.device_tree_widget.topLevelItemCount()):
            artist_item = self.device_tree_widget.topLevelItem(i)
            for j in range(artist_item.childCount()):
                album_item = artist_item.child(j)
                for k in range(album_item.childCount()):
                    data = album_item.child(k).data(0, Qt.UserRole) or {}
                    if data.get("type") == "track":
                        tracks.append(data["track"])
        return tracks

    def _flatten_device_tree_albums(self) -> list[list[Track]]:
        albums = []
        for i in range(self.device_tree_widget.topLevelItemCount()):
            artist_item = self.device_tree_widget.topLevelItem(i)
            for j in range(artist_item.childCount()):
                album_item = artist_item.child(j)
                tracks = []
                for k in range(album_item.childCount()):
                    data = album_item.child(k).data(0, Qt.UserRole) or {}
                    if data.get("type") == "track":
                        tracks.append(data["track"])
                if tracks:
                    albums.append(tracks)
        return albums

    def _edit_selected_device_table_track(self, proxy_index):
        tracks = self._device_table_ordered_tracks()
        self._edit_device_track_sequence(tracks, proxy_index.row())

    def _edit_device_tree_item(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.UserRole) or {}
        if data.get("type") != "track":
            return
        tracks = self._flatten_device_tree_tracks()
        index = tracks.index(data["track"]) if data["track"] in tracks else 0
        self._edit_device_track_sequence(tracks, index)

    def _edit_device_track_sequence(self, tracks: list[Track], start_index: int):
        if not self.selected_device or not self.device_db or not tracks:
            return
        dialog = TagEditDialog(
            Path(self.selected_device.mountpoint),
            tracks,
            start_index,
            on_saved=self._persist_device_track_edit,
            settings=self.settings,
            parent=self,
        )
        dialog.exec()
        self._refresh_views()

    def _edit_device_album_tags(self, artist: str, album: str):
        if not self.selected_device or not self.device_db:
            return
        albums = self._flatten_device_tree_albums()
        if not albums:
            return
        start_index = 0
        for idx, tracks in enumerate(albums):
            first = tracks[0]
            if (first.artist or tr("unknown_artist")) == artist and (first.album or tr("unknown_album")) == album:
                start_index = idx
                break
        dialog = AlbumEditDialog(
            Path(self.selected_device.mountpoint),
            albums,
            start_index,
            on_saved=self._persist_device_track_edit,
            settings=self.settings,
            parent=self,
        )
        dialog.exec()
        self._refresh_views()

    def _persist_device_track_edit(self, old_track: Track, fields: dict) -> Track:
        device_root = Path(self.selected_device.mountpoint)
        new_path = device_root / fields["path"]
        new_hash = hash_file(new_path)
        stat = new_path.stat()
        updated = Track(
            id=old_track.id,
            path=fields["path"],
            filename=new_path.name,
            hash=new_hash,
            source_hash=old_track.source_hash,
            artist=fields["artist"],
            album=fields["album"],
            title=fields["title"],
            track_number=fields["track_number"],
            track_total=fields["track_total"],
            disc_number=fields["disc_number"],
            year=fields["year"],
            genre=fields["genre"],
            format=old_track.format,
            size=stat.st_size,
            mtime=stat.st_mtime,
        )
        if updated.path != old_track.path:
            self.device_db.delete_by_path(old_track.path)
        self.device_db.upsert_track(updated)
        return updated

    # ---------- context menus ----------

    def _show_media_info(self, track: Track, from_device: bool):
        if from_device:
            if not self.selected_device:
                return
            file_path = Path(self.selected_device.mountpoint) / track.path
        else:
            if not self.source_root:
                return
            file_path = self.source_root / track.path

        if not file_path.exists():
            QMessageBox.warning(self, tr("msg_file_missing_title"), tr("msg_file_missing_text"))
            return

        info = tagsmod.read_media_info(file_path)
        MediaInfoDialog(file_path, info, self).exec()

    def _table_context_menu(self, pos):
        index = self.table_view.indexAt(pos)
        if not index.isValid():
            return
        tracks = self._table_ordered_tracks()
        track = tracks[index.row()]
        self._show_track_menu(track, tracks, index.row(), self.table_view.viewport().mapToGlobal(pos))

    def _tree_context_menu(self, pos):
        item = self.tree_widget.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.UserRole) or {}
        item_type = data.get("type")
        global_pos = self.tree_widget.viewport().mapToGlobal(pos)

        if item_type == "track":
            tracks = self._flatten_tree_tracks()
            track = data["track"]
            index = tracks.index(track) if track in tracks else 0
            self._show_track_menu(track, tracks, index, global_pos)
        elif item_type == "album":
            self._show_album_menu(global_pos, data["artist"], data["album"])
        elif item_type == "artist":
            self._show_artist_menu(global_pos, data["artist"])

    def _device_table_context_menu(self, pos):
        index = self.device_table_view.indexAt(pos)
        if not index.isValid():
            return
        tracks = self._device_table_ordered_tracks()
        source_row = self.device_proxy_model.mapToSource(self.device_proxy_model.index(index.row(), 0)).row()
        track = self.device_table_model.track_at(source_row)
        self._show_device_track_menu(track, tracks, index.row(), self.device_table_view.viewport().mapToGlobal(pos))

    def _device_tree_context_menu(self, pos):
        item = self.device_tree_widget.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.UserRole) or {}
        item_type = data.get("type")
        global_pos = self.device_tree_widget.viewport().mapToGlobal(pos)

        if item_type == "track":
            tracks = self._flatten_device_tree_tracks()
            track = data["track"]
            index = tracks.index(track) if track in tracks else 0
            self._show_device_track_menu(track, tracks, index, global_pos)
        elif item_type == "album":
            self._show_device_album_menu(global_pos, data["artist"], data["album"])
        elif item_type == "artist":
            self._show_device_artist_menu(global_pos, data["artist"])

    def _show_device_track_menu(self, device_track: Track, tracks: list[Track], index: int, global_pos):
        menu = QMenu(self)
        edit_action = menu.addAction(tr("menu_edit_tags"))
        media_info_action = menu.addAction(tr("menu_media_info"))
        delete_action = menu.addAction(tr("menu_delete_from_device"))
        action = menu.exec(global_pos)
        if action == edit_action:
            self._edit_device_track_sequence(tracks, index)
        elif action == media_info_action:
            self._show_media_info(device_track, from_device=True)
        elif action == delete_action:
            self._delete_device_track(device_track)

    def _show_device_album_menu(self, global_pos, artist: str, album: str):
        menu = QMenu(self)
        edit_action = menu.addAction(tr("menu_edit_album_tags", album=album))
        delete_action = menu.addAction(tr("menu_delete_album_from_device"))
        action = menu.exec(global_pos)
        if action == edit_action:
            self._edit_device_album_tags(artist, album)
        elif action == delete_action:
            self._delete_device_album(artist, album)

    def _show_device_artist_menu(self, global_pos, artist: str):
        menu = QMenu(self)
        delete_action = menu.addAction(tr("menu_delete_artist_from_device"))
        action = menu.exec(global_pos)
        if action == delete_action:
            self._delete_device_artist(artist)

    def _delete_device_track(self, device_track: Track):
        if not self.selected_device:
            return
        reply = QMessageBox.question(
            self,
            tr("confirm_delete_device_title"),
            tr("confirm_delete_device_text", title=device_track.title, mount=self.selected_device.mountpoint),
        )
        if reply != QMessageBox.Yes:
            return

        self._delete_device_tracks([device_track])

    def _show_track_menu(self, track: Track, tracks: list[Track], index: int, global_pos):
        menu = QMenu(self)
        edit_action = menu.addAction(tr("menu_edit_tags"))
        media_info_action = menu.addAction(tr("menu_media_info"))
        sync_action = menu.addAction(tr("menu_sync_track"))
        delete_device_action = None
        if self.selected_device and track.source_hash in self.device_hashes:
            delete_device_action = menu.addAction(tr("menu_delete_from_device"))
        delete_action = menu.addAction(tr("menu_delete_track"))

        action = menu.exec(global_pos)
        if action == edit_action:
            self._edit_track_sequence(tracks, index)
        elif action == media_info_action:
            self._show_media_info(track, from_device=False)
        elif action == sync_action:
            self._sync_tracks([track], tr("sync_track_description", title=track.title))
        elif delete_device_action and action == delete_device_action:
            self._delete_from_device(track)
        elif action == delete_action:
            self._delete_library_track(track)

    def _show_artist_menu(self, global_pos, artist: str):
        menu = QMenu(self)
        sync_action = menu.addAction(tr("menu_sync_artist", artist=artist))
        delete_action = menu.addAction(tr("menu_delete_artist"))
        action = menu.exec(global_pos)
        if action == sync_action:
            self._sync_tracks(self._tracks_for_artist(artist), tr("sync_artist_description", artist=artist))
        elif action == delete_action:
            self._delete_library_artist(artist)

    def _show_album_menu(self, global_pos, artist: str, album: str):
        menu = QMenu(self)
        edit_action = menu.addAction(tr("menu_edit_album_tags", album=album))
        sync_action = menu.addAction(tr("menu_sync_album", album=album))
        delete_action = menu.addAction(tr("menu_delete_album"))
        action = menu.exec(global_pos)
        if action == edit_action:
            self._edit_album_tags(artist, album)
        elif action == sync_action:
            self._sync_tracks(self._tracks_for_album(artist, album), tr("sync_album_description", album=album))
        elif action == delete_action:
            self._delete_library_album(artist, album)

    def _flatten_tree_albums(self) -> list[list[Track]]:
        albums = []
        for i in range(self.tree_widget.topLevelItemCount()):
            artist_item = self.tree_widget.topLevelItem(i)
            for j in range(artist_item.childCount()):
                album_item = artist_item.child(j)
                tracks = []
                for k in range(album_item.childCount()):
                    data = album_item.child(k).data(0, Qt.UserRole) or {}
                    if data.get("type") == "track":
                        tracks.append(data["track"])
                if tracks:
                    albums.append(tracks)
        return albums

    def _edit_album_tags(self, artist: str, album: str):
        if not self.source_root or not self.library_db:
            return
        albums = self._flatten_tree_albums()
        if not albums:
            return
        start_index = 0
        for idx, tracks in enumerate(albums):
            first = tracks[0]
            if (first.artist or tr("unknown_artist")) == artist and (first.album or tr("unknown_album")) == album:
                start_index = idx
                break
        dialog = AlbumEditDialog(
            self.source_root, albums, start_index, on_saved=self._persist_track_edit, settings=self.settings, parent=self
        )
        dialog.exec()
        self._refresh_views()

    def _delete_from_device(self, track: Track):
        if not self.selected_device or not self.device_db:
            return
        device_track = self.device_db.get_by_source_hash(track.hash)
        if not device_track:
            return
        self._delete_device_track(device_track)

    # ---------- deletion ----------

    def _device_tracks_for_artist(self, artist: str) -> list[Track]:
        return [t for t in self.device_db.all_tracks() if (t.artist or tr("unknown_artist")) == artist]

    def _device_tracks_for_album(self, artist: str, album: str) -> list[Track]:
        return [
            t
            for t in self.device_db.all_tracks()
            if (t.artist or tr("unknown_artist")) == artist and (t.album or tr("unknown_album")) == album
        ]

    def _delete_library_tracks(self, tracks: list[Track]):
        if not self.source_root or not self.library_db or not tracks:
            return
        for track in tracks:
            file_path = self.source_root / track.path
            if file_path.exists():
                file_path.unlink()
                remove_empty_parent_dirs(file_path.parent, self.source_root)
            self.library_db.delete_by_path(track.path)
        self._refresh_views()

    def _delete_library_track(self, track: Track):
        reply = QMessageBox.question(
            self, tr("confirm_delete_track_title"), tr("confirm_delete_track_text", title=track.title)
        )
        if reply != QMessageBox.Yes:
            return
        self._delete_library_tracks([track])

    def _delete_library_album(self, artist: str, album: str):
        tracks = self._tracks_for_album(artist, album)
        if not tracks:
            return
        reply = QMessageBox.question(
            self, tr("confirm_delete_album_title"), tr("confirm_delete_album_text", album=album, count=len(tracks))
        )
        if reply != QMessageBox.Yes:
            return
        self._delete_library_tracks(tracks)

    def _delete_library_artist(self, artist: str):
        tracks = self._tracks_for_artist(artist)
        if not tracks:
            return
        reply = QMessageBox.question(
            self, tr("confirm_delete_artist_title"), tr("confirm_delete_artist_text", artist=artist, count=len(tracks))
        )
        if reply != QMessageBox.Yes:
            return
        self._delete_library_tracks(tracks)

    def _delete_device_tracks(self, tracks: list[Track]):
        if not self.selected_device or not tracks:
            return
        if self.device_db:
            self.device_db.close()
            self.device_db = None

        progress = QProgressDialog(tr("progress_deleting_label_initial"), None, 0, len(tracks), self)
        progress.setWindowTitle(f"{APP_NAME} — {tr('progress_deleting_title')}")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()

        def on_delete_progress(index: int, total: int, track: Track):
            progress.setValue(index - 1)
            progress.setLabelText(tr("progress_deleting_label", index=index, total=total, name=track.filename))
            QApplication.processEvents()

        delete_many_from_device(Path(self.selected_device.mountpoint), tracks, on_progress=on_delete_progress)
        progress.setValue(len(tracks))
        progress.close()

        # Deletion removed exactly these files and their rows, so the device
        # database stays accurate -- no rescan needed to reflect it.
        self._on_device_selected(self.device_combo.currentIndex(), rescan=False)

    def _delete_device_album(self, artist: str, album: str):
        if not self.device_db:
            return
        tracks = self._device_tracks_for_album(artist, album)
        if not tracks:
            return
        reply = QMessageBox.question(
            self,
            tr("confirm_delete_device_album_title"),
            tr("confirm_delete_device_album_text", album=album, count=len(tracks), mount=self.selected_device.mountpoint),
        )
        if reply != QMessageBox.Yes:
            return
        self._delete_device_tracks(tracks)

    def _delete_device_artist(self, artist: str):
        if not self.device_db:
            return
        tracks = self._device_tracks_for_artist(artist)
        if not tracks:
            return
        reply = QMessageBox.question(
            self,
            tr("confirm_delete_device_artist_title"),
            tr(
                "confirm_delete_device_artist_text",
                artist=artist,
                count=len(tracks),
                mount=self.selected_device.mountpoint,
            ),
        )
        if reply != QMessageBox.Yes:
            return
        self._delete_device_tracks(tracks)

    # ---------- settings ----------

    def _open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.updated_settings()
            self.settings.save(self.project_root)
            apply_theme(QApplication.instance(), self.settings.theme)
