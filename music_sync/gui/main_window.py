from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QStyle,
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
from ..db import MusicDatabase, Track, device_db_path, library_db_path
from ..i18n import set_language, tr
from ..scanner import hash_file, scan_directory
from ..settings import Settings
from ..sync import ConflictResolution, delete_from_device, sync_to_device
from .album_edit_dialog import AlbumEditDialog
from .models import TrackTableModel
from .settings_dialog import SettingsDialog
from .tag_edit_dialog import TagEditDialog
from .theme import apply_theme

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
        self.device_hashes: set[str] = set()

        self.setWindowTitle(APP_NAME)
        self.resize(1000, 650)

        self._build_ui()
        self._refresh_devices()
        self._restore_last_source()

    # ---------- UI construction ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.addLayout(self._build_toolbar())

        self.tabs = QTabWidget()
        self.table_view = self._build_table_view()
        self.tree_widget = self._build_tree_widget()
        self.tabs.addTab(self.table_view, tr("tab_table"))
        self.tabs.addTab(self.tree_widget, tr("tab_tree"))
        layout.addWidget(self.tabs)

        self.status_label = QLabel(tr("status_choose_directory"))
        layout.addWidget(self.status_label)

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()

        choose_dir_btn = QPushButton(tr("btn_choose_dir"))
        choose_dir_btn.clicked.connect(self._choose_source_directory)
        row.addWidget(choose_dir_btn)

        rescan_btn = QPushButton(tr("btn_rescan"))
        rescan_btn.clicked.connect(self._rescan_source)
        row.addWidget(rescan_btn)

        row.addSpacing(20)
        row.addWidget(QLabel(tr("label_target_device")))

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(280)
        self.device_combo.currentIndexChanged.connect(self._on_device_selected)
        row.addWidget(self.device_combo)

        refresh_devices_btn = QPushButton(tr("btn_refresh_devices"))
        refresh_devices_btn.clicked.connect(self._refresh_devices)
        row.addWidget(refresh_devices_btn)

        sync_btn = QPushButton(tr("btn_sync"))
        sync_btn.clicked.connect(self._run_sync)
        row.addWidget(sync_btn)

        eject_btn = QPushButton(tr("btn_eject"))
        eject_btn.clicked.connect(self._eject_device)
        row.addWidget(eject_btn)

        row.addStretch()

        settings_btn = QPushButton(tr("btn_settings"))
        settings_btn.clicked.connect(self._open_settings)
        row.addWidget(settings_btn)

        return row

    def _build_table_view(self) -> QTableView:
        view = QTableView()
        self.table_model = TrackTableModel()
        self.proxy_model = QSortFilterProxyModel()
        self.proxy_model.setSourceModel(self.table_model)
        view.setModel(self.proxy_model)
        view.setSortingEnabled(True)
        view.setSelectionBehavior(QAbstractItemView.SelectRows)
        view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        view.setContextMenuPolicy(Qt.CustomContextMenu)
        view.customContextMenuRequested.connect(self._table_context_menu)
        view.doubleClicked.connect(self._edit_selected_table_track)
        return view

    def _build_tree_widget(self) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setHeaderLabels([tr("tree_header_name"), tr("tree_header_year"), tr("tree_header_format")])
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._tree_context_menu)
        tree.itemDoubleClicked.connect(self._edit_tree_item)
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
        count = len(self.library_db.all_tracks())
        self.status_label.setText(tr("status_loaded_library", count=count, path=self.source_root))

    def _rescan_source(self):
        if not self.source_root or not self.library_db:
            QMessageBox.information(self, tr("msg_no_directory_title"), tr("msg_no_directory_text"))
            return

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
            self.source_root,
            self.library_db,
            progress_callback=on_progress,
            should_stop=progress.wasCanceled,
        )
        cancelled = progress.wasCanceled()
        progress.close()

        self._refresh_views()
        if cancelled:
            self.status_label.setText(tr("status_scan_cancelled", count=len(tracks)))
        else:
            self.status_label.setText(tr("status_scan_done", count=len(tracks), path=self.source_root))

    def _refresh_views(self):
        if not self.library_db:
            return
        tracks = self.library_db.all_tracks()
        self.table_model.set_tracks(tracks)
        self.table_model.set_device_hashes(self.device_hashes)
        self._populate_tree(tracks)

    def _populate_tree(self, tracks: list[Track]):
        self.tree_widget.clear()
        by_artist: dict[str, dict[str, list[Track]]] = {}
        for track in tracks:
            artist = track.artist or tr("unknown_artist")
            album = track.album or tr("unknown_album")
            by_artist.setdefault(artist, {}).setdefault(album, []).append(track)

        on_device_icon = self._on_device_icon()

        for artist in sorted(by_artist):
            artist_item = QTreeWidgetItem([artist])
            artist_item.setData(0, Qt.UserRole, {"type": "artist", "artist": artist})
            for album in sorted(by_artist[artist]):
                album_item = QTreeWidgetItem([album])
                album_item.setData(0, Qt.UserRole, {"type": "album", "artist": artist, "album": album})
                for track in by_artist[artist][album]:
                    label = f"{track.track_number}. {track.title}" if track.track_number else track.title
                    track_item = QTreeWidgetItem([label, track.year, track.format])
                    track_item.setData(0, Qt.UserRole, {"type": "track", "track": track})
                    if track.hash in self.device_hashes:
                        track_item.setIcon(0, on_device_icon)
                    album_item.addChild(track_item)
                artist_item.addChild(album_item)
            self.tree_widget.addTopLevelItem(artist_item)

        self.tree_widget.expandToDepth(0)

    def _on_device_icon(self) -> QIcon:
        style = QApplication.instance().style()
        return style.standardIcon(QStyle.SP_DialogApplyButton)

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
        self._on_device_selected(restore_index)

    def _on_device_selected(self, index: int):
        self.selected_device = self.device_combo.itemData(index)
        if self.selected_device:
            device_db = MusicDatabase(device_db_path(Path(self.selected_device.mountpoint)))
            self.device_hashes = device_db.hashes()
            device_db.close()
        else:
            self.device_hashes = set()
        self._refresh_views()

    def _eject_device(self):
        if not self.selected_device:
            QMessageBox.information(self, tr("msg_no_device_title"), tr("msg_no_device_text"))
            return

        device = self.selected_device
        success, error = devicesmod.eject_device(device)
        if success:
            QMessageBox.information(self, tr("msg_eject_success_title"), tr("msg_eject_success_text"))
        else:
            QMessageBox.critical(self, tr("msg_eject_failed_title"), tr("msg_eject_failed_text", error=error))
        self._refresh_devices()

    # ---------- sync ----------

    def _run_sync(self):
        if not self.library_db:
            QMessageBox.information(self, tr("msg_no_library_title"), tr("msg_no_library_text"))
            return
        self._sync_tracks(self.library_db.all_tracks(), tr("sync_whole_library"))

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

        reply = QMessageBox.question(
            self,
            tr("confirm_sync_title"),
            tr("confirm_sync_text", description=description, mount=self.selected_device.mountpoint),
        )
        if reply != QMessageBox.Yes:
            return

        progress = QProgressDialog(tr("progress_sync_label_initial"), tr("progress_cancel"), 0, len(tracks), self)
        progress.setWindowTitle(f"{APP_NAME} — {tr('progress_sync_title')}")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

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
        )
        progress.close()

        self._on_device_selected(self.device_combo.currentIndex())

        message = (
            f"{tr('sync_result_copied')}: {result.copied}\n"
            f"{tr('sync_result_present')}: {result.already_present}\n"
            f"{tr('sync_result_skipped')}: {result.skipped}\n"
            f"{tr('sync_result_errors')}: {len(result.errors)}"
        )
        if result.errors:
            message += "\n\n" + "\n".join(result.errors[:10])
        QMessageBox.information(self, tr("sync_done_title"), message)

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
        dialog = TagEditDialog(self.source_root, tracks, start_index, on_saved=self._persist_track_edit, parent=self)
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

    # ---------- context menus ----------

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
            self._show_group_menu(
                global_pos,
                label=tr("menu_sync_artist", artist=data["artist"]),
                tracks_provider=lambda: self._tracks_for_artist(data["artist"]),
                description=tr("sync_artist_description", artist=data["artist"]),
            )

    def _show_track_menu(self, track: Track, tracks: list[Track], index: int, global_pos):
        menu = QMenu(self)
        edit_action = menu.addAction(tr("menu_edit_tags"))
        sync_action = menu.addAction(tr("menu_sync_track"))
        delete_action = None
        if self.selected_device and track.hash in self.device_hashes:
            delete_action = menu.addAction(tr("menu_delete_from_device"))

        action = menu.exec(global_pos)
        if action == edit_action:
            self._edit_track_sequence(tracks, index)
        elif action == sync_action:
            self._sync_tracks([track], tr("sync_track_description", title=track.title))
        elif delete_action and action == delete_action:
            self._delete_from_device(track)

    def _show_group_menu(self, global_pos, label: str, tracks_provider, description: str):
        menu = QMenu(self)
        sync_action = menu.addAction(label)
        action = menu.exec(global_pos)
        if action == sync_action:
            self._sync_tracks(tracks_provider(), description)

    def _show_album_menu(self, global_pos, artist: str, album: str):
        menu = QMenu(self)
        edit_action = menu.addAction(tr("menu_edit_album_tags", album=album))
        sync_action = menu.addAction(tr("menu_sync_album", album=album))
        action = menu.exec(global_pos)
        if action == edit_action:
            self._edit_album_tags(artist, album)
        elif action == sync_action:
            self._sync_tracks(self._tracks_for_album(artist, album), tr("sync_album_description", album=album))

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
        dialog = AlbumEditDialog(self.source_root, albums, start_index, on_saved=self._persist_track_edit, parent=self)
        dialog.exec()
        self._refresh_views()

    def _delete_from_device(self, track: Track):
        if not self.selected_device:
            return
        device_db = MusicDatabase(device_db_path(Path(self.selected_device.mountpoint)))
        device_track = device_db.get_by_hash(track.hash)
        device_db.close()
        if not device_track:
            return

        reply = QMessageBox.question(
            self,
            tr("confirm_delete_device_title"),
            tr("confirm_delete_device_text", title=track.title, mount=self.selected_device.mountpoint),
        )
        if reply != QMessageBox.Yes:
            return

        delete_from_device(Path(self.selected_device.mountpoint), device_track)
        self._on_device_selected(self.device_combo.currentIndex())

    # ---------- settings ----------

    def _open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.updated_settings()
            self.settings.save(self.project_root)
            apply_theme(QApplication.instance(), self.settings.theme)
