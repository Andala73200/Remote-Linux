from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QCheckBox, QMessageBox

from app.core.async_task import run_async
from app.dialogs.package_manager_dialog import PackageManagerDialog
from app.dialogs.server_health_dialog import ServerHealthDialog
from app.dialogs.users_dialog import UsersDialog
from app.i18n import tr


class MainObservabilityMixin:
    def _main_tab_changed(self, _index: int) -> None:
        if not self.session:
            return
        current = self.tabs.currentWidget()
        if current in {
            self.backups, self.postgres, self.network_security, self.tasks
        }:
            current.refresh()

    def _load_identity(self, session) -> None:
        run_async(
            session.identity_info,
            lambda info: self._identity_checked(session, info),
            lambda _text: self._identity_unavailable(session),
            guard=lambda: self.session is session,
        )

    def _identity_checked(self, session, value: object) -> None:
        if self.session is not session:
            return
        info = dict(value or {})
        user = str(info.get("user") or session.profile.user or "—")
        level = str(info.get("level") or "user")
        self.identity_button.setText(f"👤 {user} · {level}")
        self.identity_button.setToolTip(
            f"{tr('ui.ssh_user')} {user}\n"
            f"{tr('ui.detected_privilege_level')} {level}\n"
            f"{tr('ui.click_to_view_linux_users_and_groups')}"
        )
        self.identity_button.setEnabled(True)

    def _identity_unavailable(self, session) -> None:
        if self.session is session:
            self.identity_button.setText(f"👤 {session.profile.user} · ?")
            self.identity_button.setEnabled(True)

    def _open_users(self) -> None:
        if not self.session:
            QMessageBox.information(self, "Utilisateurs", "Aucun serveur connecté.")
            return
        UsersDialog(self.session, self).exec()

    def _open_package_manager(self, manager: str, terminal=None) -> None:
        if not self.session or (terminal and terminal.session is not self.session):
            return
        PackageManagerDialog(
            self.session,
            manager,
            self._sudo_password,
            parent=self,
            venv_path=str(getattr(terminal, "virtual_env", "") or ""),
            storage=self.storage,
        ).exec()
        target = terminal or self.terminals.active_terminal()
        target.console.setFocus()

    def _offer_venv_activation(self, terminal, venv_path: str) -> None:
        if (
            not self.session
            or terminal.session is not self.session
            or not bool(
                self.storage.settings.get("terminal_venv_prompt_enabled", True)
            )
        ):
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr('ui.venv_detected'))
        box.setTextFormat(Qt.PlainText)
        box.setText(tr('ui.activate_detected_venv'))
        box.setInformativeText(str(venv_path))
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        disable = QCheckBox(tr('ui.do_not_ask_again'))
        box.setCheckBox(disable)
        answer = box.exec()
        if disable.isChecked():
            self.storage.settings["terminal_venv_prompt_enabled"] = False
            self.storage.save()
            self.terminals.apply_preferences(self.storage.settings)
        if answer == QMessageBox.Yes and terminal.session is self.session:
            terminal.activate_project_venv(str(venv_path))

    def _backup_health_changed(self, state: str, message: str) -> None:
        if state in {"error", "warning"}:
            self.alerts.add_alert(
                "backups", "Sauvegardes à vérifier", message
            )
        else:
            self.alerts.remove_alert("backups")

    def _schedule_health_popup(self, session) -> None:
        if not bool(getattr(session.profile, "show_health_popup", True)):
            run_async(
                session.backup_status,
                self._background_backup_ready,
                lambda text: self._backup_health_changed("error", text),
                guard=lambda: self.session is session,
            )
            return
        QTimer.singleShot(1200, lambda: self._load_health_popup(session))

    def _load_health_popup(self, session) -> None:
        if self.session is not session:
            return
        run_async(
            session.health_summary,
            lambda data: self._health_ready(session, data),
            lambda text: self.statusBar().showMessage(
                f"{tr('ui.health_summary_unavailable')} {text}"
            ),
            guard=lambda: self.session is session,
        )

    def _health_ready(self, session, value: object) -> None:
        if self.session is not session:
            return
        data = dict(value or {})
        backups = dict(data.get("backups") or {})
        self._background_backup_ready(backups)
        dialog = ServerHealthDialog(
            session.profile.name, data, len(self.alerts.alerts), self
        )
        dialog.exec()
        if dialog.disable.isChecked():
            session.profile.show_health_popup = False
            self.storage.save()

    def _background_backup_ready(self, value: object) -> None:
        data = dict(value or {})
        if not data.get("installed"):
            self._backup_health_changed("unmonitored", "pgBackRest non installé")
        elif not data.get("configured"):
            self._backup_health_changed(
                "warning", str(data.get("error") or "pgBackRest non configuré")
            )
        elif not data.get("healthy"):
            self._backup_health_changed(
                "error", str(data.get("errors") or data.get("error") or "État incorrect")
            )
        else:
            self._backup_health_changed("ok", "Sauvegardes OK")

    def _reactivate_health_popup(self, profile_id: str) -> None:
        profile = next(
            (item for item in self.storage.profiles if item.id == profile_id), None
        )
        if not profile:
            return
        profile.show_health_popup = True
        self.storage.save()
        self.statusBar().showMessage(
            f"{tr('ui.the_health_summary_will_be_shown_on_the_next')} "
            f"{profile.name}."
        )
