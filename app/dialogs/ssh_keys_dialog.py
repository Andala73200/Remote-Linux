from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from PySide6.QtCore import Qt

from app.core.async_task import run_async
from app.i18n import ntr, tr


class SSHKeysDialog(QDialog):
    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.busy = False
        self.setWindowTitle("Clés SSH autorisées")
        self.resize(900, 520)
        info = QLabel(
            f"{tr('ui.authorized_keys_for')} {session.profile.user}@{session.profile.name}. "
            f"{tr('ui.deleting_a_key_may_prevent_the_next_ssh_connection')}"
        )
        info.setWordWrap(True)
        self.status = QLabel("Lecture de ~/.ssh/authorized_keys…")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Type", "Empreinte", "Commentaire", "Ligne"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.add_button = QPushButton("Ajouter une clé publique…")
        self.add_button.clicked.connect(self.add_key)
        self.remove_button = QPushButton("Supprimer la clé sélectionnée…")
        self.remove_button.clicked.connect(self.remove_key)
        self.remove_button.setEnabled(False)
        self.table.itemSelectionChanged.connect(self._update_remove_enabled)
        actions = QHBoxLayout()
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.close_button = buttons.button(QDialogButtonBox.Close)
        self.close_button.setText("Fermer")
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.status)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        session = self.session
        self._set_busy(True)
        run_async(
            session.ssh_authorized_keys, self._ready, self._failed,
            guard=lambda: self.session is session,
        )

    def _ready(self, value: object) -> None:
        rows = list(value or [])
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = [row.get("type", ""), row.get("fingerprint", ""), row.get("comment", ""), row.get("line", "")]
            for column, cell in enumerate(values):
                item = QTableWidgetItem(str(cell))
                if column == 1:
                    item.setData(Qt.UserRole, row.get("fingerprint", ""))
                self.table.setItem(index, column, item)
        self.table.resizeColumnsToContents()
        key_label = ntr(len(rows), 'ui.authorized_key', 'ui.authorized_keys')
        self.status.setText(f"{len(rows)} {key_label}.")
        self._set_busy(False)

    def add_key(self) -> None:
        text, ok = QInputDialog.getMultiLineText(
            self, "Ajouter une clé SSH", "Colle une clé publique OpenSSH complète :"
        )
        if not ok or not text.strip():
            return
        session = self.session
        self.status.setText("Ajout de la clé…")
        self._set_busy(True)
        run_async(
            lambda: session.add_ssh_authorized_key(text),
            lambda fingerprint: self._changed(f"Clé ajoutée : {fingerprint}"),
            self._failed,
            guard=lambda: self.session is session,
        )

    def remove_key(self) -> None:
        row = self.table.currentRow()
        item = self.table.item(row, 1) if row >= 0 else None
        fingerprint = str(item.data(Qt.UserRole) or "") if item else ""
        if not fingerprint or QMessageBox.question(
            self, tr('ui.delete_ssh_key'),
            f"{tr('ui.permanently_delete_this_key')}\n\n{fingerprint}\n\n"
            f"{tr('ui.ensure_another_connection_method_remains_available')}"
        ) != QMessageBox.Yes:
            return
        session = self.session
        self.status.setText("Suppression de la clé…")
        self._set_busy(True)
        run_async(
            lambda: session.remove_ssh_authorized_key(fingerprint),
            lambda _value: self._changed("Clé supprimée."), self._failed,
            guard=lambda: self.session is session,
        )

    def _changed(self, message: str) -> None:
        self.status.setText(message)
        self.refresh()

    def _failed(self, text: str) -> None:
        self.status.setText(f"{tr('ui.operation_failed')} {text}")
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.add_button.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        self._update_remove_enabled()

    def _update_remove_enabled(self) -> None:
        self.remove_button.setEnabled(not self.busy and self.table.currentRow() >= 0)

    def reject(self) -> None:
        if self.busy:
            QMessageBox.information(
                self, "Opération en cours",
                "Attends la fin de l’opération sur authorized_keys avant de fermer."
            )
            return
        super().reject()
