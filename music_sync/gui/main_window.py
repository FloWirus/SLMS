import logging
import shutil
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import QByteArray, QEventLoop, QObject, Qt, QThread, QTimer, Signal
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

from .. import devices as devicesmod
from .. import tags as tagsmod
from ..converter import (
    CONVERSION_TARGET_ORDER,
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
from .background import TaskContext, TaskWorker
from .icons import full_presence_icon, partial_presence_icon
from .media_info_dialog import MediaInfoDialog
from .models import COLUMN_KEYS, TrackFilterProxyModel, TrackTableModel, format_size, polish_sort_key
from .settings_dialog import SettingsDialog
from .tag_edit_dialog import TagEditDialog

# Item data role holding a tree node's lowercased label for the search box.
# Qt.UserRole itself is taken -- the tree stores each node's {"type": ...}
# descriptor there.
SEARCH_TEXT_ROLE = Qt.UserRole + 1

# How long typing has to pause before the search actually runs. Long enough
# to swallow a run of keystrokes, short enough to still feel live.
SEARCH_DEBOUNCE_MS = 150
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

    # Emitted when the user presses Cancel. The work itself runs on a worker
    # thread and only polls for this between files, so cancelling is always a
    # request, never an interruption mid-copy.
    cancel_requested = Signal()

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
        self.cancel_requested.emit()

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
        # True while a scan/sync/delete runs on a worker thread -- see
        # _begin_task.
        self._busy = False
        # Live search state, per tree widget: the text currently typed (the
        # tree has no proxy model, so the filter has to be re-applied by
        # hand every time _populate_tree rebuilds it) and the expansion
        # snapshot taken when filtering started, so clearing the box gives
        # the user back the tree they had rather than a fully expanded one.
        self._tree_filter_text: dict[QTreeWidget, str] = {}
        self._tree_prefilter_state: dict[QTreeWidget, tuple[set, set]] = {}
        # Debounce timers and the not-yet-applied query per tree, so a burst
        # of keystrokes costs one filter pass -- see _apply_search_filter.
        self._search_debounce: dict[QTreeWidget, QTimer] = {}
        self._pending_filter_text: dict[QTreeWidget, tuple[str, TrackFilterProxyModel]] = {}

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
        if self._busy:
            # Closing now would destroy the window (and the databases below)
            # out from under a worker thread still writing to a card.
            QMessageBox.information(self, tr("msg_busy_title"), tr("msg_busy_close_text"))
            event.ignore()
            return
        self._save_header_states()
        self.settings.save(self.project_root)
        # Both handles are explicitly closed rather than left to interpreter
        # shutdown: the device one lives on removable media, where an open
        # sqlite connection means unflushed data and a card that refuses to
        # unmount cleanly after the window is gone.
        for db in (self.library_db, self.device_db):
            if db is not None:
                db.close()
        self.library_db = None
        self.device_db = None
        super().closeEvent(event)

    # ---------- background work ----------

    def _run_background(self, work, progress_dialog, label_key: str):
        """Run `work(ctx)` on a worker thread while `progress_dialog` stays
        live; returns (result, cancelled).

        The wait is a nested event loop rather than a thread.wait(): the GUI
        thread has to keep processing events for the dialog to repaint, for
        Cancel to be clickable, and -- crucially -- for the conflict question
        the worker blocks on to ever get an answer. Blocking the GUI thread
        here would deadlock exactly that exchange.

        Re-entrancy is held off by _begin_task(): with the window's actions
        refusing to start while one task runs, the nested loop can't stack a
        second operation on top of this one.
        """
        context = TaskContext()
        thread = QThread(self)
        worker = TaskWorker(work, context)
        worker.moveToThread(thread)

        outcome: dict = {}
        loop = QEventLoop()

        def on_progress(index: int, total: int, name: str):
            if total:
                progress_dialog.setMaximum(total)
                progress_dialog.setValue(index)
            progress_dialog.setLabelText(tr(label_key, index=index, total=total, name=name))

        def on_conflict_raised(track: Track, target_path: str):
            context.answer_conflict(self._ask_conflict(track, Path(target_path)))

        def on_finished(result, error):
            outcome["result"] = result
            outcome["error"] = error
            loop.quit()

        context.progress_reported.connect(on_progress)
        context.conflict_raised.connect(on_conflict_raised)
        worker.finished.connect(on_finished)
        # QProgressDialog offers "canceled"; _TransferProgressDialog offers
        # its own equivalent. Either way, Cancel only sets a flag the work
        # polls between files -- nothing is torn out from under it.
        if hasattr(progress_dialog, "cancel_requested"):
            progress_dialog.cancel_requested.connect(context.cancel)
        else:
            progress_dialog.canceled.connect(context.cancel)

        thread.started.connect(worker.run)
        thread.start()
        loop.exec()
        thread.quit()
        thread.wait()

        # Read the flag *before* closing: QProgressDialog treats being closed
        # as being cancelled and emits canceled from its own closeEvent, so
        # asking afterwards reports every completed operation as cancelled.
        cancelled = context.should_stop()
        with suppress(RuntimeError):
            if hasattr(progress_dialog, "cancel_requested"):
                progress_dialog.cancel_requested.disconnect(context.cancel)
            else:
                progress_dialog.canceled.disconnect(context.cancel)
        progress_dialog.close()

        if outcome.get("error") is not None:
            raise outcome["error"]
        return outcome.get("result"), cancelled

    def _ask_conflict(self, track: Track, target_path: Path) -> ConflictResolution:
        reply = QMessageBox.question(
            self,
            tr("conflict_title"),
            tr("conflict_text", path=target_path),
            QMessageBox.Yes | QMessageBox.No,
        )
        return ConflictResolution.OVERWRITE if reply == QMessageBox.Yes else ConflictResolution.SKIP

    def _begin_task(self) -> bool:
        """Claim the single "one long operation at a time" slot.

        Everything long now runs in a nested event loop, which keeps the
        window responsive -- including responsive to another click on Sync.
        Two scans/syncs/deletes at once would have two threads writing the
        same database and the same card, so every entry point asks here
        first."""
        if self._busy:
            QMessageBox.information(self, tr("msg_busy_title"), tr("msg_busy_text"))
            return False
        self._busy = True
        return True

    def _end_task(self) -> None:
        self._busy = False

    @staticmethod
    def _clear_sort_indicator(view: QTableView) -> None:
        """Leave the table unsorted, so it shows source order (artist, album,
        disc, track -- see MusicDatabase.all_tracks). Clicking a header still
        sorts; this only removes the meaningless startup sort on column 0."""
        view.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        view.model().sort(-1)

    def _restore_header_states(self):
        self._restore_header_state(self.table_view.horizontalHeader(), self.settings.table_header_state)
        self._restore_header_state(self.device_table_view.horizontalHeader(), self.settings.device_table_header_state)
        # A header state saved by an earlier version carries that column-0
        # sort with it; restoring it would put the bug straight back.
        for view in (self.table_view, self.device_table_view):
            if view.horizontalHeader().sortIndicatorSection() == 0:
                self._clear_sort_indicator(view)
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

        # Built after both panes, since it filters them both, then inserted
        # above the splitter -- index 1 puts it directly under the toolbar.
        layout.insertWidget(1, self._build_search_bar(), 0)

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

    def _build_search_bar(self) -> QWidget:
        """The one search box, sitting above both panes and filtering them
        together. A single query for the library and the device is what you
        actually want when comparing the two sides: type an artist once and
        both panes narrow to it, so what's missing on the device is visible
        side by side instead of needing the same thing typed twice."""
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(tr("search_label")), 0)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("search_placeholder"))
        # The built-in clear button is the only affordance here: filtering is
        # live on every keystroke, so there is no button to press to search.
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        row.addWidget(self.search_edit, 1)
        return bar

    def _on_search_text_changed(self, text: str):
        """Both panes filter off the same box. Each side keeps its own proxy,
        tree and debounce timer, so an empty device pane costs nothing here
        and the two stay independent apart from sharing the query."""
        self._apply_search_filter(text, self.proxy_model, self.tree_widget)
        self._apply_search_filter(text, self.device_proxy_model, self.device_tree_widget)

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
        proxy_model = TrackFilterProxyModel()
        proxy_model.setSourceModel(model)
        # Sort by the raw value each column's data() returns under
        # Qt.UserRole (numbers as numbers, text through polish_sort_key)
        # instead of the proxy's default of comparing the formatted
        # DisplayRole strings shown in the cells.
        proxy_model.setSortRole(Qt.UserRole)
        # Matching against every column (title, artist, album, format...) is
        # handled by TrackFilterProxyModel itself, so none of Qt's own
        # filter-column/case settings apply here.
        view.setModel(proxy_model)
        view.setSortingEnabled(True)
        # setSortingEnabled immediately sorts by whatever the header's
        # default indicator points at, which is column 0 -- the "\u2713"
        # checkbox column, whose sort key is just whether a row is checked.
        # Nearly every row ties there, and Qt's sort is not stable, so tied
        # rows came back from a filter change in whatever order the algorithm
        # left them: searching "coma" then clearing stacked the Coma tracks
        # above A..., B... Dropping the indicator leaves the proxy unsorted,
        # which is what the default view actually wants -- all_tracks()
        # already returns rows ordered by artist, album, disc, track.
        self._clear_sort_indicator(view)
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
        # Ctrl/Shift multi-selection, matching what the table views already
        # do. This is plain item selection -- the per-track checkboxes are a
        # separate thing entirely (they drive syncing, not editing).
        tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
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
        self.library_db = self._open_library_db(self.source_root)
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
        self.library_db = self._open_library_db(directory)
        self._refresh_views()

    def _open_library_db(self, source_root: Path) -> MusicDatabase:
        """The index for this source directory, carrying the old shared
        database over the first time.

        The migration is deliberately narrow: the single library.db that
        earlier versions used describes whichever directory was open last, so
        it is only adopted for that one -- adopting it for a different folder
        would present another library's rows as this one's.
        """
        path = library_db_path(self.project_root, source_root)
        legacy = library_db_path(self.project_root)
        if not path.exists() and legacy.is_file() and self.settings.last_source_root:
            if Path(self.settings.last_source_root) == Path(source_root):
                path.parent.mkdir(parents=True, exist_ok=True)
                legacy.rename(path)
        return MusicDatabase(path)

    def _scan_with_progress(self, root: Path, db: MusicDatabase) -> tuple[list, bool]:
        """Scan root and reconcile db with what's actually on disk (drops
        stale rows for files removed outside the app), showing a progress
        dialog. Returns the up-to-date tracks plus whether the user cancelled.

        The scan itself (which hashes every changed file) runs on a worker
        thread -- see _run_background."""
        if not self._begin_task():
            return [], True

        progress = QProgressDialog(tr("progress_scanning_label_initial"), tr("progress_cancel"), 0, 0, self)
        progress.setWindowTitle(f"{APP_NAME} — {tr('progress_scanning_title')}")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        # Without this the dialog keeps closing itself the moment a value is
        # set, since QProgressDialog auto-closes on reaching its maximum.
        progress.setAutoClose(False)
        progress.setAutoReset(False)

        def work(ctx):
            return scan_directory(
                root,
                db,
                progress_callback=lambda index, total, path: ctx.progress(index, total, path.name),
                should_stop=ctx.should_stop,
            )

        try:
            tracks, cancelled = self._run_background(work, progress, "progress_scanning_label")
        except OSError as exc:
            logger.exception("scan failed")
            QMessageBox.critical(self, tr("msg_scan_failed_title"), str(exc))
            return [], True
        finally:
            self._end_task()
        return tracks or [], cancelled

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

    @staticmethod
    def _tree_node_key(item: QTreeWidgetItem) -> tuple | None:
        """Identity of an artist/album node that survives a rebuild. The
        QTreeWidgetItem objects themselves don't -- _populate_tree drops and
        recreates every one of them -- so expansion state has to be keyed by
        the names the node stands for instead."""
        data = item.data(0, Qt.UserRole) or {}
        if data.get("type") == "artist":
            return ("artist", data.get("artist"))
        if data.get("type") == "album":
            return ("album", data.get("artist"), data.get("album"))
        return None

    def _tree_expansion_state(self, tree_widget: QTreeWidget) -> tuple[set, set]:
        """(keys of every artist/album node present, keys of the expanded
        ones). Both halves matter on restore: a node that's missing from the
        first set is new (a rename, or a freshly scanned album) and gets the
        default state, while a known node absent from the second was
        deliberately collapsed by the user and must stay that way."""
        known: set = set()
        expanded: set = set()
        for i in range(tree_widget.topLevelItemCount()):
            artist_item = tree_widget.topLevelItem(i)
            for item in (artist_item, *(artist_item.child(j) for j in range(artist_item.childCount()))):
                key = self._tree_node_key(item)
                if key is None:
                    continue
                known.add(key)
                if item.isExpanded():
                    expanded.add(key)
        return known, expanded

    def _restore_tree_expansion(self, tree_widget: QTreeWidget, known: set, expanded: set):
        for i in range(tree_widget.topLevelItemCount()):
            artist_item = tree_widget.topLevelItem(i)
            for depth, item in (
                (0, artist_item),
                *((1, artist_item.child(j)) for j in range(artist_item.childCount())),
            ):
                key = self._tree_node_key(item)
                # Default for a node we've never seen matches what
                # expandToDepth(0) does: artists open, albums closed.
                item.setExpanded(key in expanded if key in known else depth == 0)

    def _populate_tree(
        self, tree_widget: QTreeWidget, tracks: list[Track], other_hashes: set[str], checked_hashes: set[str]
    ):
        # Saving tags rebuilds the tree from scratch; without carrying the
        # user's collapsed artists/albums (and scroll position) across that
        # rebuild, every save snaps the view back to the default layout and
        # loses their place in a large library.
        known_nodes, expanded_nodes = self._tree_expansion_state(tree_widget)
        had_content = tree_widget.topLevelItemCount() > 0
        scroll_position = tree_widget.verticalScrollBar().value()

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
                artist_item.setData(0, SEARCH_TEXT_ROLE, artist.lower())
                artist_item.setFlags(artist_item.flags() | Qt.ItemIsUserCheckable)
                artist_present_flags = []
                artist_checked_flags = []
                for album in sorted(by_artist[artist], key=polish_sort_key):
                    album_item = QTreeWidgetItem([album])
                    album_item.setData(0, Qt.UserRole, {"type": "album", "artist": artist, "album": album})
                    album_item.setData(0, SEARCH_TEXT_ROLE, album.lower())
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
                        track_item.setData(0, SEARCH_TEXT_ROLE, label.lower())
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

            if had_content:
                self._restore_tree_expansion(tree_widget, known_nodes, expanded_nodes)
            else:
                tree_widget.expandToDepth(0)
        finally:
            self._updating_checks = False

        if had_content:
            tree_widget.verticalScrollBar().setValue(scroll_position)

        # The rebuild dropped every item, hidden flags included, so an active
        # search has to be re-applied or a save would silently un-filter the
        # tree while the search box still shows the query.
        filter_text = self._tree_filter_text.get(tree_widget, "")
        if filter_text:
            self._filter_tree(tree_widget, filter_text)

    def _apply_search_filter(self, text: str, proxy_model: TrackFilterProxyModel, tree_widget: QTreeWidget):
        """Queue a filter of a pane's table and tree down to tracks matching
        `text`. Wired to textChanged -- no Enter, no button.

        Debounced rather than run inline: re-filtering re-sorts the whole
        table and walks the whole tree, and doing that once per keystroke
        meant a fast typist queued up a run per character and the box lagged
        behind their typing. Restarting the timer collapses a burst of
        keystrokes into the single filter pass that matters -- the one for
        the text they stopped on."""
        timer = self._search_debounce.get(tree_widget)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(SEARCH_DEBOUNCE_MS)
            timer.timeout.connect(lambda tw=tree_widget: self._run_search_filter(tw))
            self._search_debounce[tree_widget] = timer
        self._pending_filter_text[tree_widget] = (text, proxy_model)
        timer.start()

    def _run_search_filter(self, tree_widget: QTreeWidget):
        pending = self._pending_filter_text.pop(tree_widget, None)
        if pending is None:
            return
        text, proxy_model = pending
        proxy_model.set_filter_text(text)
        previous = self._tree_filter_text.get(tree_widget, "")
        self._tree_filter_text[tree_widget] = text
        self._filter_tree(tree_widget, text, previous)

    def _filter_tree(self, tree_widget: QTreeWidget, text: str, previous: str = ""):
        """Hide every tree node that doesn't match `text`.

        `previous` is the query this one replaces. When the new query merely
        extends it ("bea" -> "beat"), a node that was already hidden can't
        come back -- a longer needle matches a subset of what the shorter one
        did -- so whole hidden subtrees are skipped instead of re-tested.
        That turns the common case (typing another character) into work
        proportional to what's still on screen rather than to the library."""
        needle = text.strip().lower()
        if not needle:
            for artist_item, album_item, track_item in self._tree_nodes(tree_widget):
                (track_item or album_item or artist_item).setHidden(False)
            saved = self._tree_prefilter_state.pop(tree_widget, None)
            if saved:
                self._restore_tree_expansion(tree_widget, *saved)
            return

        # Snapshot the pre-filter shape once, on the keystroke that starts a
        # search -- matches get their parents force-expanded below, which
        # would otherwise overwrite what the user had collapsed.
        if tree_widget not in self._tree_prefilter_state:
            self._tree_prefilter_state[tree_widget] = self._tree_expansion_state(tree_widget)

        narrowing = bool(previous) and needle.startswith(previous.strip().lower())

        # One repaint at the end instead of one per setHidden/setExpanded:
        # without this the view relayouts thousands of times mid-walk.
        tree_widget.setUpdatesEnabled(False)
        try:
            for i in range(tree_widget.topLevelItemCount()):
                artist_item = tree_widget.topLevelItem(i)
                if narrowing and artist_item.isHidden():
                    continue
                artist_hit = needle in self._search_text(artist_item)
                artist_visible = False
                for j in range(artist_item.childCount()):
                    album_item = artist_item.child(j)
                    if narrowing and album_item.isHidden():
                        continue
                    album_hit = artist_hit or needle in self._search_text(album_item)
                    album_visible = False
                    for k in range(album_item.childCount()):
                        track_item = album_item.child(k)
                        if narrowing and track_item.isHidden():
                            continue
                        # A hit on the artist or album shows everything under
                        # it: searching for a band means wanting its records,
                        # not only the tracks whose titles repeat the band's
                        # name.
                        visible = album_hit or needle in self._search_text(track_item)
                        track_item.setHidden(not visible)
                        album_visible = album_visible or visible
                    album_item.setHidden(not album_visible)
                    album_item.setExpanded(album_visible)
                    artist_visible = artist_visible or album_visible
                artist_item.setHidden(not artist_visible)
                artist_item.setExpanded(artist_visible)
        finally:
            tree_widget.setUpdatesEnabled(True)

    @staticmethod
    def _search_text(item: QTreeWidgetItem) -> str:
        """The item's label, lowercased. _populate_tree stashes this on every
        node it builds, because re-lowercasing every label on every keystroke
        was a large share of the filter's cost on a big tree. Computed on the
        spot for any node that somehow lacks it -- writing it back here would
        emit itemChanged in the middle of the filter walk, which the check
        propagation in _on_tree_item_changed would pick up."""
        cached = item.data(0, SEARCH_TEXT_ROLE)
        return item.text(0).lower() if cached is None else cached

    @staticmethod
    def _tree_nodes(tree_widget: QTreeWidget):
        """Every (artist, album, track) node in the tree, one row per node --
        the two shallower entries are None on deeper rows, so callers can
        pick out the level they care about."""
        for i in range(tree_widget.topLevelItemCount()):
            artist_item = tree_widget.topLevelItem(i)
            yield artist_item, None, None
            for j in range(artist_item.childCount()):
                album_item = artist_item.child(j)
                yield artist_item, album_item, None
                for k in range(album_item.childCount()):
                    yield artist_item, album_item, album_item.child(k)

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

    @staticmethod
    def _tracks_of_artist(tracks: list[Track], artist: str) -> list[Track]:
        return [t for t in tracks if (t.artist or tr("unknown_artist")) == artist]

    @staticmethod
    def _tracks_of_album(tracks: list[Track], artist: str, album: str) -> list[Track]:
        return [
            t
            for t in tracks
            if (t.artist or tr("unknown_artist")) == artist and (t.album or tr("unknown_album")) == album
        ]

    def _tracks_for_artist(self, artist: str) -> list[Track]:
        return self._tracks_of_artist(self.library_db.all_tracks(), artist)

    def _tracks_for_album(self, artist: str, album: str) -> list[Track]:
        return self._tracks_of_album(self.library_db.all_tracks(), artist, album)

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

        if not self._confirm_free_space(tracks):
            return

        reply = QMessageBox.question(
            self,
            tr("confirm_sync_title"),
            tr("confirm_sync_text", description=description, mount=self.selected_device.mountpoint),
        )
        if reply != QMessageBox.Yes:
            return

        mountpoint = Path(self.selected_device.mountpoint)
        source_root = self.source_root
        dir_template = self.settings.dir_template
        filename_template = self.settings.filename_template
        track_no_fix = self.settings.track_no_fix
        force = self.force_checkbox.isChecked()

        def work(ctx):
            # Everything this closure touches was read off the widgets above,
            # on the GUI thread: a worker must never reach back into a
            # checkbox or a combo box to find out what it is doing.
            return sync_to_device(
                source_root,
                tracks,
                mountpoint,
                dir_template,
                filename_template,
                on_conflict=ctx.ask_conflict,
                on_progress=lambda index, total, track: ctx.progress(index, total, track.filename),
                conversion=conversion,
                cover_resize=cover_resize,
                track_no_fix=track_no_fix,
                on_scan_progress=lambda index, total, path: ctx.progress(index, total, path.name),
                force=force,
                should_stop=ctx.should_stop,
            )

        result = self._run_transfer(work, len(tracks))
        if result is None:
            return

        self.source_checked_hashes.clear()
        self.device_checked_hashes.clear()
        # sync_to_device() reconciled the device database and registered every
        # file it wrote, so it is already current -- reopen it to pick that
        # up, but don't scan the whole card a second time.
        self._on_device_selected(self.device_combo.currentIndex(), rescan=False)

        self._show_sync_result(result)

    def _confirm_free_space(self, tracks: list[Track]) -> bool:
        """Check up front that the tracks about to be copied plausibly fit on
        the device, instead of discovering it one ENOSPC at a time half-way
        through a sync and handing back a list of a thousand identical errors.

        An estimate on purpose, hence a warning the user can override rather
        than a hard stop: tracks already on the device won't be copied again,
        and conversion usually makes files smaller -- so this over-counts,
        and being wrong in that direction must not block a sync that would
        actually have fit."""
        needed = sum(track.size for track in tracks)
        try:
            free = shutil.disk_usage(self.selected_device.mountpoint).free
        except OSError:
            return True
        if needed <= free:
            return True
        reply = QMessageBox.warning(
            self,
            tr("msg_not_enough_space_title"),
            tr("msg_not_enough_space_text", needed=format_size(needed), free=format_size(free)),
            QMessageBox.Yes | QMessageBox.No,
        )
        return reply == QMessageBox.Yes

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

        mountpoint = Path(self.selected_device.mountpoint)
        source_root = self.source_root
        library_db = self.library_db
        dir_template = self.settings.dir_template
        filename_template = self.settings.filename_template

        def work(ctx):
            return sync_from_device(
                mountpoint,
                tracks,
                source_root,
                library_db,
                dir_template,
                filename_template,
                on_conflict=ctx.ask_conflict,
                on_progress=lambda index, total, track: ctx.progress(index, total, track.filename),
                on_scan_progress=lambda index, total, path: ctx.progress(index, total, path.name),
                should_stop=ctx.should_stop,
            )

        result = self._run_transfer(work, len(tracks))
        if result is None:
            return

        self.source_checked_hashes.clear()
        self.device_checked_hashes.clear()
        self._refresh_views()

        self._show_sync_result(result)

    def _run_transfer(self, work, track_count: int) -> SyncResult | None:
        """Put a transfer on a worker thread behind the progress dialog, in
        the one shape both directions want. Returns None when the transfer
        never ran (another operation holds the slot) or failed outright,
        which is also when the caller must not go on to refresh anything."""
        if not self._begin_task():
            return None

        progress = _TransferProgressDialog(
            tr("progress_sync_label_initial"), tr("progress_cancel"), 0, track_count, self
        )
        progress.setWindowTitle(f"{APP_NAME} — {tr('progress_sync_title')}")
        progress.setWindowModality(Qt.WindowModal)
        progress.show()

        try:
            result, _cancelled = self._run_background(work, progress, "progress_sync_label")
            return result
        except Exception as exc:
            logger.exception("transfer failed")
            QMessageBox.critical(self, tr("msg_sync_failed_title"), str(exc))
            return None
        finally:
            self._end_task()

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

    @staticmethod
    def _ordered_tracks(proxy_model, table_model) -> list[Track]:
        """The table's tracks in the order the view currently shows them
        (i.e. after sorting and filtering), which is the order Prev/Next in
        the tag dialog then walks."""
        tracks = []
        for row in range(proxy_model.rowCount()):
            source_row = proxy_model.mapToSource(proxy_model.index(row, 0)).row()
            tracks.append(table_model.track_at(source_row))
        return tracks

    def _flatten_tree_tracks(self, tree_widget: QTreeWidget) -> list[Track]:
        """Every track in a tree, top to bottom."""
        return [
            (track_item.data(0, Qt.UserRole) or {})["track"]
            for _artist, _album, track_item in self._tree_nodes(tree_widget)
            if track_item is not None and (track_item.data(0, Qt.UserRole) or {}).get("type") == "track"
        ]

    def _flatten_tree_albums(self, tree_widget: QTreeWidget) -> list[list[Track]]:
        """Every album in a tree as its own list of tracks, in tree order --
        what the album editor pages through with Prev/Next album."""
        albums: list[list[Track]] = []
        for i in range(tree_widget.topLevelItemCount()):
            artist_item = tree_widget.topLevelItem(i)
            for j in range(artist_item.childCount()):
                album_item = artist_item.child(j)
                tracks = [
                    (album_item.child(k).data(0, Qt.UserRole) or {})["track"]
                    for k in range(album_item.childCount())
                    if (album_item.child(k).data(0, Qt.UserRole) or {}).get("type") == "track"
                ]
                if tracks:
                    albums.append(tracks)
        return albums

    @staticmethod
    def _album_start_index(albums: list[list[Track]], artist: str, album: str) -> int:
        """Which album in the list the editor should open on -- the one the
        user right-clicked. Matched on the same "unknown" fallbacks the tree
        labels use, so a track with no artist tag still finds its node."""
        for index, tracks in enumerate(albums):
            first = tracks[0]
            if (first.artist or tr("unknown_artist")) == artist and (first.album or tr("unknown_album")) == album:
                return index
        return 0

    def _selected_tree_tracks(self, tree_widget: QTreeWidget) -> list[Track]:
        """Tracks covered by the current selection, in the order they appear
        in the tree. A selected album or artist node stands for everything
        beneath it, so selecting an album plus one of its tracks yields that
        album once rather than a duplicated track. Hidden nodes are skipped:
        a selection made before typing in the search box must not drag in
        rows the user can no longer see."""
        selected: list[Track] = []
        seen: set[str] = set()
        for artist_item, album_item, track_item in self._tree_nodes(tree_widget):
            if track_item is None or track_item.isHidden() or album_item.isHidden() or artist_item.isHidden():
                continue
            if not (track_item.isSelected() or album_item.isSelected() or artist_item.isSelected()):
                continue
            track = (track_item.data(0, Qt.UserRole) or {}).get("track")
            if track is not None and track.path not in seen:
                seen.add(track.path)
                selected.append(track)
        return selected

    @staticmethod
    def _selected_table_tracks(view: QTableView, proxy_model, table_model) -> list[Track]:
        rows = sorted(view.selectionModel().selectedRows(), key=lambda index: index.row())
        return [table_model.track_at(proxy_model.mapToSource(index).row()) for index in rows]

    def _open_dialog(self, dialog_class, root: Path, items: list, start_index: int, on_saved) -> None:
        """Open one of the two tag editors and refresh the views afterwards.

        Both editors take the same arguments and are used from both panes;
        the only thing that differs between the four combinations is which
        root the files live under and which persist callback writes the
        result back to the right database."""
        dialog = dialog_class(root, items, start_index, on_saved=on_saved, settings=self.settings, parent=self)
        dialog.exec()
        self._refresh_views()

    def _edit_tracks_as_album(self, tracks: list[Track]):
        """Edit a loose multi-selection through the album dialog: it offers
        exactly the fields that make sense to apply to several tracks at
        once (artist, album, year, genre, cover), and none of the per-track
        ones (title, track number) that would overwrite each file with the
        same value."""
        if not self.source_root or not self.library_db or not tracks:
            return
        self._open_dialog(AlbumEditDialog, self.source_root, [list(tracks)], 0, self._persist_track_edit)

    def _edit_device_tracks_as_album(self, tracks: list[Track]):
        if not self.selected_device or not self.device_db or not tracks:
            return
        self._open_dialog(
            AlbumEditDialog,
            Path(self.selected_device.mountpoint),
            [list(tracks)],
            0,
            self._persist_device_track_edit,
        )

    def _edit_selected_table_track(self, proxy_index):
        tracks = self._ordered_tracks(self.proxy_model, self.table_model)
        self._edit_track_sequence(tracks, proxy_index.row())

    def _edit_tree_item(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.UserRole) or {}
        if data.get("type") != "track":
            return
        tracks = self._flatten_tree_tracks(self.tree_widget)
        index = tracks.index(data["track"]) if data["track"] in tracks else 0
        self._edit_track_sequence(tracks, index)

    def _edit_track_sequence(self, tracks: list[Track], start_index: int):
        if not self.source_root or not self.library_db or not tracks:
            return
        self._open_dialog(TagEditDialog, self.source_root, tracks, start_index, self._persist_track_edit)

    @staticmethod
    def _persist_edit(db: MusicDatabase, root: Path, old_track: Track, fields: dict, source_hash: str | None) -> Track:
        """Write a tag edit back to one of the databases: re-read the file's
        hash/size/mtime (the edit changed them), carry the new field values
        over, and move the row if the file was renamed.

        `source_hash` is what distinguishes the two sides. A library row is
        its own source, so it takes the file's new hash; a device row keeps
        pointing at whatever library track it was copied from, which editing
        the copy on the card does not change."""
        new_path = root / fields["path"]
        new_hash = hash_file(new_path)
        stat = new_path.stat()
        updated = Track(
            id=old_track.id,
            path=fields["path"],
            filename=new_path.name,
            hash=new_hash,
            source_hash=new_hash if source_hash is None else source_hash,
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
            db.delete_by_path(old_track.path)
        db.upsert_track(updated)
        return updated

    def _persist_track_edit(self, old_track: Track, fields: dict) -> Track:
        updated = self._persist_edit(self.library_db, self.source_root, old_track, fields, source_hash=None)
        # Editing tags rewrites the file, so its hash changes -- and copies
        # already sitting on the connected device are registered against the
        # old one. Move that registration over, or the track would show up as
        # missing from the device and get copied a second time. Only possible
        # for the device that happens to be plugged in right now; one edited
        # while another card was connected still loses the link until that
        # card is re-synced.
        if updated.hash != old_track.hash and self.device_db is not None:
            if self.device_db.reassign_source_hash(old_track.hash, updated.hash):
                self.device_hashes = self.device_db.source_hashes()
        return updated

    def _edit_selected_device_table_track(self, proxy_index):
        tracks = self._ordered_tracks(self.device_proxy_model, self.device_table_model)
        self._edit_device_track_sequence(tracks, proxy_index.row())

    def _edit_device_tree_item(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.UserRole) or {}
        if data.get("type") != "track":
            return
        tracks = self._flatten_tree_tracks(self.device_tree_widget)
        index = tracks.index(data["track"]) if data["track"] in tracks else 0
        self._edit_device_track_sequence(tracks, index)

    def _edit_device_track_sequence(self, tracks: list[Track], start_index: int):
        if not self.selected_device or not self.device_db or not tracks:
            return
        self._open_dialog(
            TagEditDialog,
            Path(self.selected_device.mountpoint),
            tracks,
            start_index,
            self._persist_device_track_edit,
        )

    def _edit_device_album_tags(self, artist: str, album: str):
        if not self.selected_device or not self.device_db:
            return
        albums = self._flatten_tree_albums(self.device_tree_widget)
        if not albums:
            return
        self._open_dialog(
            AlbumEditDialog,
            Path(self.selected_device.mountpoint),
            albums,
            self._album_start_index(albums, artist, album),
            self._persist_device_track_edit,
        )

    def _persist_device_track_edit(self, old_track: Track, fields: dict) -> Track:
        return self._persist_edit(
            self.device_db,
            Path(self.selected_device.mountpoint),
            old_track,
            fields,
            source_hash=old_track.source_hash,
        )

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
        tracks = self._ordered_tracks(self.proxy_model, self.table_model)
        track = tracks[index.row()]
        self._show_track_menu(
            track,
            tracks,
            index.row(),
            self.table_view.viewport().mapToGlobal(pos),
            self._selected_table_tracks(self.table_view, self.proxy_model, self.table_model),
        )

    def _tree_context_menu(self, pos):
        item = self.tree_widget.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.UserRole) or {}
        item_type = data.get("type")
        global_pos = self.tree_widget.viewport().mapToGlobal(pos)

        if item_type == "track":
            tracks = self._flatten_tree_tracks(self.tree_widget)
            track = data["track"]
            index = tracks.index(track) if track in tracks else 0
            self._show_track_menu(track, tracks, index, global_pos, self._selected_tree_tracks(self.tree_widget))
        elif item_type == "album":
            self._show_album_menu(global_pos, data["artist"], data["album"])
        elif item_type == "artist":
            self._show_artist_menu(global_pos, data["artist"])

    def _device_table_context_menu(self, pos):
        index = self.device_table_view.indexAt(pos)
        if not index.isValid():
            return
        tracks = self._ordered_tracks(self.device_proxy_model, self.device_table_model)
        source_row = self.device_proxy_model.mapToSource(self.device_proxy_model.index(index.row(), 0)).row()
        track = self.device_table_model.track_at(source_row)
        self._show_device_track_menu(
            track,
            tracks,
            index.row(),
            self.device_table_view.viewport().mapToGlobal(pos),
            self._selected_table_tracks(self.device_table_view, self.device_proxy_model, self.device_table_model),
        )

    def _device_tree_context_menu(self, pos):
        item = self.device_tree_widget.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.UserRole) or {}
        item_type = data.get("type")
        global_pos = self.device_tree_widget.viewport().mapToGlobal(pos)

        if item_type == "track":
            tracks = self._flatten_tree_tracks(self.device_tree_widget)
            track = data["track"]
            index = tracks.index(track) if track in tracks else 0
            self._show_device_track_menu(
                track, tracks, index, global_pos, self._selected_tree_tracks(self.device_tree_widget)
            )
        elif item_type == "album":
            self._show_device_album_menu(global_pos, data["artist"], data["album"])
        elif item_type == "artist":
            self._show_device_artist_menu(global_pos, data["artist"])

    def _show_device_track_menu(self, device_track: Track, tracks: list[Track], index: int, global_pos, selected: list[Track] | None = None):
        menu = QMenu(self)
        multi_edit_action = None
        if selected and len(selected) > 1 and any(t.path == device_track.path for t in selected):
            multi_edit_action = menu.addAction(tr("menu_edit_selected_tags", count=len(selected)))
        edit_action = menu.addAction(tr("menu_edit_tags"))
        media_info_action = menu.addAction(tr("menu_media_info"))
        delete_action = menu.addAction(tr("menu_delete_from_device"))
        action = menu.exec(global_pos)
        if multi_edit_action and action == multi_edit_action:
            self._edit_device_tracks_as_album(selected)
        elif action == edit_action:
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

    def _show_track_menu(self, track: Track, tracks: list[Track], index: int, global_pos, selected: list[Track] | None = None):
        menu = QMenu(self)
        # Offered only when the right-clicked track is itself part of a
        # multi-track selection -- right-clicking outside the selection is a
        # gesture about that one row, not about what's highlighted elsewhere.
        multi_edit_action = None
        if selected and len(selected) > 1 and any(t.path == track.path for t in selected):
            multi_edit_action = menu.addAction(tr("menu_edit_selected_tags", count=len(selected)))
        edit_action = menu.addAction(tr("menu_edit_tags"))
        media_info_action = menu.addAction(tr("menu_media_info"))
        sync_action = menu.addAction(tr("menu_sync_track"))
        delete_device_action = None
        if self.selected_device and track.source_hash in self.device_hashes:
            delete_device_action = menu.addAction(tr("menu_delete_from_device"))
        delete_action = menu.addAction(tr("menu_delete_track"))

        action = menu.exec(global_pos)
        if multi_edit_action and action == multi_edit_action:
            self._edit_tracks_as_album(selected)
        elif action == edit_action:
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

    def _edit_album_tags(self, artist: str, album: str):
        if not self.source_root or not self.library_db:
            return
        albums = self._flatten_tree_albums(self.tree_widget)
        if not albums:
            return
        self._open_dialog(
            AlbumEditDialog,
            self.source_root,
            albums,
            self._album_start_index(albums, artist, album),
            self._persist_track_edit,
        )

    def _delete_from_device(self, track: Track):
        if not self.selected_device or not self.device_db:
            return
        device_track = self.device_db.get_by_source_hash(track.hash)
        if not device_track:
            return
        self._delete_device_track(device_track)

    # ---------- deletion ----------

    def _device_tracks_for_artist(self, artist: str) -> list[Track]:
        return self._tracks_of_artist(self.device_db.all_tracks(), artist)

    def _device_tracks_for_album(self, artist: str, album: str) -> list[Track]:
        return self._tracks_of_album(self.device_db.all_tracks(), artist, album)

    def _delete_library_tracks(self, tracks: list[Track]):
        if not self.source_root or not self.library_db or not tracks:
            return
        errors: list[str] = []
        for track in tracks:
            file_path = self.source_root / track.path
            try:
                if file_path.exists():
                    file_path.unlink()
                    remove_empty_parent_dirs(file_path.parent, self.source_root)
            except OSError as exc:
                # A read-only file or a permission problem must not abort the
                # rest of a multi-track delete, and its row has to stay --
                # the file is still there.
                logger.error(tr("log_delete_failed", path=track.path, error=exc))
                errors.append(f"{track.path}: {exc}")
                continue
            self.library_db.delete_by_path(track.path)
        self._refresh_views()
        if errors:
            QMessageBox.critical(self, tr("msg_delete_failed_title"), "\n".join(errors[:10]))

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

        if not self._begin_task():
            return

        progress = QProgressDialog(tr("progress_deleting_label_initial"), None, 0, len(tracks), self)
        progress.setWindowTitle(f"{APP_NAME} — {tr('progress_deleting_title')}")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        mountpoint = Path(self.selected_device.mountpoint)

        def work(ctx):
            delete_many_from_device(
                mountpoint,
                tracks,
                on_progress=lambda index, total, track: ctx.progress(index, total, track.filename),
            )

        try:
            self._run_background(work, progress, "progress_deleting_label")
        except OSError as exc:
            logger.error(tr("log_delete_failed", path=mountpoint, error=exc))
            QMessageBox.critical(self, tr("msg_delete_failed_title"), str(exc))
        finally:
            self._end_task()

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
