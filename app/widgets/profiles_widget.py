from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.models import ConnectionProfile
from app.core.distribution import distribution_label


class _ProfileRow(QWidget):
    STATE_COLORS = {
        "disconnected": "#8D96A3",
        "connecting": "#F2C866",
        "connected": "#55D187",
        "error": "#FF6666",
    }
    STATE_LABELS = {
        "disconnected": "Déconnecté",
        "connecting": "Connexion…",
        "connected": "Connecté",
        "error": "Erreur",
    }

    def __init__(self, profile: ConnectionProfile, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.status_dot = QLabel("●")
        self.status_dot.setFixedWidth(14)
        self.kind_icon = QLabel("☁" if profile.kind == "cloudflare" else "⌁")
        self.kind_icon.setFixedWidth(18)
        self.name_label = QLabel(profile.name)
        self.name_label.setStyleSheet("font-weight: 600; font-size: 10.5pt;")
        self.detail_label = QLabel()
        self.detail_label.setStyleSheet("color: #98A3B1; font-size: 8.7pt;")

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(1)
        texts.addWidget(self.name_label)
        texts.addWidget(self.detail_label)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(5)
        layout.addWidget(self.status_dot)
        layout.addWidget(self.kind_icon)
        layout.addLayout(texts, 1)
        self.setStyleSheet("background: transparent;")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.set_state("disconnected")

    def set_state(self, state: str) -> None:
        state = state if state in self.STATE_COLORS else "disconnected"
        self.status_dot.setStyleSheet(
            f"color: {self.STATE_COLORS[state]}; font-size: 12pt; background: transparent;"
        )
        if self.profile.kind != "cloudflare":
            connection_type = "SSH direct"
        elif self.profile.cloudflare_auth_mode == "user_login":
            connection_type = "Cloudflare · Connexion utilisateur"
        else:
            connection_type = "Cloudflare · Jeton de service"
        distro = distribution_label(getattr(self.profile, "distribution", "auto"))
        self.detail_label.setText(f"{connection_type} · {distro} · {self.STATE_LABELS[state]}")


class ProfilesWidget(QWidget):
    add_requested = Signal()
    edit_requested = Signal()
    delete_requested = Signal()
    connect_requested = Signal()
    disconnect_requested = Signal()
    purge_requested = Signal()
    health_popup_reactivate_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._busy = False
        self._active_profile_id: str | None = None

        title = QLabel("SITES SSH FAVORIS")
        title.setStyleSheet("font-weight: bold; font-size: 10pt; padding: 3px;")

        self.add_button = self._tool_button("＋", "Ajouter un site SSH")
        self.edit_button = self._tool_button("✎", "Modifier le site sélectionné")
        self.delete_button = self._tool_button("−", "Supprimer le site sélectionné")
        self.connection_button = self._tool_button("⏻", "Se connecter")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.add_button)
        header.addWidget(self.edit_button)
        header.addWidget(self.delete_button)
        header.addWidget(self.connection_button)

        self.list = QListWidget()
        self.list.setSpacing(2)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.itemDoubleClicked.connect(lambda _item: self._double_click())
        self.list.currentItemChanged.connect(lambda *_args: self._update_actions())
        self.list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(header)
        layout.addWidget(self.list, 1)

        self.add_button.clicked.connect(self.add_requested)
        self.edit_button.clicked.connect(self.edit_requested)
        self.delete_button.clicked.connect(self.delete_requested)
        self.connection_button.clicked.connect(self._toggle_connection)
        self._update_actions()

    @staticmethod
    def _tool_button(text: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFixedSize(30, 28)
        button.setAutoRaise(True)
        return button

    def set_profiles(self, profiles: list[ConnectionProfile]) -> None:
        selected_id = self.selected_profile_id()
        self.list.clear()
        for profile in profiles:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, profile.id)
            item.setData(Qt.UserRole + 1, profile)
            row = _ProfileRow(profile, self.list)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            if profile.id == selected_id:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self._refresh_states()
        self._update_actions()

    def selected_profile_id(self) -> str | None:
        item = self.list.currentItem()
        return str(item.data(Qt.UserRole)) if item else None

    def select_profile(self, profile_id: str) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.UserRole) == profile_id:
                self.list.setCurrentItem(item)
                break

    def set_connection_state(self, connected: bool, busy: bool = False) -> None:
        self._connected = connected
        self._busy = busy
        if connected or busy:
            self._active_profile_id = self.selected_profile_id()
        elif not connected:
            self._active_profile_id = None
        self._refresh_states()
        self._update_actions()

    def _refresh_states(self) -> None:
        for row_index in range(self.list.count()):
            item = self.list.item(row_index)
            row = self.list.itemWidget(item)
            if not isinstance(row, _ProfileRow):
                continue
            profile_id = str(item.data(Qt.UserRole))
            if profile_id == self._active_profile_id:
                state = "connected" if self._connected else "connecting" if self._busy else "disconnected"
            else:
                state = "disconnected"
            row.set_state(state)

    def _update_actions(self) -> None:
        has_selection = self.list.currentItem() is not None
        selected_is_active = (
            has_selection and self.selected_profile_id() == self._active_profile_id
        )
        self.add_button.setEnabled(not self._busy)
        self.edit_button.setEnabled(has_selection and not self._busy and not self._connected)
        self.delete_button.setEnabled(has_selection and not self._busy and not self._connected)
        can_toggle = has_selection and not self._busy and (
            not self._connected or selected_is_active
        )
        self.connection_button.setEnabled(can_toggle)
        self.connection_button.setText("⏻")
        self.connection_button.setToolTip(
            "Se déconnecter" if self._connected and selected_is_active else "Se connecter"
        )
        self.list.setEnabled(not self._busy)

    def _toggle_connection(self) -> None:
        if self._connected:
            self.disconnect_requested.emit()
        else:
            self.connect_requested.emit()

    def _double_click(self) -> None:
        if self._busy:
            return
        if self._connected:
            if self.selected_profile_id() == self._active_profile_id:
                self.disconnect_requested.emit()
            return
        self.connect_requested.emit()

    def _context_menu(self, point) -> None:
        item = self.list.itemAt(point)
        if item:
            self.list.setCurrentItem(item)
        menu = QMenu(self)
        if item:
            profile_id = str(item.data(Qt.UserRole))
            selected_is_active = profile_id == self._active_profile_id
            if self._connected and selected_is_active:
                menu.addAction("Se déconnecter", self.disconnect_requested.emit)
            elif not self._connected and not self._busy:
                menu.addAction("Se connecter", self.connect_requested.emit)
            menu.addSeparator()
            edit_action = menu.addAction("Modifier…", self.edit_requested.emit)
            delete_action = menu.addAction("Supprimer…", self.delete_requested.emit)
            purge_action = menu.addAction(
                tr('ui.purge_connection_data_menu'), self.purge_requested.emit
            )
            edit_action.setEnabled(not self._busy and not self._connected)
            delete_action.setEnabled(not self._busy and not self._connected)
            purge_action.setEnabled(not self._busy and not selected_is_active)
            menu.addSeparator()
            profile = item.data(Qt.UserRole + 1)
            if not bool(getattr(profile, "show_health_popup", True)):
                profile_id = str(item.data(Qt.UserRole))
                menu.addAction(
                    "Réactiver la popup Santé du serveur",
                    lambda: self.health_popup_reactivate_requested.emit(profile_id),
                )
        menu.addAction("Ajouter un site…", self.add_requested.emit)
        menu.exec(self.list.viewport().mapToGlobal(point))
