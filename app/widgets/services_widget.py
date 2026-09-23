import re
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.async_task import run_async
from app.core.session import RemoteSession
from app.i18n import tr
from app.storage import Storage
from app.widgets.service_delete_mixin import ServiceDeleteMixin
from app.widgets.empty_message_table import EmptyMessageTable

STATE_COLORS = {
    "active": "#55D187",
    "inactive": "#9AA1AA",
    "failed": "#FF6666",
    "activating": "#F2C866",
    "reloading": "#F2C866",
    "deactivating": "#FF9F55",
    "unknown": "#C792EA",
}
FAVORITE_BACKGROUND = QColor("#252C38")
FAVORITE_STAR_BACKGROUND = QColor("#40391F")
FAVORITE_STAR_COLOR = QColor("#FFD166")


def natural_key(value: str):
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", value)
    ]


class ServicesWidget(ServiceDeleteMixin, QWidget):
    logs_requested = Signal(str)
    config_requested = Signal(str)
    failed_services_changed = Signal(int, str)
    favorite_failed_services_changed = Signal(int, str)

    def __init__(self, storage: Storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.session: RemoteSession | None = None
        self.sudo_password_provider: Callable[[bool], str | bool | None] | None = None
        self.services: list[dict[str, str]] = []
        self.sort_column = 1
        self.sort_ascending = True

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Rechercher un service…")
        self.search_edit.textChanged.connect(self._render)
        self.refresh_button = QPushButton("Actualiser")
        self.refresh_button.clicked.connect(self.refresh)
        self.favorite_filter_button = QPushButton("★")
        self.favorite_filter_button.setObjectName("favoriteFilterButton")
        self.favorite_filter_button.setCheckable(True)
        self.favorite_filter_button.setFixedWidth(38)
        self.favorite_filter_button.setChecked(
            bool(self.storage.settings.get("service_favorites_only", False))
        )
        self.favorite_filter_button.setStyleSheet(
            "QPushButton#favoriteFilterButton { font-size: 13pt; padding: 3px; }"
            "QPushButton#favoriteFilterButton:checked {"
            " background: #5A4A1E; border-color: #D7AE45; color: #FFD166; }"
        )
        self.favorite_filter_button.toggled.connect(self._favorite_filter_changed)
        self._update_filter_tooltip()

        top = QHBoxLayout()
        top.addWidget(self.search_edit, 1)
        top.addWidget(self.refresh_button)
        top.addWidget(self.favorite_filter_button)

        self.table = EmptyMessageTable(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["★", "Service", "Chargé", "État", "Sous-état", "Démarrage", "Description"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().sectionClicked.connect(self._sort_clicked)
        self.table.doubleClicked.connect(self.show_logs)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        buttons = QHBoxLayout()
        for label, action in [
            ("Démarrer", "start"),
            ("Arrêter", "stop"),
            ("Redémarrer", "restart"),
        ]:
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, name=action: self.run_action(name)
            )
            buttons.addWidget(button)
        self.logs_button = QPushButton("Voir les logs…")
        self.logs_button.clicked.connect(self.show_logs)
        buttons.addWidget(self.logs_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        self.set_connected(False)

    def set_sudo_password_provider(
        self, provider: Callable[[bool], str | bool | None]
    ) -> None:
        self.sudo_password_provider = provider

    def set_session(self, session: RemoteSession | None) -> None:
        self.session = session
        self.set_connected(session is not None)
        if session:
            self.refresh()
        else:
            self.services.clear()
            self.table.setRowCount(0)
            self.table.set_empty_message()

    def set_connected(self, connected: bool) -> None:
        self.search_edit.setEnabled(connected)
        self.refresh_button.setEnabled(connected)
        self.table.setEnabled(connected)
        for button in self.findChildren(QPushButton):
            button.setEnabled(connected)

    def service_names(self) -> list[str]:
        return sorted(
            (service["unit"] for service in self.services), key=natural_key
        )

    def favorite_service_names(self) -> set[str]:
        if not self.session:
            return set()
        return self.storage.favorite_services(self.session.profile.id)

    def refresh(self) -> None:
        if not self.session:
            return
        self.refresh_button.setEnabled(False)
        session = self.session
        run_async(
            session.list_services,
            self._on_services,
            self._show_error,
            guard=lambda: self.session is session,
        )

    def _on_services(self, services: object) -> None:
        self.refresh_button.setEnabled(True)
        self.services = list(services)
        failed = [
            row["unit"]
            for row in self.services
            if self._state_key(row) == "failed"
        ]
        favorite_names = self.favorite_service_names()
        favorite_failed = [name for name in failed if name in favorite_names]
        self.failed_services_changed.emit(len(failed), ", ".join(failed[:5]))
        self.favorite_failed_services_changed.emit(
            len(favorite_failed), ", ".join(favorite_failed[:5])
        )
        self._render()

    def _favorite_filter_changed(self, checked: bool) -> None:
        self.storage.settings["service_favorites_only"] = bool(checked)
        self.storage.save()
        self._update_filter_tooltip()
        self._render()

    def _update_filter_tooltip(self) -> None:
        text = (
            "Afficher tous les services"
            if self.favorite_filter_button.isChecked()
            else "Afficher uniquement les services favoris"
        )
        self.favorite_filter_button.setToolTip(text)

    def _sort_clicked(self, column: int) -> None:
        if column == self.sort_column:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = column
            self.sort_ascending = True
        self._render()

    def _render(self, *_args) -> None:
        needle = self.search_edit.text().strip().lower()
        rows = [
            service
            for service in self.services
            if not needle or needle in " ".join(service.values()).lower()
        ]
        favorites = self.favorite_service_names()
        favorites_only = self.favorite_filter_button.isChecked()
        if favorites_only:
            rows = [row for row in rows if row["unit"] in favorites]
        if self.session and favorites_only and not favorites:
            self.table.set_empty_message(
                "ui.no_favorite_service", "ui.no_favorite_service_hint"
            )
        else:
            self.table.set_empty_message()

        fields = [None, "unit", "load", "active", "sub", "enabled", "description"]
        field = fields[self.sort_column] or "unit"
        favorite_rows = [row for row in rows if row["unit"] in favorites]
        normal_rows = [row for row in rows if row["unit"] not in favorites]
        for group in (favorite_rows, normal_rows):
            group.sort(
                key=lambda row: natural_key(row.get(field, "")),
                reverse=not self.sort_ascending,
            )
        rows = favorite_rows + normal_rows

        self.table.setRowCount(len(rows))
        for row_index, service in enumerate(rows):
            is_favorite = service["unit"] in favorites
            state_color = QBrush(QColor(STATE_COLORS[self._state_key(service)]))
            values = [
                "★" if is_favorite else "☆",
                service["unit"],
                service["load"],
                service["active"],
                service["sub"],
                service["enabled"],
                service["description"],
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, service["unit"])
                if column in {0, 3, 4}:
                    item.setTextAlignment(Qt.AlignCenter)
                if column in {1, 3, 4}:
                    item.setForeground(state_color)
                if is_favorite:
                    item.setBackground(
                        QBrush(
                            FAVORITE_STAR_BACKGROUND
                            if column == 0
                            else FAVORITE_BACKGROUND
                        )
                    )
                    if column == 0:
                        item.setForeground(QBrush(FAVORITE_STAR_COLOR))
                    if column == 1:
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                self.table.setItem(row_index, column, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    @staticmethod
    def _state_key(service: dict[str, str]) -> str:
        active, sub = service.get("active", "unknown"), service.get("sub", "")
        if active == "failed" or sub == "failed":
            return "failed"
        if active in STATE_COLORS:
            return active
        if sub in STATE_COLORS:
            return sub
        return "unknown"

    def _cell_clicked(self, row: int, column: int) -> None:
        if column == 0 and self.session:
            item = self.table.item(row, 1)
            if item:
                self._toggle_favorite(str(item.data(Qt.UserRole)))

    def selected_service(self) -> str | None:
        row = self.table.currentRow()
        item = self.table.item(row, 1) if row >= 0 else None
        return str(item.data(Qt.UserRole)) if item else None

    def _context_menu(self, point) -> None:
        item = self.table.itemAt(point)
        if item:
            self.table.selectRow(item.row())
        service = self.selected_service()
        if not service:
            return
        favorites = self.favorite_service_names()
        menu = QMenu(self)
        menu.addAction(
            "Retirer des favoris" if service in favorites else "Ajouter aux favoris",
            lambda: self._toggle_favorite(service),
        )
        menu.addSeparator()
        for label, action in [
            ("Démarrer", "start"),
            ("Arrêter", "stop"),
            ("Redémarrer", "restart"),
            ("Recharger", "reload"),
        ]:
            menu.addAction(
                label,
                lambda _checked=False, name=action: self.run_action(name),
            )
        menu.addSeparator()
        menu.addAction("Activer au démarrage", lambda: self.run_action("enable"))
        menu.addAction("Désactiver au démarrage", lambda: self.run_action("disable"))
        menu.addSeparator()
        menu.addAction(
            tr("ui.open_service_configuration"),
            lambda: self._open_service_configuration(service),
        )
        menu.addAction("Afficher les journaux…", self.show_logs)
        menu.addAction("Copier le nom", lambda: self._copy_name(service))
        menu.addSeparator()
        menu.addAction(
            "🗑 Supprimer le service…", lambda: self._prepare_delete(service)
        )
        menu.exec(self.table.viewport().mapToGlobal(point))

    def _toggle_favorite(self, service: str) -> None:
        if not self.session:
            return
        profile_id = self.session.profile.id
        self.storage.set_service_favorite(
            profile_id, service, service not in self.favorite_service_names()
        )
        self._on_services(self.services)

    def run_action(self, action: str) -> None:
        service = self.selected_service()
        if not self.session or not service:
            QMessageBox.information(
                self, "Services", "Sélectionne d'abord un service."
            )
            return
        command = f"sudo systemctl {action} -- {service}"
        mode = str(self.storage.settings.get("command_preview", "sensitive"))
        protected = bool(self.storage.settings.get("protected_mode", True))
        sensitive = action in {"stop", "restart", "disable"}
        if (
            protected and sensitive
            or mode == "always"
            or mode == "sensitive" and sensitive
        ):
            text = f"Confirmer l’action sur {service} ?\n\nCommande :\n{command}"
            if QMessageBox.question(self, "Confirmation", text) != QMessageBox.Yes:
                return
        self._execute_action(action, service, self.session.sudo_password)

    def _execute_action(
        self, action: str, service: str, password: str | None
    ) -> None:
        session = self.session
        if not session:
            self._show_error("La connexion SSH n’est plus active.")
            return

        def done(result: object) -> None:
            code, output = result
            if code == 0:
                self.refresh()
                return
            lowered = output.lower()
            needs_password = any(
                word in lowered
                for word in (
                    "password",
                    "mot de passe",
                    "authentication",
                    "sorry, try again",
                    "incorrect",
                    "a password is required",
                )
            )
            if needs_password and self.sudo_password_provider:
                session.sudo_password = None
                entered = self.sudo_password_provider(bool(password))
                if entered is not False and entered:
                    self._execute_action(action, service, str(entered))
                    return
            self._show_error(output or f"systemctl a retourné le code {code}.")

        run_async(
            lambda: session.systemctl(action, service, password),
            done,
            self._show_error,
            guard=lambda: self.session is session,
        )

    def _open_service_configuration(self, service: str) -> None:
        session = self.session
        if not session:
            return

        def done(details: object) -> None:
            path = str(dict(details).get("path") or "")
            if not path:
                self._show_error(tr("ui.service_configuration_file_not_found"))
                return
            self.config_requested.emit(path)

        run_async(
            lambda: session.service_details(service),
            done,
            self._show_error,
            guard=lambda: self.session is session,
        )

    def show_logs(self) -> None:
        service = self.selected_service()
        if self.session and service:
            self.logs_requested.emit(service)

    @staticmethod
    def _copy_name(service: str) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(service)

    def _show_error(self, text: str) -> None:
        self.refresh_button.setEnabled(bool(self.session))
        QMessageBox.critical(self, "Erreur", text)
