from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from app.dialogs.favorite_dialog import FavoriteDialog
from app.i18n import tr
from app.models import FavoriteCommand
from app.storage import Storage


class FavoritesManagerDialog(QDialog):
    def __init__(self, storage: Storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.setWindowTitle("Gérer les commandes favorites")
        self.resize(780, 500)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Favori", "Commande"])
        self.tree.header().setStretchLastSection(True)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit_selected())
        add = QPushButton("Ajouter")
        edit = QPushButton("Modifier")
        delete = QPushButton("Supprimer")
        close = QPushButton("Fermer")
        add.clicked.connect(self.add_favorite)
        edit.clicked.connect(self.edit_selected)
        delete.clicked.connect(self.delete_selected)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(edit)
        row.addWidget(delete)
        row.addStretch(1)
        row.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tree, 1)
        layout.addLayout(row)
        self.reload()

    def reload(self) -> None:
        self.tree.clear()
        folders: dict[tuple[str, ...], QTreeWidgetItem] = {}
        for favorite in sorted(self.storage.favorites, key=lambda f: (f.folder.lower(), f.name.lower())):
            parent = None
            path: tuple[str, ...] = ()
            for part in [p for p in favorite.folder.split("/") if p]:
                path += (part,)
                folder_item = folders.get(path)
                if not folder_item:
                    folder_item = QTreeWidgetItem([part, ""])
                    folder_item.setData(0, Qt.UserRole + 1, "folder")
                    (parent.addChild(folder_item) if parent else self.tree.addTopLevelItem(folder_item))
                    folders[path] = folder_item
                parent = folder_item
            display = favorite.command or "Action intelligente"
            item = QTreeWidgetItem([favorite.name, display])
            item.setData(0, Qt.UserRole, favorite.id)
            (parent.addChild(item) if parent else self.tree.addTopLevelItem(item))
        self.tree.expandAll()

    def selected_favorite(self) -> FavoriteCommand | None:
        item = self.tree.currentItem()
        favorite_id = item.data(0, Qt.UserRole) if item else None
        return next((f for f in self.storage.favorites if f.id == favorite_id), None)

    def add_favorite(self) -> None:
        dialog = FavoriteDialog(parent=self)
        if dialog.exec():
            self.storage.favorites.append(dialog.result_favorite())
            self.storage.save()
            self.reload()

    def edit_selected(self) -> None:
        favorite = self.selected_favorite()
        if not favorite:
            return
        if favorite.kind != "command":
            name, ok = QInputDialog.getText(self, "Favori intelligent", "Nom :", text=favorite.name)
            if not ok or not name.strip():
                return
            folder, ok = QInputDialog.getText(self, "Favori intelligent", "Dossier (3 niveaux maximum) :", text=favorite.folder)
            parts = [part.strip() for part in folder.split("/") if part.strip()]
            if not ok or len(parts) > 3:
                return
            favorite.name = name.strip()
            favorite.folder = "/".join(parts) or "Général"
            self.storage.save()
            self.reload()
            return
        dialog = FavoriteDialog(favorite, parent=self)
        if dialog.exec():
            dialog.result_favorite()
            self.storage.save()
            self.reload()

    def delete_selected(self) -> None:
        favorite = self.selected_favorite()
        if not favorite:
            return
        if QMessageBox.question(self, tr('ui.delete'), f"{tr('ui.delete')} « {favorite.name} » ?") == QMessageBox.Yes:
            self.storage.favorites = [item for item in self.storage.favorites if item.id != favorite.id]
            self.storage.save()
            self.reload()
