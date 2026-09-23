from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from app.core.async_task import run_async
from app.dialogs.preferences_dialog import PreferencesDialog
from app.i18n import set_language, tr
from app.dialogs.ssh_keys_dialog import SSHKeysDialog
from app.dialogs.sudo_password_dialog import SudoPasswordDialog


class MainSudoMixin:
    def _sudo_password(self, force_prompt: bool = False):
        if not self.session:
            return False
        profile = self.session.profile
        if self.session.sudo_password is not None and not force_prompt:
            return self.session.sudo_password

        stored = self.credentials.get_sudo_password(profile)
        if stored and not force_prompt:
            self.session.sudo_password = stored
            return stored

        default = ""
        if self.current_profile and self.current_profile.auth_method == "password":
            default = self.secret or ""
        save_securely = bool(
            self.storage.settings.get("save_sudo_password", False)
        )
        dialog = SudoPasswordDialog(
            profile.name,
            default_password=default,
            secure_available=self.credentials.is_available(),
            secure_checked=bool(stored or save_securely),
            parent=self,
        )
        if not dialog.exec():
            return False
        password = dialog.password()
        self.session.sudo_password = password if dialog.remember_for_session() else None
        if dialog.remember_on_pc():
            self.storage.settings["save_sudo_password"] = True
            self.storage.save()
            try:
                self.credentials.set_sudo_password(profile, password)
            except RuntimeError as exc:
                QMessageBox.warning(self, "Mot de passe sudo", str(exc))
        return password

    def unlock_sudo(self) -> None:
        session = self.session
        if not session:
            QMessageBox.information(self, "Sudo", "Aucun serveur connecté.")
            return
        password = self._sudo_password(force_prompt=True)
        if password is False:
            return
        self.statusBar().showMessage(tr('ui.checking_sudo_password'))
        run_async(
            lambda: session.validate_sudo(str(password)),
            lambda result: self._sudo_validation_done(session, result),
            lambda text: QMessageBox.critical(self, "Sudo", text),
            guard=lambda: self.session is session,
        )

    def _sudo_validation_done(self, session, result: object) -> None:
        if self.session is not session:
            return
        code, output = result
        if int(code) == 0:
            self.statusBar().showMessage(tr('ui.sudo_unlocked_for_this_session'))
            QMessageBox.information(self, "Sudo", "Le mot de passe sudo est valide.")
            return
        session.sudo_password = None
        self.credentials.delete_sudo_password(session.profile)
        self.statusBar().showMessage(tr('ui.sudo_authentication_failed'))
        details = str(output) or "Le mot de passe sudo est incorrect."
        QMessageBox.critical(
            self, "Sudo",
            f"{tr(details)}\n\n{tr('fragment.the_saved_password_has_been_deleted')}"
        )

    def _credential_profile(self):
        if self.session:
            return self.session.profile
        return self.current_profile or self.selected_profile()

    def forget_sudo_password(self, ask_confirmation: bool = True) -> bool:
        profile = self._credential_profile()
        if not profile:
            QMessageBox.information(self, "Mot de passe sudo", "Aucun profil sélectionné.")
            return False
        if ask_confirmation:
            text = f"Oublier le mot de passe sudo enregistré pour « {profile.name} » ?"
            if QMessageBox.question(self, "Mot de passe sudo", text) != QMessageBox.Yes:
                return False
        removed = self.credentials.delete_sudo_password(profile)
        if self.session and self.session.profile.id == profile.id:
            self.session.sudo_password = None
        if ask_confirmation:
            message = (
                "Le mot de passe sudo enregistré a été supprimé."
                if removed else "Aucun mot de passe sudo enregistré n’a été trouvé."
            )
            QMessageBox.information(self, "Mot de passe sudo", message)
        return removed

    def open_preferences(self) -> None:
        profile = self._credential_profile()
        saved = bool(self.credentials.get_sudo_password(profile))
        dialog = PreferencesDialog(
            self.storage.settings,
            profile_name=profile.name if profile else "",
            sudo_saved=saved,
            secure_available=self.credentials.is_available(),
            secure_backend=self.credentials.backend_name(),
            ssh_keys_available=bool(self.session),
            parent=self,
        )

        def forget_from_preferences() -> None:
            if self.forget_sudo_password(ask_confirmation=True):
                dialog.set_sudo_state(
                    profile.name if profile else "", False,
                    self.credentials.is_available(),
                )

        dialog.forget_sudo_requested.connect(forget_from_preferences)
        dialog.ssh_keys_requested.connect(
            lambda: SSHKeysDialog(self.session, dialog).exec()
            if self.session else None
        )
        if dialog.exec():
            values = dialog.values()
            save_requested = bool(values.get("save_sudo_password", False))
            self.storage.settings.update(values)
            self.storage.save()
            set_language(str(values.get("language", "auto")))
            if (
                save_requested
                and profile
                and not self.credentials.get_sudo_password(profile)
                and self.session
                and self.session.profile.id == profile.id
                and self.session.sudo_password
            ):
                try:
                    self.credentials.set_sudo_password(
                        profile, self.session.sudo_password
                    )
                except RuntimeError as exc:
                    QMessageBox.warning(self, "Mot de passe sudo", str(exc))
            self.system.apply_preferences()
            if hasattr(self, "_apply_notification_preferences"):
                self._apply_notification_preferences()
            if hasattr(self, "terminals"):
                self.terminals.apply_preferences(self.storage.settings)
