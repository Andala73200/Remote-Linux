from PySide6.QtWidgets import QMessageBox

from app.core.async_task import run_async
from app.dialogs.delete_service_dialog import DeleteServiceDialog
from app.i18n import tr


class ServiceDeleteMixin:
    def _prepare_delete(self, service: str) -> None:
        session = self.session
        if not session:
            return
        run_async(
            lambda: session.service_details(service),
            lambda details: self._confirm_delete(session, details),
            self._show_error,
            guard=lambda: self.session is session,
        )

    def _confirm_delete(self, session, details: object) -> None:
        if self.session is not session:
            return
        info = dict(details)
        if bool(info.get("generated")):
            QMessageBox.warning(
                self, "Suppression impossible",
                "Ce service est généré dynamiquement dans /run/systemd et ne peut pas être supprimé ici.",
            )
            return
        if not info.get("path"):
            QMessageBox.warning(
                self, "Suppression impossible",
                "Ce service ne possède pas de fichier d’unité supprimable.",
            )
            return
        allow_nonlocal = bool(
            self.storage.settings.get("allow_delete_nonlocal_services", False)
        )
        if not bool(info.get("local")) and not allow_nonlocal:
            QMessageBox.warning(
                self, "Service protégé",
                "Ce service n’a pas été créé localement.\n\n"
                "Active l’option correspondante dans Préférences → Services systemd "
                "pour autoriser sa suppression.",
            )
            return
        dialog = DeleteServiceDialog(info, self)
        if not dialog.exec():
            return
        password = self.sudo_password_provider(False) if self.sudo_password_provider else None
        if password is False:
            return
        self._delete_execute(
            session,
            str(info["service"]),
            password,
            allow_nonlocal,
            dialog.remove_package(),
        )

    def _delete_execute(
        self,
        session,
        service: str,
        password: str | None,
        allow_nonlocal: bool,
        remove_package: bool,
    ) -> None:
        def done(result: object) -> None:
            code, output = result
            if int(code) == 0:
                QMessageBox.information(
                    self, tr('ui.service_deleted'),
                    str(output) or f"{service} {tr('ui.was_deleted')}"
                )
                self.refresh()
                return
            lowered = str(output).lower()
            if "password" in lowered or "mot de passe" in lowered or "authentication" in lowered:
                if self.sudo_password_provider:
                    entered = self.sudo_password_provider(True)
                    if entered is not False and entered:
                        self._delete_execute(
                            session,
                            service,
                            str(entered),
                            allow_nonlocal,
                            remove_package,
                        )
                        return
            self._show_error(str(output) or f"{tr('ui.deletion_failed_code')} {code}.")

        run_async(
            lambda: session.delete_service(
                service,
                password,
                allow_nonlocal,
                remove_package,
            ),
            done,
            self._show_error,
            guard=lambda: self.session is session,
        )
