from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from app.i18n import ntr, tr


class SmartHealthPanel(QWidget):
    refresh_requested = Signal()
    health_changed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status = QLabel("Santé SMART non chargée")
        self.status.setWordWrap(True)
        self.refresh_button = QPushButton("Lire les données SMART")
        self.refresh_button.clicked.connect(self.refresh_requested)
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.refresh_button)
        self.install_command = QLineEdit()
        self.install_command.setReadOnly(True)
        self.install_command.hide()
        self.copy_button = QPushButton("Copier la commande")
        self.copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.install_command.text())
        )
        self.copy_button.hide()
        command_row = QHBoxLayout()
        command_row.addWidget(self.install_command, 1)
        command_row.addWidget(self.copy_button)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "Disque", "Modèle", "Série", "Santé", "Température", "Usure SSD",
            "Erreurs", "Heures", "Message",
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(command_row)
        layout.addWidget(self.table, 1)

    def set_loading(self, loading: bool) -> None:
        self.refresh_button.setEnabled(not loading)
        if loading:
            self.status.setText("Lecture SMART en cours…")

    def set_result(self, result: object) -> None:
        data = dict(result or {})
        installed = bool(data.get("installed"))
        self.install_command.setVisible(not installed)
        self.copy_button.setVisible(not installed)
        self.refresh_button.setEnabled(True)
        if not installed:
            self.status.setText("smartmontools n’est pas installé sur ce serveur.")
            self.install_command.setText(str(data.get("install_command") or ""))
            self.table.setRowCount(0)
            self.health_changed.emit("unavailable", "SMART non surveillé")
            return
        rows = list(data.get("devices") or [])
        self.table.setRowCount(len(rows))
        failures = 0
        unknown = 0
        for index, row in enumerate(rows):
            passed = row.get("passed")
            if passed is False:
                failures += 1
            elif passed is None:
                unknown += 1
            health = "OK" if passed is True else "ÉCHEC" if passed is False else "Inconnu"
            values = [
                row.get("path", ""), row.get("model", ""), row.get("serial", ""),
                health,
                "—" if row.get("temperature") is None else f"{row['temperature']} °C",
                "—" if row.get("wear") is None else f"{row['wear']} % utilisée",
                "—" if row.get("errors") is None else row.get("errors"),
                "—" if row.get("hours") is None else f"{row['hours']} h",
                row.get("message", ""),
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        privilege = tr('fragment.full_access' if data.get("privileged") else 'ui.limited_permissions')
        disk_label = ntr(len(rows), 'ui.disk_analyzed', 'ui.disks_analyzed')
        failure_label = ntr(failures, 'fragment.failed', 'fragment.failed')
        unknown_label = ntr(unknown, 'ui.unknown_status', 'ui.unknown_statuses')
        self.status.setText(
            f"{len(rows)} {disk_label}  •  {failures} {failure_label}  •  "
            f"{unknown} {unknown_label}  •  {privilege}"
        )
        state = "error" if failures else "warning" if unknown else "ok" if rows else "unavailable"
        self.health_changed.emit(state, self.status.text())

    def set_error(self, text: str) -> None:
        self.refresh_button.setEnabled(True)
        self.status.setText(f"{tr('ui.unable_to_read_smart_data')} {text}")
        self.health_changed.emit("error", text)
