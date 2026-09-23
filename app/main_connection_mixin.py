from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMessageBox

from app.core.async_task import run_async
from app.core.connection_cleanup import purge_known_hosts
from app.core.connection_worker import ConnectionWorker
from app.core.distribution import distribution_label, normalize_distribution
from app.dialogs.profile_dialog import ProfileDialog
from app.i18n import ntr, tr


class ConnectionMixin:
    def selected_profile(self):
        profile_id = self.profiles.selected_profile_id()
        return next((profile for profile in self.storage.profiles if profile.id == profile_id), None)

    def add_profile(self) -> None:
        dialog = ProfileDialog(credentials=self.credentials, parent=self)
        if not dialog.exec():
            return
        profile = dialog.result_profile()
        cloudflare_secret = dialog.cloudflare_secret()
        if cloudflare_secret:
            try:
                self.credentials.set_cloudflare_secret(profile, cloudflare_secret)
            except RuntimeError as exc:
                QMessageBox.critical(self, "Jeton Cloudflare", str(exc))
                return
        self.storage.profiles.append(profile)
        self.storage.save()
        self._reload_profiles()
        self.profiles.select_profile(profile.id)

    def edit_profile(self) -> None:
        profile = self.selected_profile()
        if not profile:
            return
        dialog = ProfileDialog(profile, self.credentials, self)
        if not dialog.exec():
            return
        dialog.result_profile()
        cloudflare_secret = dialog.cloudflare_secret()
        if cloudflare_secret:
            try:
                self.credentials.set_cloudflare_secret(profile, cloudflare_secret)
            except RuntimeError as exc:
                QMessageBox.critical(self, "Jeton Cloudflare", str(exc))
                return
        self.storage.save()
        self._reload_profiles()
        self.profiles.select_profile(profile.id)

    def delete_profile(self) -> None:
        profile = self.selected_profile()
        if profile and QMessageBox.question(
            self, tr('ui.delete'), f"{tr('ui.delete')} « {profile.name} » ?"
        ) == QMessageBox.Yes:
            cleanup_error = self._purge_profile_artifacts(profile, remove_profile=True)
            self._reload_profiles()
            if cleanup_error:
                QMessageBox.warning(self, tr('ui.warnings'), cleanup_error)

    def purge_profile_connection_data(self) -> None:
        profile = self.selected_profile()
        if not profile:
            return
        if self.session and self.session.profile.id == profile.id:
            QMessageBox.warning(
                self, tr('ui.purge_connection_data'),
                tr('ui.disconnect_before_purging_connection_data'),
            )
            return
        text = (
            f"{tr('ui.purge_connection_data_for')} « {profile.name} » ?\n\n"
            f"{tr('ui.profile_will_be_kept_connection_data_removed')}"
        )
        if QMessageBox.question(
            self, tr('ui.purge_connection_data'), text,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        cleanup_error = self._purge_profile_artifacts(profile, remove_profile=False)
        self._reload_profiles()
        self.profiles.select_profile(profile.id)
        if cleanup_error:
            QMessageBox.warning(self, tr('ui.warnings'), cleanup_error)
        else:
            QMessageBox.information(
                self, tr('ui.purge_connection_data'),
                tr('ui.connection_data_purged'),
            )

    def _purge_profile_artifacts(self, profile, remove_profile: bool) -> str:
        self.credentials.delete_sudo_password(profile)
        self.credentials.delete_cloudflare_secret(profile)
        self.credentials.delete_cloudflare_user_token(profile)
        cleanup_error = ""
        try:
            purge_known_hosts(profile)
        except OSError as exc:
            cleanup_error = f"{tr('ui.known_hosts_cleanup_failed')} {exc}"
        if remove_profile:
            self.storage.remove_profile_data(profile.id)
        else:
            self.storage.purge_profile_data(profile.id)
        return cleanup_error

    def connect_selected(self) -> None:
        profile = self.selected_profile()
        if not profile or self.connection_worker:
            return
        secret = self._ask_secret(profile)
        if secret is False:
            return
        cloudflare_secret = None
        cloudflare_user_token = None
        if profile.kind == "cloudflare":
            if profile.cloudflare_auth_mode == "user_login":
                cloudflare_user_token = self.credentials.get_cloudflare_user_token(profile)
            else:
                if not (profile.cloudflare_client_id or "").strip():
                    QMessageBox.warning(
                        self,
                        "Jeton Cloudflare manquant",
                        "Aucun Client ID n'est renseigné pour ce profil.",
                    )
                    return
                cloudflare_secret = self.credentials.get_cloudflare_secret(profile)
                if not cloudflare_secret:
                    QMessageBox.warning(
                        self,
                        "Jeton Cloudflare manquant",
                        "Aucun Client Secret n'est enregistré pour ce profil.",
                    )
                    return
        self.current_profile = profile
        self.secret = secret
        self.cloudflare_secret = cloudflare_secret
        self.cloudflare_user_token = cloudflare_user_token
        self.manual_disconnect = False
        self.reconnect_attempts = 0
        self.alerts.clear()
        self._distribution_warning_profile_id = ""
        self._start_connection(profile, secret, reconnect=False)

    def _ask_secret(self, profile):
        if profile.auth_method == "password":
            value, ok = QInputDialog.getText(
                self, tr('ui.ssh_authentication'),
                f"{tr('ui.password_for')} {profile.user}@{profile.name} :",
                QLineEdit.Password,
            )
            return value if ok else False
        if profile.key_path:
            value, ok = QInputDialog.getText(
                self, tr('ui.ssh_key'),
                tr('ui.key_passphrase_leave_blank_if_none'),
                QLineEdit.Password,
            )
            return value if ok else False
        return None

    def _start_connection(self, profile, secret, reconnect: bool) -> None:
        if self.connection_worker:
            return
        self.reconnecting = reconnect
        self.terminals.append_system(f"\n>>> {'Reconnexion' if reconnect else 'Connexion'} à {profile.name}…\n")
        self.statusBar().showMessage(tr('ui.reconnecting' if reconnect else 'ui.connecting_status'))
        self.server_label.setText(f"{profile.name} — {tr('ui.connecting')}")
        self.profiles.set_connection_state(False, busy=True)
        worker = ConnectionWorker(
            profile, secret, self.cloudflare_secret, self.cloudflare_user_token
        )
        self.connection_worker = worker
        worker.status.connect(lambda text: self.statusBar().showMessage(tr(text)))
        worker.log.connect(self.terminals.append_system)
        worker.host_key_required.connect(self._confirm_host_key)
        worker.cloudflare_user_token_acquired.connect(self._store_cloudflare_user_token)
        worker.connected.connect(self._connected)
        worker.failed.connect(self._connection_failed)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def _store_cloudflare_user_token(self, token: str) -> None:
        if not token or not self.current_profile:
            return
        self.cloudflare_user_token = token
        try:
            self.credentials.set_cloudflare_user_token(self.current_profile, token)
        except RuntimeError as exc:
            self.terminals.append_system(
                f">>> Session Cloudflare non enregistrée dans le coffre : {exc}\n"
            )

    def _confirm_host_key(self, hostname: str, algorithm: str, fingerprint: str) -> None:
        if self.connection_worker:
            text = (
                f"{tr('ui.new_ssh_key_a20e62')}\n\n"
                f"{tr('ui.host')} {hostname}\n"
                f"{tr('ui.type_50c8cd')} {algorithm}\n"
                f"{tr('ui.fingerprint')} {fingerprint}\n\n"
                f"{tr('ui.accept_it')}"
            )
            accepted = (
                QMessageBox.question(self, tr('ui.new_ssh_key'), text)
                == QMessageBox.Yes
            )
            self.connection_worker.submit_host_key_decision(accepted)

    def _connected(self, session) -> None:
        if self.manual_disconnect:
            session.close()
            return
        was_reconnecting = self.reconnecting
        self.session = session
        self._favorite_failed_signature = ""
        self.alerts.remove_alert("connection")
        self.alerts.remove_alert("connection_final")
        stored_sudo = self.credentials.get_sudo_password(session.profile)
        if stored_sudo:
            session.sudo_password = stored_sudo
        self.connection_generation += 1
        generation = self.connection_generation
        # The shell is usable as soon as it opens. Distribution and update
        # checks remain fully asynchronous.
        self.terminals.set_input_allowed(True)
        self.terminals.set_session(session, reconnect=self.reconnecting)
        self.services.set_session(session)
        self.files.set_session(session)
        self.system.set_session(session)
        self.backups.set_session(session)
        self.postgres.set_session(session)
        self.network_security.set_session(session)
        self.tasks.set_session(session)
        self.system.set_update_count(None)
        self._set_update_toolbar(None)
        self.favorites.connected = True
        self.monitor.set_session(session)
        self.profiles.select_profile(session.profile.id)
        self.profiles.set_connection_state(True)
        self.server_label.setText(f"● {session.profile.name}")
        self.identity_button.setText(f"👤 {session.profile.user} · …")
        self.identity_button.setEnabled(True)
        self.statusBar().showMessage(f"{tr('ui.connected_singular')} — {session.profile.name}")
        self._load_identity(session)
        run_async(
            session.distribution_info,
            lambda info: self._distribution_checked(session, info),
            lambda text: self._distribution_probe_failed(session, text),
            guard=lambda: self.session is session,
        )
        if not was_reconnecting:
            self._schedule_health_popup(session)
        QTimer.singleShot(60000, lambda: self._mark_connection_stable(generation))


    def _distribution_checked(self, session, info: object) -> None:
        if self.session is not session:
            return
        manager = ""
        if isinstance(info, dict):
            family = str(info.get("family") or "other")
            detected_name = str(info.get("name") or "Linux")
            manager = str(info.get("package_manager") or "").lower()
            self.terminals.set_environment(detected_name, family, manager)
            configured = normalize_distribution(
                getattr(session.profile, "distribution", "auto")
            )
            detected = normalize_distribution(family)
            if configured not in {"auto", "other"} and detected != configured:
                self.statusBar().showMessage(
                    f"{detected_name} — {tr('ui.detected_distribution_used_for_updates')}"
                )
        # The real package manager detected on the server is authoritative.
        if manager in {"apt", "dnf", "yum"}:
            self._start_initial_update_check(session)
        else:
            message = tr('ui.package_manager_not_supported')
            self.system.set_update_count(None, message)
            self._set_update_toolbar(None, message)

    def _start_initial_update_check(self, session) -> None:
        if self.session is not session:
            return
        run_async(
            session.update_count,
            lambda count: self._updates_done(session, int(count)),
            lambda error: self._updates_failed(session, error),
            guard=lambda: self.session is session,
        )

    def _distribution_probe_failed(self, session, text: str) -> None:
        if self.session is not session:
            return
        configured = normalize_distribution(
            getattr(session.profile, "distribution", "auto")
        )
        if configured in {"ubuntu", "redhat"}:
            self.terminals.set_environment(
                distribution_label(configured), configured,
                "apt" if configured == "ubuntu" else "dnf",
            )
            self._start_initial_update_check(session)
            return
        self.system.set_update_count(None, text)
        self._set_update_toolbar(None, text)
        self.terminals.set_environment("Linux", "other")

    def _updates_done(self, session, count: int) -> None:
        if self.session is not session:
            return
        self.system.set_update_count(count)
        self._set_update_toolbar(count)
        update_label = ntr(count, 'ui.available_update', 'ui.available_updates_e8cc9d')
        self.terminals.append_system(
            f">>> {tr('fragment.update_check_completed')} {count} {update_label}.\n"
        )
        self.terminals.redraw_prompt()
        self.terminals.set_input_allowed(True)
        if count:
            self.alerts.add_alert(
                "updates", tr('fragment.updates'), f"{count} {update_label}."
            )
        else:
            self.alerts.remove_alert("updates")

    def _updates_failed(self, session, text: str) -> None:
        if self.session is session:
            self.system.set_update_count(None, text)
            self._set_update_toolbar(None, text)
            self.terminals.append_system(f">>> Vérification des mises à jour impossible : {text}\n")
            self.terminals.redraw_prompt()
            self.terminals.set_input_allowed(True)

    def _mark_connection_stable(self, generation: int) -> None:
        if generation == self.connection_generation and self.session and self.session.is_alive():
            self.reconnect_attempts = 0

    def _connection_failed(self, message: str) -> None:
        if self.manual_disconnect:
            self.statusBar().showMessage(tr('fragment.disconnected'))
            return
        self.terminals.append_system(f">>> Échec de connexion : {message}\n")
        self.profiles.set_connection_state(False)
        self.alerts.add_alert("connection", "Connexion", message)
        if self.reconnecting:
            self._schedule_reconnect()
        else:
            self.statusBar().showMessage(tr('ui.connection_failed'))
            QMessageBox.critical(self, "Connexion impossible", message)

    def _worker_finished(self) -> None:
        self.connection_worker = None

    def _session_lost(self) -> None:
        if self.manual_disconnect or not self.session:
            return
        self.terminals.append_system("\n>>> Connexion perdue.\n")
        self.alerts.add_alert("connection", "Connexion perdue", "La reconnexion automatique va démarrer.")
        self._detach_session(close=True)
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self.manual_disconnect:
            return
        if self.reconnect_attempts >= self.MAX_RECONNECTS:
            self.reconnecting = False
            self.statusBar().showMessage(tr('ui.reconnection_abandoned_after_10_attempts'))
            self.terminals.append_system(">>> Échec après 10 tentatives automatiques.\n")
            self.profiles.set_connection_state(False)
            self.alerts.add_alert(
                "connection_final",
                "Connexion définitivement perdue",
                "La reconnexion automatique a échoué après 10 tentatives.",
            )
            return
        delay = self.RECONNECT_DELAYS[self.reconnect_attempts]
        self.reconnect_attempts += 1
        self.statusBar().showMessage(
            f"{tr('ui.reconnection')} {self.reconnect_attempts}/10 "
            f"{tr('ui.in')} {delay} s…"
        )
        self.terminals.append_system(
            f">>> {tr('ui.automatic_reconnection')} {self.reconnect_attempts}/10 "
            f"{tr('ui.in')} {delay} s…\n"
        )
        self.reconnect_timer.start(delay * 1000)

    def _do_reconnect(self) -> None:
        if self.current_profile and not self.manual_disconnect:
            self._start_connection(self.current_profile, self.secret, reconnect=True)

    def disconnect_session(self) -> None:
        if self.updates_installing:
            QMessageBox.warning(
                self,
                "Mises à jour en cours",
                "La déconnexion est désactivée pendant l’installation des mises à jour.",
            )
            return
        self.manual_disconnect = True
        self.reconnect_timer.stop()
        if self.connection_worker:
            self.connection_worker.cancel()
        self._detach_session(close=True)
        self.profiles.set_connection_state(False)
        self.server_label.setText("Aucun serveur connecté")
        self.statusBar().showMessage(tr('fragment.disconnected'))

    def _detach_session(self, close: bool) -> None:
        session = self.session
        if self.updates_installing:
            self.updates_installing = False
            self._updates_operation = None
            self.alerts.add_alert(
                "updates_install_unknown",
                "État des mises à jour inconnu",
                "La connexion a été perdue pendant l’installation. Vérifie le gestionnaire de paquets après reconnexion.",
            )
        self.monitor.set_session(None)
        self.terminals.set_session(None)
        self.services.set_session(None)
        self.files.set_session(None)
        self.system.set_session(None)
        self.backups.set_session(None)
        self.postgres.set_session(None)
        self.network_security.set_session(None)
        self.tasks.set_session(None)
        self._set_update_toolbar(None)
        self.favorites.connected = False
        self.session = None
        self.identity_button.setText("👤 —")
        self.identity_button.setEnabled(False)
        self.terminals.set_environment("Linux —", "other")
        if close and session:
            session.close()
