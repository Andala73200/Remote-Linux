from __future__ import annotations

import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from app.core.async_task import run_async
from app.i18n import byte_units, tr


class BackupsWidget(QWidget):
    health_changed = Signal(str, str)

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.session = None
        self.status = QLabel("Déconnecté")
        self.status.setStyleSheet("font-size: 14pt; font-weight: 700;")
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.install_command = QLineEdit()
        self.install_command.setReadOnly(True)
        self.install_command.hide()
        self.copy_button = QPushButton("Copier la commande")
        self.copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.install_command.text())
        )
        self.copy_button.hide()
        self.refresh_button = QPushButton("🔄 Actualiser")
        self.refresh_button.clicked.connect(self.refresh)
        self.verify_button = QPushButton("Vérifier l’intégrité")
        self.verify_button.setToolTip(
            "Lance pgBackRest verify. Cette lecture peut être longue sur un gros dépôt."
        )
        self.verify_button.clicked.connect(self.verify)
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.verify_button)
        top.addWidget(self.refresh_button)
        command_row = QHBoxLayout()
        command_row.addWidget(self.install_command, 1)
        command_row.addWidget(self.copy_button)
        self.verification = QLabel("Vérification : —")
        self.verification.setWordWrap(True)
        self.history = QTableWidget(0, 8)
        self.history.setHorizontalHeaderLabels([
            "Stanza", "Type", "Identifiant", "Fin", "Durée", "Base",
            "Dépôt", "État",
        ])
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.horizontalHeader().setStretchLastSection(True)
        self.errors = QPlainTextEdit()
        self.errors.setReadOnly(True)
        self.errors.setMaximumHeight(120)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.summary)
        layout.addLayout(command_row)
        layout.addWidget(self.verification)
        layout.addWidget(self.history, 1)
        layout.addWidget(QLabel("Erreurs et remarques"))
        layout.addWidget(self.errors)
        self._set_connected(False)

    def set_session(self, session) -> None:
        self.session = session
        self.history.setRowCount(0)
        self.errors.clear()
        self._set_connected(bool(session))
        self._show_verification()
        if session:
            self.status.setText(tr('ui.ready_refreshes_when_the_tab_is_opened'))

    def _set_connected(self, connected: bool) -> None:
        self.refresh_button.setEnabled(connected)
        self.verify_button.setEnabled(False)
        if not connected:
            self.status.setText(tr('fragment.disconnected'))
            self.summary.setText("")
            self.install_command.hide()
            self.copy_button.hide()

    def refresh(self) -> None:
        if not self.session:
            return
        session = self.session
        self.refresh_button.setEnabled(False)
        self.status.setText(tr('ui.reading_pgbackrest'))
        run_async(
            session.backup_status,
            self._ready,
            self._failed,
            guard=lambda: self.session is session,
        )

    def _ready(self, result: object) -> None:
        self.refresh_button.setEnabled(True)
        data = dict(result or {})
        installed = bool(data.get("installed"))
        configured = bool(data.get("configured"))
        self.install_command.setVisible(not installed)
        self.copy_button.setVisible(not installed)
        self.verify_button.setEnabled(
            installed and configured and bool(data.get("stanza_names"))
        )
        if not installed:
            self.status.setText(tr('ui.pgbackrest_is_not_installed'))
            self.status.setStyleSheet("font-size:14pt;font-weight:700;color:#f2c866;")
            self.summary.setText(
                "Remote Linux ne peut pas conclure qu’aucune sauvegarde n’existe : "
                "seules les sauvegardes pgBackRest sont surveillées ici."
            )
            self.install_command.setText(str(data.get("install_command") or ""))
            self.history.setRowCount(0)
            self.errors.setPlainText(
                tr('ui.no_automatic_installation_will_be_performed')
            )
            self.health_changed.emit("unmonitored", "pgBackRest non installé")
            return
        if not configured:
            no_stanza = data.get("reason") == "no_stanza"
            self.status.setText(tr(
                'ui.pgbackrest_is_installed_no_stanza_configured'
                if no_stanza else
                'ui.pgbackrest_is_installed_but_unavailable_or_not_configured'
            ))
            self.status.setStyleSheet("font-size:14pt;font-weight:700;color:#f2c866;")
            self.summary.setText(tr(
                'ui.verification_is_unavailable_until_a_valid_stanza_is_declared'
                if no_stanza else
                'ui.check_the_stanzas_repository_and_postgres_user_permissions'
            ))
            error = data.get("error")
            self.errors.setPlainText(
                str(error) if error else tr('ui.configuration_not_found')
            )
            self.history.setRowCount(0)
            if no_stanza:
                self._discard_false_no_stanza_verification()
            self.health_changed.emit("warning", self.errors.toPlainText())
            return
        healthy = bool(data.get("healthy"))
        self.status.setText(tr('ui.backups_ok' if healthy else 'ui.backups_need_attention'))
        color = "#55d187" if healthy else "#ff6666"
        self.status.setStyleSheet(f"font-size:14pt;font-weight:700;color:{color};")
        last = data.get("last") or {}
        stanza_count = int(data.get("stanzas") or 0)
        stanza_label = tr('ui.stanza_627251' if stanza_count == 1 else 'ui.stanzas')
        self.summary.setText(
            f"{stanza_count} {stanza_label}  •  "
            f"{tr('fragment.latest_backup')} {last.get('date', '—')}  •  "
            f"{tr('fragment.database')} {self._bytes(int(last.get('database_size') or 0))}  •  "
            f"{tr('fragment.repository')} {self._bytes(int(last.get('repository_size') or 0))}"
        )
        self._fill_history(list(data.get("history") or []))
        errors = data.get("errors")
        self.errors.setPlainText(
            str(errors) if errors else tr('ui.no_errors_reported')
        )
        self.health_changed.emit("ok" if healthy else "error", self.errors.toPlainText())

    def _fill_history(self, rows: list[dict]) -> None:
        self.history.setRowCount(len(rows))
        labels = {"full": "Complète", "diff": "Différentielle", "incr": "Incrémentale"}
        for index, row in enumerate(rows):
            duration = max(0, int(row.get("stop_epoch") or 0) - int(row.get("start_epoch") or 0))
            values = [
                row.get("stanza", ""),
                tr(labels.get(str(row.get("type")), row.get("type", ""))),
                row.get("label", ""), row.get("date", "—"), f"{duration} s",
                self._bytes(int(row.get("database_size") or 0)),
                self._bytes(int(row.get("repository_size") or 0)),
                tr('ui.error') if row.get("error") else "OK",
            ]
            for column, value in enumerate(values):
                self.history.setItem(index, column, QTableWidgetItem(str(value)))
        self.history.resizeColumnsToContents()

    def verify(self) -> None:
        if not self.session or QMessageBox.question(
            self, "Vérifier les sauvegardes",
            "Lancer une vérification complète du dépôt pgBackRest ?\n\n"
            "L’opération est en lecture seule mais peut être longue."
        ) != QMessageBox.Yes:
            return
        session = self.session
        self.verify_button.setEnabled(False)
        self.verification.setText(tr('fragment.verification_in_progress'))
        run_async(
            session.backup_verify,
            self._verification_ready,
            self._verification_failed,
            guard=lambda: self.session is session,
        )

    def _verification_ready(self, result: object) -> None:
        code, output = result
        ok = int(code) == 0
        self._store_verification(ok, str(output))
        self.verify_button.setEnabled(True)
        self._show_verification()
        self.errors.setPlainText(
            str(output) or tr('ui.verification_succeeded' if ok else 'ui.failed')
        )
        if not ok:
            self.health_changed.emit("error", self.errors.toPlainText())

    def _verification_failed(self, text: str) -> None:
        self._store_verification(False, text)
        self.verify_button.setEnabled(bool(self.session))
        self._show_verification()

    def _store_verification(self, ok: bool, output: str) -> None:
        if not self.session:
            return
        records = self.storage.settings.setdefault("backup_verifications", {})
        if not isinstance(records, dict):
            records = {}
            self.storage.settings["backup_verifications"] = records
        records[self.session.profile.id] = {
            "ok": ok, "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "output": output[-4000:],
        }
        self.storage.save()

    def _show_verification(self) -> None:
        profile_id = self.session.profile.id if self.session else ""
        records = self.storage.settings.get("backup_verifications", {})
        record = records.get(profile_id, {}) if isinstance(records, dict) else {}
        if not record:
            self.verification.setText(tr('ui.integrity_verification_never_run_from_remote_linux'))
            return
        state = tr('fragment.succeeded' if record.get("ok") else 'fragment.failed_e6f749')
        self.verification.setText(
            f"{tr('fragment.last_verification')} {state} — {record.get('time', '—')}"
        )

    def _discard_false_no_stanza_verification(self) -> None:
        if not self.session:
            return
        records = self.storage.settings.get("backup_verifications", {})
        if not isinstance(records, dict):
            return
        record = records.get(self.session.profile.id, {})
        if (
            isinstance(record, dict) and not record.get("ok")
            and "aucune stanza" in str(record.get("output") or "").casefold()
        ):
            records.pop(self.session.profile.id, None)
            self.storage.save()
            self._show_verification()

    def _failed(self, text: str) -> None:
        self.refresh_button.setEnabled(bool(self.session))
        self.status.setText(tr('ui.unable_to_read'))
        self.errors.setPlainText(text)
        self.health_changed.emit("error", text)

    @staticmethod
    def _bytes(value: float) -> str:
        for unit in byte_units():
            if value < 1024 or unit in {"To", "TB"}:
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"0 {byte_units()[0]}"
