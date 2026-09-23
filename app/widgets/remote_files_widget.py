import datetime
import posixpath

from PySide6.QtCore import QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QToolButton,
    QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout, QWidget,
)

from app.core.async_task import run_async
from app.core.session import RemoteSession
from app.i18n import byte_units, tr
from app.widgets.remote_file_transfer_mixin import RemoteFileTransferMixin
from app.widgets.remote_new_item_mixin import RemoteNewItemMixin
from app.widgets.remote_tree import RemoteTree
from app.widgets.transfer_queue import TransferQueueWidget
from app.storage import Storage

PATH_ROLE = Qt.UserRole
DIR_ROLE = Qt.UserRole + 1
LOADED_ROLE = Qt.UserRole + 2
SUMMARY_ROLE = Qt.UserRole + 3
DETAIL_ROLE = Qt.UserRole + 4
NAME_ROLE = Qt.UserRole + 5
PARENT_ROLE = Qt.UserRole + 6
RIGHT_COLORS = {"RW": "#55D187", "RWX": "#55D1C8", "R": "#F2C866", "RX": "#7EC8FF", "W": "#FF9F55", "X": "#C792EA", "🔒": "#FF6666"}


class RemoteFilesWidget(RemoteNewItemMixin, RemoteFileTransferMixin, QWidget):
    PATH_ROLE = PATH_ROLE
    DIR_ROLE = DIR_ROLE
    directory_activated = Signal(str)
    edit_requested = Signal(str)
    status = Signal(str)
    transfer_failed = Signal(str)

    def __init__(self, storage: Storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.session: RemoteSession | None = None
        self.home_path = "/"
        self.root_path = "/"
        self.current_path = "/"
        self._tree_generation = 0
        self._navigation_serial = 0
        title = QLabel("FICHIERS DISTANTS")
        title.setStyleSheet("font-weight: bold; font-size: 10pt; padding: 3px;")
        self.root_combo = QComboBox()
        self.root_combo.addItem("Dossier actuel", "home")
        self.root_combo.addItem("Racine /", "root")
        self.root_combo.currentIndexChanged.connect(self.reload_root)
        self.recent_button = QToolButton()
        self.recent_button.setText("Récents ▼")
        self.recent_button.clicked.connect(self._show_recent_menu)
        self.refresh_button = QPushButton("⟳")
        self.refresh_button.setFixedSize(40, 30)
        self.refresh_button.setToolTip(tr("ui.refresh_file_tree"))
        self.refresh_button.setStyleSheet(
            "QPushButton { font-size: 16px; font-weight: 700; padding: 0; }"
        )
        self.refresh_button.clicked.connect(self.reload_root)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.root_combo, 1)
        toolbar.addWidget(self.recent_button)
        toolbar.addWidget(self.refresh_button)
        self.tree = RemoteTree()
        self.tree.remote_drag_handler = self._prepare_remote_drag
        self.tree.setHeaderLabels(["Nom", "Droits", "Taille"])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.header().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.header().customContextMenuRequested.connect(self._header_context_menu)
        self._apply_column_visibility()
        self.tree.itemExpanded.connect(self._expand_item)
        self.tree.itemDoubleClicked.connect(self._double_click)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.files_dropped.connect(self._upload_drop)
        self.transfers = TransferQueueWidget()
        self.transfers.transfer_failed.connect(self.transfer_failed.emit)
        self._drag_temp_dirs = []
        self.info = QLabel("Déconnecté")
        self.info.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(title)
        layout.addLayout(toolbar)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.transfers)
        layout.addWidget(self.info)
        self.set_session(None)


    def _apply_column_visibility(self) -> None:
        rights_visible = bool(self.storage.settings.get("file_column_rights_visible", True))
        size_visible = bool(self.storage.settings.get("file_column_size_visible", True))
        self.tree.setColumnHidden(1, not rights_visible)
        self.tree.setColumnHidden(2, not size_visible)

    def _header_context_menu(self, point) -> None:
        menu = QMenu(self)
        title = menu.addAction("Colonnes visibles")
        title.setEnabled(False)
        menu.addSeparator()

        name_action = menu.addAction("Nom")
        name_action.setCheckable(True)
        name_action.setChecked(True)
        name_action.setEnabled(False)

        rights_action = menu.addAction("Droits")
        rights_action.setCheckable(True)
        rights_action.setChecked(not self.tree.isColumnHidden(1))
        rights_action.toggled.connect(
            lambda checked: self._set_column_visible("file_column_rights_visible", 1, checked)
        )

        size_action = menu.addAction("Taille")
        size_action.setCheckable(True)
        size_action.setChecked(not self.tree.isColumnHidden(2))
        size_action.toggled.connect(
            lambda checked: self._set_column_visible("file_column_size_visible", 2, checked)
        )
        menu.exec(self.tree.header().mapToGlobal(point))

    def _set_column_visible(self, setting: str, column: int, visible: bool) -> None:
        self.storage.settings[setting] = bool(visible)
        self.storage.save()
        self.tree.setColumnHidden(column, not visible)
        if visible:
            self.tree.resizeColumnToContents(column)

    def active_transfer_count(self) -> int:
        return self.transfers.active_transfer_count()

    def set_session(self, session: RemoteSession | None) -> None:
        self._navigation_serial += 1
        self.transfers.set_session(session)
        if session is None:
            self.transfers.cancel_all()
        self.session = session
        self._reset_tree()
        self.setEnabled(session is not None)
        self.info.setText("Chargement…" if session else "Déconnecté")
        if session:
            run_async(
                session.remote_home,
                self._home_loaded,
                self._show_error,
                guard=lambda: self.session is session,
            )

    def shutdown_transfers(self, timeout_ms: int = 5000) -> bool:
        return self.transfers.shutdown(timeout_ms)

    def _home_loaded(self, path: object) -> None:
        self.home_path = str(path)
        self.current_path = self.home_path
        self.reload_root()

    def navigate_to(self, path: str) -> None:
        if not self.session or not path:
            return
        self.current_path = posixpath.normpath(path) or "/"
        blocker = QSignalBlocker(self.root_combo)
        self.root_combo.setCurrentIndex(0)
        del blocker
        self.reload_root()

    def _go_parent(self) -> None:
        if not self.session:
            return
        current = posixpath.normpath(self.current_path or "/")
        parent = posixpath.dirname(current.rstrip("/")) or "/"
        if parent == current:
            return
        self.navigate_to(parent)
        self.directory_activated.emit(parent)

    def reload_root(self) -> None:
        if not self.session:
            return
        self.root_path = "/" if self.root_combo.currentData() == "root" else self.current_path
        self._reset_tree()
        if self.root_path != "/":
            parent_item = QTreeWidgetItem(["...", "", ""])
            parent_item.setData(0, PARENT_ROLE, True)
            parent_item.setToolTip(0, tr("ui.show_the_parent_folder_and_move_the_terminal_there"))
            self.tree.addTopLevelItem(parent_item)
        root = QTreeWidgetItem([self.root_path, "", ""])
        root.setData(0, PATH_ROLE, self.root_path)
        root.setData(0, DIR_ROLE, True)
        root.setData(0, LOADED_ROLE, False)
        root.setData(0, SUMMARY_ROLE, self.root_path)
        root.setData(0, DETAIL_ROLE, self.root_path)
        root.addChild(QTreeWidgetItem(["Chargement…"] ))
        self.tree.addTopLevelItem(root)
        root.setExpanded(True)
        # Some programmatic refreshes do not emit itemExpanded again on every
        # Qt/Windows combination. Start the root load explicitly as a fallback;
        # _expand_item is idempotent because it marks the item as loaded first.
        self._expand_item(root)
        self.info.setText(self.root_path)

    def _reset_tree(self) -> None:
        """Invalidate all pending loads before destroying Qt items."""
        self._tree_generation += 1
        self.tree.prepare_for_reset()
        self.tree.clear()

    def _find_item_by_path(self, path: str) -> QTreeWidgetItem | None:
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value() is not None:
            item = iterator.value()
            if str(item.data(0, PATH_ROLE) or "") == path:
                return item
            iterator += 1
        return None

    def _expand_item(self, item: QTreeWidgetItem) -> None:
        if item.data(0, DIR_ROLE) and not item.data(0, LOADED_ROLE) and self.session:
            path = str(item.data(0, PATH_ROLE))
            item.setData(0, LOADED_ROLE, True)
            session = self.session
            generation = self._tree_generation
            run_async(
                lambda: session.list_directory(path),
                lambda rows: self._fill_path(path, rows),
                lambda text: self._directory_load_failed(path, text),
                guard=lambda: (
                    self.session is session
                    and self._tree_generation == generation
                ),
            )

    def _fill_path(self, path: str, rows: object) -> None:
        parent = self._find_item_by_path(path)
        if parent is None:
            return
        parent.takeChildren()
        for entry in rows:
            rights = str(entry["rights"])
            rights_icons = self._rights_icons(rights)
            child = QTreeWidgetItem([
                str(entry["name"]), rights_icons,
                "" if entry["is_dir"] else self._format_size(int(entry["size"])),
            ])
            child.setData(0, PATH_ROLE, entry["path"])
            child.setData(0, NAME_ROLE, entry["name"])
            child.setData(0, DIR_ROLE, bool(entry["is_dir"]))
            child.setData(0, LOADED_ROLE, False)
            modified = datetime.datetime.fromtimestamp(int(entry["mtime"])).strftime("%d/%m/%Y à %H:%M:%S")
            link = "  •  Lien symbolique" if entry.get("is_link") else ""
            meanings = self._rights_meanings(rights, bool(entry["is_dir"]))
            summary = f"{meanings}\nModifié : {modified}  •  {self._format_size(int(entry['size']))}{link}"
            detail = (
                f"{entry['path']}\nTaille exacte : {int(entry['size']):,} octets\n"
                f"Propriétaire : UID {entry['uid']}  •  Groupe : GID {entry['gid']}\n"
                f"Droits POSIX : {entry['mode_text']}\nAccès effectif : {rights_icons} — {meanings}\n"
                f"Modifié : {modified}{link}"
            ).replace(",", " ")
            child.setData(0, SUMMARY_ROLE, summary)
            child.setData(0, DETAIL_ROLE, detail)
            child.setForeground(1, QColor(RIGHT_COLORS.get(rights, "#C792EA")))
            if entry["is_dir"]:
                child.addChild(QTreeWidgetItem(["…"]))
            parent.addChild(child)
        parent.setData(0, LOADED_ROLE, True)

    def _directory_load_failed(self, path: str, text: str) -> None:
        parent = self._find_item_by_path(path)
        if parent is not None:
            parent.setData(0, LOADED_ROLE, False)
            parent.takeChildren()
            error_item = QTreeWidgetItem(["Erreur de chargement — développer pour réessayer"])
            parent.addChild(error_item)
        self._show_error(text)

    def _double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        # Never clear the tree while itemDoubleClicked is being emitted:
        # Qt still uses the item until the event has fully returned.
        if bool(item.data(0, PARENT_ROLE)):
            QTimer.singleShot(0, self._go_parent)
            return
        path = item.data(0, PATH_ROLE)
        if not path:
            return
        is_dir = bool(item.data(0, DIR_ROLE))
        selected_path = str(path)
        if is_dir:
            self._record(selected_path, "ouvert", True)
            self._schedule_directory_navigation(selected_path)
        else:
            self._record(selected_path, "modifié")
            self.edit_requested.emit(selected_path)

    def _schedule_directory_navigation(self, path: str) -> None:
        """Defer the root change until the current Qt event has completed."""
        self._navigation_serial += 1
        serial = self._navigation_serial
        session = self.session

        def apply_navigation() -> None:
            if (
                serial != self._navigation_serial
                or session is None
                or self.session is not session
            ):
                return
            self.navigate_to(path)
            self.directory_activated.emit(path)

        QTimer.singleShot(0, apply_navigation)

    def _context_menu(self, point) -> None:
        item = self.tree.itemAt(point)
        menu = QMenu(self)
        if item and bool(item.data(0, PARENT_ROLE)):
            menu.addAction("...", self._go_parent)
            menu.addSeparator()
            menu.addAction(tr("ui.refresh"), self.reload_root)
            menu.exec(self.tree.viewport().mapToGlobal(point))
            return
        target_dir = self.root_path
        if item and item.data(0, PATH_ROLE):
            path = str(item.data(0, PATH_ROLE))
            is_dir = bool(item.data(0, DIR_ROLE))
            target_dir = path if is_dir else posixpath.dirname(path)
            if is_dir:
                menu.addAction("Ouvrir un terminal ici", lambda: self.directory_activated.emit(path))
                menu.addAction("Envoyer des fichiers ici…", lambda: self._choose_upload(path))
                menu.addAction("Télécharger vers Windows…", lambda: self._download(path, True))
            else:
                menu.addAction("Ouvrir et modifier", lambda: self.edit_requested.emit(path))
                menu.addAction("Télécharger vers Windows…", lambda: self._download(path, False))
            menu.addSeparator()
        self._add_new_menu(menu, target_dir)
        if item and item.data(0, PATH_ROLE):
            menu.addSeparator()
            menu.addAction("Renommer…", lambda: self._rename(item))
            menu.addAction("Supprimer", lambda: self._delete(item))
            menu.addAction("Copier le chemin", lambda: QApplication.clipboard().setText(path))
        menu.addSeparator()
        menu.addAction("Actualiser", self.reload_root)
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _show_recent_menu(self) -> None:
        menu = QMenu(self)
        items = self.storage.recent_for(self.session.profile.id) if self.session else []
        if not items:
            menu.addAction("Aucun fichier récent").setEnabled(False)
        for entry in items[:20]:
            path = str(entry.get("path", ""))
            action = menu.addAction(f"{entry.get('action', '')} — {path}")
            action.triggered.connect(lambda _checked=False, p=path, d=bool(entry.get("is_dir")): self.navigate_to(p) if d else self.edit_requested.emit(p))
        menu.exec(self.recent_button.mapToGlobal(self.recent_button.rect().bottomLeft()))

    def _create_directory(self, parent: str) -> None:
        name, ok = QInputDialog.getText(self, "Nouveau dossier", "Nom du dossier :")
        if ok and name.strip() and "/" not in name:
            path = posixpath.join(parent, name.strip())
            session = self.session
            run_async(
                lambda: session.make_directory(path),
                lambda _x: self.reload_root(),
                self._show_error,
                guard=lambda: self.session is session,
            )

    def _rename(self, item: QTreeWidgetItem) -> None:
        old = str(item.data(0, PATH_ROLE))
        name, ok = QInputDialog.getText(self, "Renommer", "Nouveau nom :", text=posixpath.basename(old))
        if ok and name.strip() and "/" not in name:
            new = posixpath.join(posixpath.dirname(old), name.strip())
            session = self.session
            run_async(
                lambda: session.rename_remote(old, new),
                lambda _x: self.reload_root(),
                self._show_error,
                guard=lambda: self.session is session,
            )

    def _delete(self, item: QTreeWidgetItem) -> None:
        path = str(item.data(0, PATH_ROLE))
        is_dir = bool(item.data(0, DIR_ROLE))
        session = self.session
        if not session:
            return
        if not is_dir:
            self._confirm_delete(path, False, False, session)
            return
        run_async(
            lambda: session.remote_directory_has_entries(path),
            lambda non_empty: self._confirm_delete(
                path, True, bool(non_empty), session
            ),
            self._show_error,
            guard=lambda: self.session is session,
        )

    def _confirm_delete(
        self, path: str, is_dir: bool, non_empty: bool, session: RemoteSession
    ) -> None:
        if self.session is not session:
            return
        if is_dir and non_empty:
            answer = QMessageBox.warning(
                self,
                tr('ui.delete'),
                f"⚠ {tr('ui.folder_not_empty')}\n\n"
                f"{tr('ui.folder_not_empty_delete_warning')}\n\n{path}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        else:
            answer = QMessageBox.question(
                self,
                tr('ui.delete'),
                f"{tr('fragment.permanently_delete')}\n{path} ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        if answer != QMessageBox.Yes:
            return
        run_async(
            lambda: session.remove_remote(path, is_dir),
            lambda _x: self.reload_root(),
            self._show_error,
            guard=lambda: self.session is session,
        )

    def _record(self, path: str, action: str, is_dir: bool = False) -> None:
        if self.session:
            self.storage.add_recent_file(self.session.profile.id, path, action, is_dir)

    def _show_error(self, text: str) -> None:
        self.info.setText("Erreur")
        QMessageBox.critical(self, "Fichiers distants", text)

    @staticmethod
    def _rights_icons(rights: str) -> str:
        if rights == "🔒":
            return "🔒"
        icons = []
        if "R" in rights:
            icons.append("👓")
        if "W" in rights:
            icons.append("✏")
        if "X" in rights:
            icons.append("⚡")
        return "  ".join(icons) or "🔒"

    @staticmethod
    def _rights_meanings(rights: str, is_dir: bool) -> str:
        if rights == "🔒":
            return "🔒 Aucun accès effectif"
        parts = []
        if "R" in rights:
            parts.append("👓 Lecture")
        if "W" in rights:
            parts.append("✏ Écriture")
        if "X" in rights:
            parts.append("⚡ Traversée du dossier" if is_dir else "⚡ Exécution")
        return "  •  ".join(parts)

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in byte_units():
            if value < 1024 or unit in {"To", "TB"}:
                return f"{value:.0f} {unit}" if unit in {"o", "B"} else f"{value:.1f} {unit}"
            value /= 1024
        return str(size)
