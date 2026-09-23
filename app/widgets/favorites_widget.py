import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.dialogs.favorite_dialog import FavoriteDialog
from app.i18n import tr
from app.models import FavoriteCommand
from app.storage import Storage


PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class FavoritesWidget(QWidget):
    command_requested = Signal(str)

    def __init__(self, storage: Storage, service_provider, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.service_provider = service_provider
        self.connected = False

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Favoris", "Commande"])
        self.tree.setColumnWidth(0, 240)
        self.tree.itemDoubleClicked.connect(lambda *_args: self.run_selected())

        self.add_button = QPushButton("Ajouter")
        self.add_button.clicked.connect(self.add_favorite)
        self.edit_button = QPushButton("Modifier")
        self.edit_button.clicked.connect(self.edit_selected)
        self.delete_button = QPushButton("Supprimer")
        self.delete_button.clicked.connect(self.delete_selected)
        self.run_button = QPushButton("Exécuter")
        self.run_button.clicked.connect(self.run_selected)

        buttons = QHBoxLayout()
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(self.run_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)
        self.reload()
        self.set_connected(False)

    def set_connected(self, connected: bool) -> None:
        self.connected = connected
        self.run_button.setEnabled(connected)

    def reload(self) -> None:
        self.tree.clear()
        categories: dict[str, QTreeWidgetItem] = {}
        for favorite in sorted(self.storage.favorites, key=lambda item: (item.category.lower(), item.name.lower())):
            category_item = categories.get(favorite.category)
            if category_item is None:
                category_item = QTreeWidgetItem([favorite.category, ""])
                category_item.setFirstColumnSpanned(True)
                categories[favorite.category] = category_item
                self.tree.addTopLevelItem(category_item)
            item = QTreeWidgetItem([favorite.name, favorite.command])
            item.setData(0, 32, favorite.id)
            category_item.addChild(item)
        self.tree.expandAll()

    def selected_favorite(self) -> FavoriteCommand | None:
        item = self.tree.currentItem()
        if not item:
            return None
        favorite_id = item.data(0, 32)
        if not favorite_id:
            return None
        return next((favorite for favorite in self.storage.favorites if favorite.id == favorite_id), None)

    def add_favorite(self, command: str = "") -> None:
        dialog = FavoriteDialog(command=command, parent=self)
        if dialog.exec():
            self.storage.favorites.append(dialog.result_favorite())
            self.storage.save()
            self.reload()

    def edit_selected(self) -> None:
        favorite = self.selected_favorite()
        if not favorite:
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
        answer = QMessageBox.question(self, tr('ui.delete'), f"{tr('ui.delete_favorite')} « {favorite.name} » ?")
        if answer == QMessageBox.Yes:
            self.storage.favorites = [item for item in self.storage.favorites if item.id != favorite.id]
            self.storage.save()
            self.reload()

    def run_selected(self) -> None:
        favorite = self.selected_favorite()
        if not favorite or not self.connected:
            return
        if favorite.confirm:
            answer = QMessageBox.question(self, tr('ui.confirmation'), f"{tr('ui.run')} « {favorite.name} » ?\n\n{favorite.command}")
            if answer != QMessageBox.Yes:
                return
        command = self._resolve_parameters(favorite.command)
        if command is not None:
            self.command_requested.emit(command)

    def _resolve_parameters(self, command: str) -> str | None:
        result = command
        for name in dict.fromkeys(PLACEHOLDER_RE.findall(command)):
            if name == "service":
                services = self.service_provider()
                if not services:
                    QMessageBox.information(self, "Paramètre", "Aucun service n'est encore chargé.")
                    return None
                value, ok = QInputDialog.getItem(self, "Paramètre", "Service :", services, 0, True)
            else:
                value, ok = QInputDialog.getText(self, "Paramètre", f"Valeur de {{{name}}} :")
            if not ok or not value:
                return None
            result = result.replace("{" + name + "}", value)
        return result
