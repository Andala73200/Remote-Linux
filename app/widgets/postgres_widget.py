from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.core.async_task import run_async
from app.i18n import byte_units, tr


class PostgresWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.status = QLabel("Déconnecté")
        self.status.setStyleSheet("font-size:14pt;font-weight:700;")
        self.refresh_button = QPushButton("🔄 Actualiser")
        self.refresh_button.clicked.connect(self.refresh)
        self.install_command = QLineEdit()
        self.install_command.setReadOnly(True)
        self.install_command.hide()
        self.copy_button = QPushButton("Copier la commande")
        self.copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.install_command.text())
        )
        self.copy_button.hide()
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.refresh_button)
        command_row = QHBoxLayout()
        command_row.addWidget(self.install_command, 1)
        command_row.addWidget(self.copy_button)
        self.pages = QTabWidget()
        self.databases = self._table([
            "Base", "Taille", "Connexions", "Commits", "Rollbacks", "Cache hit",
        ])
        database_page = QWidget()
        database_layout = QVBoxLayout(database_page)
        database_layout.addWidget(self.databases)
        self.load_tables_button = QPushButton("Afficher les tables de la base sélectionnée")
        self.load_tables_button.clicked.connect(self.load_tables)
        database_layout.addWidget(self.load_tables_button)
        self.activity = self._table([
            "Base", "Utilisateur", "État", "Client", "Attente", "Durée", "Requête",
        ])
        self.tables = self._table([
            "Schéma", "Table", "Taille", "Lignes", "Mortes", "Seq scans",
            "Index scans", "Dernier autovacuum", "Dernière analyse",
        ])
        self.pages.addTab(database_page, "Bases")
        self.pages.addTab(self.activity, "Connexions / activité")
        self.pages.addTab(self.tables, "Tables importantes")
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(command_row)
        layout.addWidget(self.pages, 1)
        self._enable(False)

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def set_session(self, session) -> None:
        self.session = session
        for table in (self.databases, self.activity, self.tables):
            table.setRowCount(0)
        self._enable(bool(session))
        self.status.setText(tr('fragment.disconnected' if not session else 'ui.reading_postgresql'))
        if session:
            self.status.setText(tr('ui.ready_read_only_diagnostics'))

    def _enable(self, enabled: bool) -> None:
        self.refresh_button.setEnabled(enabled)
        self.load_tables_button.setEnabled(False)

    def refresh(self) -> None:
        if not self.session:
            return
        session = self.session
        self.refresh_button.setEnabled(False)
        self.status.setText(tr('ui.reading_postgresql'))
        run_async(
            session.postgres_overview,
            self._ready,
            self._failed,
            guard=lambda: self.session is session,
        )

    def _ready(self, result: object) -> None:
        self.refresh_button.setEnabled(True)
        data = dict(result or {})
        installed = bool(data.get("installed"))
        self.install_command.setVisible(not installed)
        self.copy_button.setVisible(not installed)
        if not installed:
            self.status.setText(tr('ui.postgresql_client_not_installed'))
            self.install_command.setText(str(data.get("install_command") or ""))
            self._clear_tables()
            return
        if not data.get("accessible"):
            error = str(data.get("error") or tr('ui.insufficient_permissions'))
            self.status.setText(
                f"{tr('ui.postgresql_detected_diagnostics_unavailable')} {error}"
            )
            self._clear_tables()
            return
        databases = list(data.get("databases") or [])
        activity = list(data.get("activity") or [])
        database_count = len(databases)
        connection_count = len(activity)
        database_label = tr('ui.database_1405df' if database_count == 1 else 'ui.databases_90fb6e')
        connection_label = tr(
            'ui.observed_connection' if connection_count == 1 else 'ui.observed_connections'
        )
        self.status.setText(
            f"PostgreSQL {data.get('version', '')}  •  "
            f"{database_count} {database_label}  •  "
            f"{connection_count} {connection_label}"
        )
        self._fill_databases(databases)
        self._fill_activity(activity)
        self.load_tables_button.setEnabled(bool(databases))

    def _fill_databases(self, rows: list[dict]) -> None:
        self.databases.setRowCount(len(rows))
        for index, row in enumerate(rows):
            read = int(row.get("blocks_read") or 0)
            hit = int(row.get("blocks_hit") or 0)
            cache = 0 if read + hit == 0 else hit * 100 / (read + hit)
            values = [
                row.get("name", ""), self._bytes(int(row.get("size") or 0)),
                row.get("connections", 0), row.get("commits", 0),
                row.get("rollbacks", 0), f"{cache:.1f} %",
            ]
            for column, value in enumerate(values):
                self.databases.setItem(index, column, QTableWidgetItem(str(value)))
        self.databases.resizeColumnsToContents()
        if rows:
            self.databases.selectRow(0)

    def _fill_activity(self, rows: list[dict]) -> None:
        self.activity.setRowCount(len(rows))
        keys = ("database", "user", "state", "client", "wait", "seconds", "query")
        for index, row in enumerate(rows):
            for column, key in enumerate(keys):
                value = row.get(key, "")
                if key == "seconds":
                    value = f"{value} s"
                self.activity.setItem(index, column, QTableWidgetItem(str(value)))
        self.activity.resizeColumnsToContents()

    def load_tables(self) -> None:
        row = self.databases.currentRow()
        item = self.databases.item(row, 0) if row >= 0 else None
        if not self.session or not item:
            return
        database = item.text()
        session = self.session
        self.load_tables_button.setEnabled(False)
        self.status.setText(f"{tr('fragment.reading_tables_from')}{database}…")
        run_async(
            lambda: session.postgres_tables(database),
            lambda rows: self._tables_ready(database, rows),
            self._failed,
            guard=lambda: self.session is session,
        )

    def _tables_ready(self, database: str, value: object) -> None:
        rows = list(value or [])
        self.tables.setRowCount(len(rows))
        keys = (
            "schema", "name", "size", "live", "dead", "seq_scan", "idx_scan",
            "vacuum", "analyze",
        )
        for index, row in enumerate(rows):
            for column, key in enumerate(keys):
                cell = self._bytes(int(row.get(key) or 0)) if key == "size" else row.get(key, "")
                self.tables.setItem(index, column, QTableWidgetItem(str(cell)))
        self.tables.resizeColumnsToContents()
        self.load_tables_button.setEnabled(True)
        table_count = len(rows)
        table_label = tr('ui.user_table' if table_count == 1 else 'ui.user_tables')
        self.status.setText(f"{database} — {table_count} {table_label}")
        self.pages.setCurrentWidget(self.tables)

    def _failed(self, text: str) -> None:
        self.refresh_button.setEnabled(bool(self.session))
        self.load_tables_button.setEnabled(bool(self.session and self.databases.rowCount()))
        self.status.setText(f"{tr('fragment.postgresql_diagnostics_failed')} {text}")

    def _clear_tables(self) -> None:
        for table in (self.databases, self.activity, self.tables):
            table.setRowCount(0)
        self.load_tables_button.setEnabled(False)

    @staticmethod
    def _bytes(value: float) -> str:
        for unit in byte_units():
            if value < 1024 or unit in {"To", "TB"}:
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"0 {byte_units()[0]}"
