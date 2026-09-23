from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QLabel,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
)

from app.core.async_task import run_async
from app.i18n import ntr, tr


class UsersDialog(QDialog):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.users_data: list[dict] = []
        self.groups_data: list[dict] = []
        self.setWindowTitle("Utilisateurs et groupes Linux")
        self.resize(1050, 650)
        self.status = QLabel("Lecture des comptes…")
        self.show_system = QCheckBox("Afficher les comptes et groupes système")
        self.show_system.toggled.connect(self._rebuild)
        self.pages = QTabWidget()
        self.users = self._table([
            "Utilisateur", "UID", "GID", "Dossier personnel", "Shell", "Groupes",
        ])
        self.groups = self._table(["Groupe", "GID", "Membres explicites"])
        self.pages.addTab(self.users, "Utilisateurs")
        self.pages.addTab(self.groups, "Groupes")
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Fermer")
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.show_system)
        layout.addWidget(self.pages, 1)
        layout.addWidget(buttons)
        run_async(
            session.linux_accounts,
            self._ready,
            self._failed,
            guard=lambda: self.session is session,
        )

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _ready(self, result: object) -> None:
        data = dict(result or {})
        self.users_data = list(data.get("users") or [])
        self.groups_data = list(data.get("groups") or [])
        user_label = ntr(len(self.users_data), 'ui.user_d3961a', 'ui.users_f51181')
        group_label = ntr(len(self.groups_data), 'ui.group_0c52dc', 'ui.groups_c196f3')
        self.status.setText(
            f"{len(self.users_data)} {user_label}  •  "
            f"{len(self.groups_data)} {group_label}  •  UID_MIN = {data.get('uid_min', '—')}"
        )
        self._rebuild()

    def _failed(self, text: str) -> None:
        self.status.setText(f"{tr('fragment.read_failed')} {text}")

    def _rebuild(self) -> None:
        show_system = self.show_system.isChecked()
        users = [row for row in self.users_data if show_system or not row.get("system")]
        groups = [row for row in self.groups_data if show_system or not row.get("system")]
        self.users.setRowCount(len(users))
        for index, row in enumerate(users):
            values = [
                row.get("name", ""), row.get("uid", ""), row.get("gid", ""),
                row.get("home", ""), row.get("shell", ""),
                ", ".join(row.get("groups") or []),
            ]
            for column, value in enumerate(values):
                self.users.setItem(index, column, QTableWidgetItem(str(value)))
        self.users.resizeColumnsToContents()
        self.groups.setRowCount(len(groups))
        for index, row in enumerate(groups):
            values = [
                row.get("name", ""), row.get("gid", ""),
                ", ".join(row.get("members") or []),
            ]
            for column, value in enumerate(values):
                self.groups.setItem(index, column, QTableWidgetItem(str(value)))
        self.groups.resizeColumnsToContents()
