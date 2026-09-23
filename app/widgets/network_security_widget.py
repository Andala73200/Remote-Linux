from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from app.core.async_task import run_async
from app.i18n import ntr, tr
from app.widgets.table_tools import Cell, FilteredTablePage, date_sort_value


class NetworkSecurityWidget(QWidget):
    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.session = None
        self.port_rows: list[dict] = []
        self.status = QLabel("Déconnecté")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("font-size:12pt;font-weight:600;")
        self.refresh_button = QPushButton("🔄 Actualiser")
        self.refresh_button.clicked.connect(self.refresh)
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.refresh_button)
        self.pages = QTabWidget()
        self.ports = self._build_ports_page()
        self.firewall = self._build_firewall_page()
        self.ssh = self._build_ssh_page()
        self.pages.addTab(self.ports, "Ports en écoute")
        self.pages.addTab(self.firewall, "Pare-feu")
        self.pages.addTab(self.ssh, "SSH / sécurité")
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.pages, 1)
        self.refresh_button.setEnabled(False)

    def _build_ports_page(self) -> QWidget:
        page = QWidget()
        self.favorite_only = QPushButton("★ Favoris uniquement")
        self.favorite_only.setCheckable(True)
        self.favorite_only.toggled.connect(self._apply_port_filters)
        controls = QHBoxLayout()
        controls.addWidget(self.favorite_only)
        controls.addStretch(1)
        self.port_table = FilteredTablePage([
            "★", "Protocole", "Adresse", "Port", "Processus", "PID", "Portée locale",
        ], skip_filters={0}, default_sort=3)
        self.port_table.table.cellClicked.connect(self._port_clicked)
        self.port_table.set_extra_predicate(self._port_favorite_predicate)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        layout.addWidget(self.port_table, 1)
        return page

    def _build_firewall_page(self) -> QWidget:
        pages = QTabWidget()
        self.firewall_summary = FilteredTablePage(
            ["Paramètre", "Valeur"], default_sort=0
        )
        self.firewall_rules = FilteredTablePage([
            "Port / service", "Protocole", "Action", "Source", "IP",
        ], default_sort=0)
        pages.addTab(self.firewall_summary, "Résumé")
        pages.addTab(self.firewall_rules, "Règles")
        return pages

    def _build_ssh_page(self) -> QWidget:
        pages = QTabWidget()
        self.connected_table = FilteredTablePage([
            "Utilisateur", "Terminal", "Connexion", "Adresse",
        ], default_sort=2)
        history_headers = [
            "Utilisateur", "Terminal", "Adresse", "Début", "Fin", "Durée", "État",
        ]
        self.recent_table = FilteredTablePage(history_headers, default_sort=3)
        self.failed_table = FilteredTablePage(history_headers, default_sort=3)
        self.sshd_table = FilteredTablePage(
            ["Paramètre SSH", "Valeur effective"], default_sort=0
        )
        self.fail2ban_table = FilteredTablePage(
            ["Paramètre Fail2ban", "Valeur"], default_sort=0
        )
        for widget, title in (
            (self.connected_table, "Connectés"),
            (self.recent_table, "Historique"),
            (self.failed_table, "Échecs"),
            (self.sshd_table, "Configuration SSH"),
            (self.fail2ban_table, "Fail2ban"),
        ):
            pages.addTab(widget, title)
        return pages

    def set_session(self, session) -> None:
        self.session = session
        self.port_rows = []
        for page in self._table_pages():
            page.fill([])
        self.refresh_button.setEnabled(bool(session))
        self.status.setText(
            "Déconnecté" if not session else "Prêt — lecture réseau et sécurité"
        )

    def refresh(self) -> None:
        if not self.session:
            return
        session = self.session
        self.refresh_button.setEnabled(False)
        self.status.setText("Lecture des ports, du pare-feu et des connexions SSH…")
        run_async(
            session.network_security, self._ready, self._failed,
            guard=lambda: self.session is session,
        )

    def _ready(self, result: object) -> None:
        data = dict(result or {})
        self.refresh_button.setEnabled(True)
        self.port_rows = list(data.get("ports") or [])
        self._fill_ports()
        self._fill_firewall(dict(data.get("firewall") or {}))
        self._fill_ssh(data)
        privilege = (
            "Lecture privilégiée active." if data.get("privileged") else
            "Lecture partielle : certains PID, échecs ou règles peuvent être masqués."
        )
        port_label = ntr(len(self.port_rows), 'ui.listening_port', 'ui.listening_ports_10fb63')
        self.status.setText(f"{len(self.port_rows)} {port_label}  •  {tr(privilege)}")

    def _fill_ports(self) -> None:
        favorites = self._favorites()
        rows = []
        for row in self.port_rows:
            key = self._port_key(row)
            port, pid = str(row.get("port", "")), str(row.get("pid", ""))
            rows.append([
                Cell("★" if key in favorites else "☆", 0 if key in favorites else 1, key),
                row.get("protocol", ""), row.get("address", ""),
                Cell(port, int(port) if port.isdigit() else -1),
                row.get("process", ""),
                Cell(pid, int(pid) if pid.isdigit() else -1),
                Cell(row.get("scope", ""), translate=True),
            ])
        self.port_table.fill(rows)
        self._apply_port_filters()

    def _fill_firewall(self, firewall: dict) -> None:
        self.firewall_summary.fill([
            [Cell(row.get("parameter", ""), translate=True), row.get("value", "")]
            for row in firewall.get("summary") or []
        ])
        self.firewall_rules.fill([
            [row.get(key, "") for key in ("target", "protocol", "action", "source", "ip")]
            for row in firewall.get("rules") or []
        ])

    def _fill_ssh(self, data: dict) -> None:
        self.connected_table.fill([
            [row.get("user", ""), row.get("terminal", ""),
             Cell(row.get("date", ""), date_sort_value(row.get("date", ""))),
             row.get("source", "")]
            for row in data.get("connected") or []
        ])
        for page, source in (
            (self.recent_table, data.get("recent") or []),
            (self.failed_table, data.get("failed") or []),
        ):
            page.fill([
                [row.get("user", ""), row.get("terminal", ""), row.get("source", ""),
                 Cell(row.get("start", ""), date_sort_value(row.get("start", ""))),
                 Cell(row.get("end", ""), date_sort_value(row.get("end", ""))),
                 row.get("duration", ""),
                 Cell(row.get("status", ""), translate=True)]
                for row in source
            ])
        for page, source in (
            (self.sshd_table, data.get("sshd") or []),
            (self.fail2ban_table, data.get("fail2ban") or []),
        ):
            page.fill([
                [row.get("parameter", ""), row.get("value", "")] for row in source
            ])

    def _port_clicked(self, row: int, column: int) -> None:
        if column != 0 or not self.session:
            return
        item = self.port_table.table.item(row, 0)
        key = str(item.data(Qt.UserRole) or "") if item else ""
        if not key:
            return
        favorites = self._favorites()
        favorites.remove(key) if key in favorites else favorites.add(key)
        records = self.storage.settings.get("network_port_favorites", {})
        if not isinstance(records, dict):
            records = {}
            self.storage.settings["network_port_favorites"] = records
        records[self.session.profile.id] = sorted(favorites)
        self.storage.save()
        self._fill_ports()

    def _favorites(self) -> set[str]:
        records = self.storage.settings.get("network_port_favorites", {})
        profile_id = self.session.profile.id if self.session else ""
        values = records.get(profile_id, []) if isinstance(records, dict) else []
        return {str(value) for value in values} if isinstance(values, list) else set()

    def _port_favorite_predicate(self, row: int) -> bool:
        if not self.favorite_only.isChecked():
            return True
        item = self.port_table.table.item(row, 0)
        return bool(item and item.text() == "★")

    def _apply_port_filters(self) -> None:
        if self.port_table.filter_bar:
            self.port_table.filter_bar.apply()

    @staticmethod
    def _port_key(row: dict) -> str:
        return "|".join(str(row.get(key, "")) for key in (
            "protocol", "address", "port", "process"
        ))

    def _table_pages(self) -> tuple[FilteredTablePage, ...]:
        return (
            self.port_table, self.firewall_summary, self.firewall_rules,
            self.connected_table, self.recent_table, self.failed_table,
            self.sshd_table, self.fail2ban_table,
        )

    def _failed(self, text: str) -> None:
        self.refresh_button.setEnabled(bool(self.session))
        self.status.setText(f"Diagnostic réseau impossible : {text}")
