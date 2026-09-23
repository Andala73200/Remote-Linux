import re
import shlex

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QWidget

from app.core.cd_command import is_cd_command
from app.dialogs.favorite_dialog import FavoriteDialog
from app.dialogs.favorites_manager_dialog import FavoritesManagerDialog
from app.dialogs.smart_action_dialog import ServiceLogsDialog, SimpleParametersDialog
from app.models import FavoriteCommand
from app.storage import Storage

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
SENSITIVE_WORDS = ("sudo ", " rm ", "delete", "systemctl stop", "systemctl restart", ">", "mv ")


class FavoritesController(QObject):
    command_requested = Signal(str)
    tree_command_requested = Signal(str)
    output_requested = Signal(str, str)
    live_output_requested = Signal(str, str)

    def __init__(self, storage: Storage, service_provider, favorite_services_provider, distribution_command_provider=None, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.service_provider = service_provider
        self.favorite_services_provider = favorite_services_provider
        self.distribution_command_provider = distribution_command_provider or (lambda: "")
        self.connected = False

    def show_menu(self, button: QWidget, current_command: str = "") -> None:
        menu = QMenu(button)
        folder_menus: dict[tuple[str, ...], QMenu] = {}
        for favorite in sorted(self.storage.favorites, key=lambda f: (f.folder.lower(), f.name.lower())):
            parent_menu = menu
            path: tuple[str, ...] = ()
            for part in [p for p in favorite.folder.split("/") if p][:3]:
                path += (part,)
                if path not in folder_menus:
                    folder_menus[path] = parent_menu.addMenu(part)
                parent_menu = folder_menus[path]
            action = parent_menu.addAction(favorite.name)
            action.setToolTip(favorite.command or "Favori intelligent")
            action.setEnabled(self.connected)
            action.triggered.connect(lambda _checked=False, item=favorite: self.run(item, button))
        if self.storage.favorites:
            menu.addSeparator()
        add_action = menu.addAction("Ajouter la commande actuelle…")
        add_action.setEnabled(bool(current_command))
        add_action.triggered.connect(lambda: self.add_favorite(current_command, button))
        menu.addAction("Gérer les favoris…", lambda: self.manage(button))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def add_favorite(self, command: str, parent: QWidget) -> None:
        dialog = FavoriteDialog(command=command, parent=parent)
        if dialog.exec():
            self.storage.favorites.append(dialog.result_favorite())
            self.storage.save()

    def manage(self, parent: QWidget) -> None:
        FavoritesManagerDialog(self.storage, parent).exec()

    def run(self, favorite: FavoriteCommand, parent: QWidget) -> None:
        if favorite.kind == "smart_logs":
            self._smart_logs(favorite, parent)
            return
        if favorite.kind.startswith("smart_"):
            self._run_smart(favorite, parent)
            return
        command = self._resolve_parameters(favorite.command, parent)
        if command and self._approve(command, favorite.confirm, favorite.name, parent):
            if favorite.follow_tree and is_cd_command(command):
                self.tree_command_requested.emit(command)
            else:
                self.command_requested.emit(command)

    def _smart_logs(self, favorite: FavoriteCommand, parent: QWidget) -> None:
        dialog = ServiceLogsDialog(
            self.service_provider(), self.favorite_services_provider(), favorite.params, parent
        )
        if not dialog.exec():
            return
        command = dialog.command()
        values = dialog.values()
        if dialog.save_preset.isChecked() and dialog.preset_name.text().strip():
            self.storage.favorites.append(FavoriteCommand(
                name=dialog.preset_name.text().strip(), folder="Services/Logs",
                kind="smart_logs", params=values,
            ))
            self.storage.save()
        title = f"Journaux — {values.get('service', '')}"
        if values.get("follow"):
            self.live_output_requested.emit(command, title)
        else:
            self.output_requested.emit(command, title)

    def _run_smart(self, favorite: FavoriteCommand, parent: QWidget) -> None:
        kind = favorite.kind
        if kind == "smart_updates":
            command = str(self.distribution_command_provider() or "").strip()
            if not command:
                QMessageBox.information(parent, "Mises à jour", "Aucun serveur connecté.")
                return
            self.output_requested.emit(command, "Mises à jour disponibles")
            return
        if kind in {"smart_service_status", "smart_service_restart"}:
            service = self._choose_service(parent)
            if not service:
                return
            if kind == "smart_service_status":
                command = f"systemctl status --no-pager -- {shlex.quote(service)}"
                self.output_requested.emit(command, f"État — {service}")
            else:
                command = f"sudo systemctl restart -- {shlex.quote(service)}"
                if self._approve(command, True, "Redémarrer un service", parent):
                    self.command_requested.emit(command)
            return
        if kind == "smart_top":
            self.output_requested.emit(
                "ps -eo pid,user,comm,%cpu,%mem --sort=-%cpu | head -n 21",
                "Processus les plus gourmands",
            )
            return
        specs = {
            "smart_ping": ("Ping", [("host", "Hôte ou IP :", "1.1.1.1")]),
            "smart_test_port": ("Tester un port", [("host", "Hôte ou IP :", "127.0.0.1"), ("port", "Port :", "80")]),
            "smart_find_file": ("Rechercher un fichier", [("root", "Dossier de départ :", "/home"), ("name", "Nom ou motif :", "*.conf")]),
            "smart_large_files": ("Plus gros fichiers", [("root", "Dossier de départ :", "/"), ("count", "Nombre de résultats :", "30")]),
        }
        title, fields = specs.get(kind, (favorite.name, []))
        dialog = SimpleParametersDialog(title, fields, parent)
        if not dialog.exec():
            return
        values = dialog.values()
        if kind == "smart_ping":
            command = f"ping -c 4 -- {shlex.quote(values['host'])}"
        elif kind == "smart_test_port":
            host = values.get("host", "")
            try:
                port = int(values.get("port", ""))
            except ValueError:
                QMessageBox.warning(parent, title, "Le port doit être un nombre.")
                return
            if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host) or not 1 <= port <= 65535:
                QMessageBox.warning(parent, title, "Hôte ou port invalide.")
                return
            command = f"if command -v nc >/dev/null 2>&1; then nc -zvw5 {shlex.quote(host)} {port}; else timeout 5 bash -c 'cat </dev/null >/dev/tcp/{host}/{port}' && echo 'Port ouvert' || echo 'Port fermé ou inaccessible'; fi"
        elif kind == "smart_find_file":
            command = f"find {shlex.quote(values['root'])} -iname {shlex.quote(values['name'])} 2>/dev/null | head -n 500"
        else:
            try:
                count = max(1, min(500, int(values.get("count", "30") or 30)))
            except ValueError:
                QMessageBox.warning(parent, title, "Le nombre de résultats doit être numérique.")
                return
            command = f"find {shlex.quote(values['root'])} -type f -printf '%s %p\n' 2>/dev/null | sort -nr | head -n {count} | numfmt --field=1 --to=iec"
        self.output_requested.emit(command, title)

    def _choose_service(self, parent: QWidget) -> str | None:
        services = self.service_provider()
        favorites = [name for name in services if name in self.favorite_services_provider()]
        choices = favorites or services
        if not choices:
            QMessageBox.information(parent, "Service", "Aucun service n'est encore chargé.")
            return None
        value, ok = QInputDialog.getItem(parent, "Service", "Service :", choices, 0, True)
        return value if ok and value else None

    def _approve(self, command: str, explicit: bool, title: str, parent: QWidget) -> bool:
        mode = str(self.storage.settings.get("command_preview", "sensitive"))
        sensitive = explicit or any(word in f" {command.lower()} " for word in SENSITIVE_WORDS)
        if mode == "always" or (mode == "sensitive" and sensitive):
            text = f"Commande qui sera exécutée :\n\n{command}"
            return QMessageBox.question(parent, title, text) == QMessageBox.Yes
        return True

    def _resolve_parameters(self, command: str, parent: QWidget) -> str | None:
        result = command
        for name in dict.fromkeys(PLACEHOLDER_RE.findall(command)):
            if name == "service":
                value = self._choose_service(parent)
                ok = bool(value)
            else:
                value, ok = QInputDialog.getText(parent, "Paramètre", f"Valeur de {{{name}}} :")
            if not ok or not value:
                return None
            result = result.replace("{" + name + "}", value)
        return result
