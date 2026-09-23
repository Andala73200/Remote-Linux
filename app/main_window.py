import shlex

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QLabel, QMainWindow, QMessageBox, QPushButton,
    QSizePolicy, QSplitter, QTabWidget, QToolBar, QWidget,
)

from app.core.async_task import run_async
from app.core.secure_credentials import SecureCredentialStore
from app.core.session_monitor import SessionMonitor
from app.core.temp_edit import TempEditManager
from app.dialogs.logs_dialog import LogsDialog, shutdown_log_readers
from app.dialogs.storage_action_dialog import StorageActionDialog
from app.dialogs.update_install_dialog import UpdateInstallDialog
from app.dialogs.updates_dialog import UpdatesDialog
from app.favorites import FavoritesController
from app.i18n import ntr, tr
from app.main_connection_mixin import ConnectionMixin
from app.main_menu_mixin import MainMenuMixin
from app.main_observability_mixin import MainObservabilityMixin
from app.main_sudo_mixin import MainSudoMixin
from app.models import FavoriteCommand
from app.storage import Storage
from app.widgets.alert_center import AlertCenter
from app.widgets.backups_widget import BackupsWidget
from app.widgets.network_security_widget import NetworkSecurityWidget
from app.widgets.postgres_widget import PostgresWidget
from app.widgets.profiles_widget import ProfilesWidget
from app.widgets.remote_files_widget import RemoteFilesWidget
from app.widgets.services_widget import ServicesWidget
from app.widgets.system_widget import SystemWidget
from app.widgets.tasks_widget import TasksWidget
from app.widgets.terminal_tabs import TerminalTabs
from app.widgets.windows_notifier import WindowsNotifier
from app.version import APP_TITLE


class MainWindow(
    MainMenuMixin, MainObservabilityMixin, ConnectionMixin, MainSudoMixin, QMainWindow
):
    MAX_RECONNECTS = 10
    RECONNECT_DELAYS = [2, 5, 10, 15, 15, 15, 15, 15, 15, 15]

    def __init__(self, storage=None):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1500, 900)
        self.storage = storage or Storage()
        self.credentials = SecureCredentialStore()
        self.session = None
        self.current_profile = None
        self.connection_worker = None
        self.secret: str | None = None
        self.cloudflare_secret: str | None = None
        self.cloudflare_user_token: str | None = None
        self.manual_disconnect = False
        self.reconnecting = False
        self.reconnect_attempts = 0
        self.connection_generation = 0
        self.updates_installing = False
        self._updates_operation = None
        self._favorite_failed_signature = ""
        self.alerts = AlertCenter()
        self.notifier = WindowsNotifier(
            self, bool(self.storage.settings.get("windows_notifications", True))
        )
        self.server_label = QLabel("Aucun serveur connecté")
        self._build_toolbar()
        self.profiles = ProfilesWidget()
        self.files = RemoteFilesWidget(self.storage)
        left = QSplitter(Qt.Vertical)
        left.addWidget(self.profiles)
        left.addWidget(self.files)
        left.setSizes([190, 610])
        left.setMinimumWidth(285)
        left.setMaximumWidth(470)
        self.terminals = TerminalTabs()
        self.terminals.apply_preferences(self.storage.settings)
        self.services = ServicesWidget(self.storage)
        self.services.set_sudo_password_provider(self._sudo_password)
        self.system = SystemWidget(self.storage.settings)
        self.backups = BackupsWidget(self.storage)
        self.postgres = PostgresWidget()
        self.network_security = NetworkSecurityWidget(self.storage)
        self.tasks = TasksWidget()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.terminals, "Terminal")
        self.tabs.addTab(self.services, "Services systemd")
        self.tabs.addTab(self.system, "Système")
        self.tabs.addTab(self.backups, "Sauvegardes")
        self.tabs.addTab(self.postgres, "PostgreSQL")
        self.tabs.addTab(self.network_security, "Réseau && sécurité")
        self.tabs.addTab(self.tasks, "Tâches planifiées")
        self.tabs.currentChanged.connect(self._main_tab_changed)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([350, 1150])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage(tr('fragment.disconnected'))
        self._build_status_metrics()
        self.favorites = FavoritesController(
            self.storage,
            self.services.service_names,
            self.services.favorite_service_names,
            lambda: self.session.quick_updates_command() if self.session else "",
            self,
        )
        self.temp_editor = TempEditManager(
            self, lambda: self.session, self._sudo_password
        )
        self.monitor = SessionMonitor(self)
        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.setSingleShot(True)
        self.reconnect_timer.timeout.connect(self._do_reconnect)
        self._connect_signals()
        self._reload_profiles()
        self.build_menu_bar()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("État global")
        toolbar.setMovable(False)
        toolbar.addWidget(self.server_label)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        self.updates_button = QPushButton("⬆ Mises à jour")
        self.updates_button.setMinimumWidth(105)
        self.updates_button.setToolTip("Voir les mises à jour disponibles et installer la sélection")
        self.updates_button.clicked.connect(self._open_updates)
        self.updates_button_action = toolbar.addWidget(self.updates_button)
        self.updates_button_action.setVisible(False)
        self.identity_button = QPushButton("👤 —")
        self.identity_button.setEnabled(False)
        self.identity_button.setToolTip("Utilisateurs et groupes Linux")
        self.identity_button.clicked.connect(self._open_users)
        toolbar.addWidget(self.identity_button)
        toolbar.addWidget(self.alerts)
        self.global_toolbar = toolbar
        self.addToolBar(Qt.TopToolBarArea, toolbar)

    def _build_status_metrics(self) -> None:
        """Add persistent system metrics to the right side of the status bar."""
        self.status_metrics: dict[str, QLabel] = {}
        definitions = (
            ("cpu", "CPU —", "Utilisation CPU instantanée"),
            ("memory", "RAM —", "Mémoire utilisée / totale"),
            ("gpu", "GPU —", "GPU"),
            ("disk", "/ —", "Occupation du volume racine /"),
            ("network", "↓ —  ↑ —", "Débit réseau instantané reçu / envoyé"),
            ("temperature", "Temp. —", "Température maximale actuellement détectée"),
        )
        for key, text, tooltip in definitions:
            label = QLabel(text)
            label.setToolTip(tooltip)
            label.setContentsMargins(8, 0, 8, 0)
            label.setStyleSheet("QLabel { color: #c8ccd4; }")
            self.statusBar().addPermanentWidget(label)
            self.status_metrics[key] = label
            if key == "gpu":
                label.hide()

    def _system_metrics_changed(self, metrics: object) -> None:
        data = dict(metrics or {})
        if not data:
            values = {
                "cpu": "CPU —",
                "memory": "RAM —",
                "gpu": "GPU —",
                "disk": "/ —",
                "network": "↓ —  ↑ —",
                "temperature": "Temp. —",
            }
            for key, text in values.items():
                self.status_metrics[key].setText(text)
                self.status_metrics[key].setStyleSheet("QLabel { color: #7f8792; }")
            self.status_metrics["gpu"].hide()
            return

        cpu = float(data.get("cpu_percent", 0.0))
        memory = float(data.get("memory_percent", 0.0))
        gpu = float(data.get("gpu_percent", 0.0))
        disk = float(data.get("disk_percent", 0.0))
        rx_rate = float(data.get("rx_rate", 0.0))
        tx_rate = float(data.get("tx_rate", 0.0))
        temperature = float(data.get("temperature", 0.0))
        memory_used = float(data.get("memory_used", 0.0))
        memory_total = float(data.get("memory_total", 0.0))

        self.status_metrics["cpu"].setText(f"CPU {cpu:.1f} %")
        self.status_metrics["memory"].setText(
            f"RAM {self.system._bytes(memory_used)} / {self.system._bytes(memory_total)}  {memory:.1f} %"
        )
        has_gpu = bool(data.get("has_gpu"))
        self.status_metrics["gpu"].setVisible(has_gpu)
        if has_gpu:
            self.status_metrics["gpu"].setText(f"GPU {gpu:.1f} %")
        self.status_metrics["disk"].setText(f"/ {disk:.1f} %")
        self.status_metrics["network"].setText(
            f"↓ {self.system._rate(rx_rate)}  ↑ {self.system._rate(tx_rate)}"
        )
        self.status_metrics["temperature"].setText(
            f"Temp. {temperature:.1f} °C" if data.get("has_temperature") else "Temp. —"
        )

        self._set_metric_color("cpu", cpu, 65.0, 85.0)
        self._set_metric_color("memory", memory, 70.0, 90.0)
        if has_gpu:
            self._set_metric_color("gpu", gpu, 70.0, 90.0)
        disk_alert = float(self.storage.settings.get("disk_alert_percent", 85))
        self._set_metric_color("disk", disk, max(0.0, disk_alert - 15.0), disk_alert)
        self.status_metrics["network"].setStyleSheet("QLabel { color: #8be9fd; }")
        if data.get("has_temperature"):
            temp_alert = float(self.storage.settings.get("temperature_alert_c", 75))
            self._set_metric_color(
                "temperature", temperature, max(0.0, temp_alert - 15.0), temp_alert
            )
        else:
            self.status_metrics["temperature"].setStyleSheet("QLabel { color: #7f8792; }")

    def _set_metric_color(
        self, key: str, value: float, warning: float, critical: float
    ) -> None:
        if value >= critical:
            color = "#ff6666"
        elif value >= warning:
            color = "#f2c866"
        else:
            color = "#55d187"
        self.status_metrics[key].setStyleSheet(f"QLabel {{ color: {color}; }}")

    def _set_update_toolbar(self, count: int | None, error: str = "") -> None:
        visible = not error and count is not None and count > 0
        if visible:
            self.updates_button.setText(f"⬆ {count} MAJ")
            update_label = ntr(count, 'ui.available_update', 'ui.available_updates_e8cc9d')
            self.updates_button.setToolTip(
                f"{count} {update_label}. "
                f"{tr('ui.click_to_view_packages_and_install_the_selection')}"
            )
        self.updates_button.setVisible(visible)
        self.updates_button_action.setVisible(visible)
        self.global_toolbar.update()

    def _connect_signals(self) -> None:
        self.profiles.add_requested.connect(self.add_profile)
        self.profiles.edit_requested.connect(self.edit_profile)
        self.profiles.delete_requested.connect(self.delete_profile)
        self.profiles.connect_requested.connect(self.connect_selected)
        self.profiles.disconnect_requested.connect(self.disconnect_session)
        self.profiles.purge_requested.connect(self.purge_profile_connection_data)
        self.profiles.health_popup_reactivate_requested.connect(
            self._reactivate_health_popup
        )
        self.terminals.command_sent.connect(self._command_sent)
        self.terminals.favorites_requested.connect(self._show_favorites)
        self.terminals.path_requested.connect(self.files.navigate_to)
        self.terminals.sudo_password_requested.connect(self._terminal_sudo_password)
        self.terminals.package_manager_requested.connect(self._open_package_manager)
        self.terminals.venv_activation_requested.connect(
            self._offer_venv_activation
        )
        self.terminals.cd_completed.connect(self._terminal_cd_completed)
        self.favorites.command_requested.connect(self._run_favorite)
        self.favorites.tree_command_requested.connect(self._run_favorite_with_tree)
        self.favorites.output_requested.connect(self._show_command_output)
        self.favorites.live_output_requested.connect(self._show_live_output)
        self.files.directory_activated.connect(self._terminal_here)
        self.files.edit_requested.connect(self._edit_remote_file)
        self.services.logs_requested.connect(self._service_logs)
        self.services.config_requested.connect(self._edit_remote_file)
        self.services.failed_services_changed.connect(self._failed_services_changed)
        self.services.favorite_failed_services_changed.connect(
            self._favorite_failed_services_changed
        )
        self.system.alert_raised.connect(self.alerts.add_alert)
        self.system.alert_cleared.connect(self.alerts.remove_alert)
        self.system.output_requested.connect(self._show_command_output)
        self.system.storage_action_requested.connect(self._storage_action)
        self.system.updates_requested.connect(self._open_updates)
        self.system.metrics_updated.connect(self._system_metrics_changed)
        self.backups.health_changed.connect(self._backup_health_changed)
        self.temp_editor.file_changed.connect(lambda path: self.alerts.add_alert(f"file:{path}", "Fichier modifié", f"Une version locale attend une décision : {path}"))
        self.temp_editor.file_resolved.connect(lambda path: self.alerts.remove_alert(f"file:{path}"))
        self.files.transfer_failed.connect(self._transfer_failed)
        self.alerts.alert_added.connect(self._alert_added)
        self.monitor.disconnected.connect(self._session_lost)

    def _apply_notification_preferences(self) -> None:
        self.notifier.set_enabled(
            bool(self.storage.settings.get("windows_notifications", True))
        )

    def _notify_windows(self, title: str, message: str) -> None:
        self.notifier.notify(tr(title), tr(message))

    def _alert_added(self, key: str, title: str, message: str) -> None:
        # Failed services use dedicated logic so notifications are sent only
        # for services marked as favorites.
        if key == "services_failed":
            return
        self._notify_windows(title, message)

    def _favorite_failed_services_changed(self, count: int, names: str) -> None:
        signature = names if count else ""
        if count and signature != self._favorite_failed_signature:
            self._notify_windows(
                "Service favori en échec",
                f"{count} {ntr(count, 'ui.favorite_service', 'ui.favorite_services')} : {names}",
            )
        self._favorite_failed_signature = signature

    def _transfer_failed(self, message: str) -> None:
        self.alerts.add_alert("transfer_failed", "Transfert échoué", message)

    def _reload_profiles(self) -> None:
        self.profiles.set_profiles(self.storage.profiles)

    def _show_favorites(self, button) -> None:
        self.favorites.show_menu(button, self.terminals.current_command())

    def _terminal_sudo_password(self, terminal) -> None:
        """Request the secret when needed and inject it only after the click."""
        password = self._sudo_password()
        if password is False:
            return
        terminal.inject_sudo_password(str(password))

    def _run_favorite(self, command: str) -> None:
        self.tabs.setCurrentWidget(self.terminals)
        self.terminals.send_command(command)

    def _run_favorite_with_tree(self, command: str) -> None:
        self.tabs.setCurrentWidget(self.terminals)
        self.terminals.send_command(command, follow_tree=True)

    def _terminal_cd_completed(
        self, _command: str, path: str, force_follow: bool
    ) -> None:
        if not self.session or not path:
            return
        if force_follow:
            if self.files.current_path.rstrip("/") != path.rstrip("/"):
                self.files.navigate_to(path)
            return
        mode = str(self.storage.settings.get("terminal_cd_tree_sync", "ask"))
        if mode == "always":
            self.files.navigate_to(path)
            return
        if mode == "never":
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("ui.cd_tree_sync_title"))
        box.setText(f"{tr('ui.follow_cd_question')}\n\n{path}")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        remember = QCheckBox(tr("ui.always_use_this_choice"))
        box.setCheckBox(remember)
        answer = box.exec()
        follow = answer == QMessageBox.Yes
        if remember.isChecked():
            self.storage.settings["terminal_cd_tree_sync"] = (
                "always" if follow else "never"
            )
            self.storage.save()
        if follow:
            self.files.navigate_to(path)

    def _show_command_output(self, command: str, title: str) -> None:
        if not self.session:
            return
        session = self.session
        run_async(
            lambda: session.execute(command, timeout=90),
            lambda result: self._output_ready(result, title, command),
            lambda text: QMessageBox.critical(self, title, text),
            guard=lambda: self.session is session,
        )

    def _output_ready(self, result: object, title: str, command: str) -> None:
        code, output = result
        text = str(output) or f"Commande terminée avec le code {code}."
        LogsDialog(title, text, command, self).exec()

    def _show_live_output(self, command: str, title: str) -> None:
        if self.session:
            LogsDialog(title, "", command, self, session=self.session, live=True).exec()

    def _service_logs(self, service: str) -> None:
        favorite = FavoriteCommand(name="Journaux", kind="smart_logs", params={"service": service, "favorite_only": True})
        self.favorites.run(favorite, self)

    def _failed_services_changed(self, count: int, names: str) -> None:
        if count:
            self.alerts.add_alert(
                "services_failed", tr('ui.failed_services'),
                f"{count} {ntr(count, 'ui.service_4cf5bc', 'ui.services_3e7aaa')} : {names}",
            )
        else:
            self.alerts.remove_alert("services_failed")

    def _terminal_here(self, path: str) -> None:
        if self.session and self.terminals.input_allowed:
            self.tabs.setCurrentWidget(self.terminals)
            self.terminals.send_command(
                f"cd -- {shlex.quote(path)}", follow_tree=True
            )

    def _edit_remote_file(self, path: str) -> None:
        if self.session:
            self.temp_editor.open_remote(self.session, path)

    def _command_sent(self, command: str) -> None:
        stripped = command.strip()
        if stripped.startswith("systemctl") or stripped.startswith("sudo systemctl"):
            QTimer.singleShot(1200, self.services.refresh)

    def _storage_action(self, action: str, payload: dict) -> None:
        if not self.session:
            QMessageBox.information(self, "Stockage", "Aucun serveur connecté.")
            return
        if action == "smart":
            password = self._sudo_password()
            if password is False:
                return
            device = str(payload.get("path") or "")
            session = self.session
            run_async(
                lambda: session.storage_smart(device, password),
                lambda result: self._storage_result(result, f"SMART — {device}"),
                lambda text: QMessageBox.critical(self, "SMART", text),
                guard=lambda: self.session is session,
            )
            return
        dialog = StorageActionDialog(action, payload, self)
        if not dialog.exec():
            return
        password = self._sudo_password()
        if password is False:
            return
        values = dialog.values()
        self.statusBar().showMessage(f"{tr('ui.storage_operation_prefix')}{action}…")
        session = self.session
        run_async(
            lambda: session.storage_action(action, values, password),
            lambda result: self._storage_result(result, dialog.windowTitle()),
            lambda text: QMessageBox.critical(self, dialog.windowTitle(), text),
            guard=lambda: self.session is session,
        )

    def _storage_result(self, result: object, title: str) -> None:
        code, output = result
        if int(code) == 0:
            QMessageBox.information(self, title, str(output) or "Opération terminée avec succès.")
            self.statusBar().showMessage(tr('ui.storage_operation_completed'))
            self.system.refresh_inventory()
        else:
            QMessageBox.critical(self, title, str(output) or f"Échec avec le code {code}.")
            self.statusBar().showMessage(tr('ui.storage_operation_failed'))

    def _open_updates(self) -> None:
        if not self.session:
            QMessageBox.information(self, "Mises à jour", "Aucun serveur connecté.")
            return
        self.statusBar().showMessage(tr('ui.reading_available_updates'))
        session = self.session
        run_async(
            session.list_updates,
            self._updates_list_ready,
            lambda text: QMessageBox.critical(self, "Mises à jour", text),
            guard=lambda: self.session is session,
        )

    def _updates_list_ready(self, updates: object) -> None:
        rows = list(updates)
        update_label = ntr(len(rows), 'ui.available_update', 'ui.available_updates_e8cc9d')
        self.statusBar().showMessage(f"{len(rows)} {update_label}")
        self.system.set_update_count(len(rows))
        self._set_update_toolbar(len(rows))
        if not rows:
            QMessageBox.information(self, "Mises à jour", "Le serveur est à jour.")
            self.system.set_update_count(0)
            self._set_update_toolbar(0)
            return
        dialog = UpdatesDialog(rows, self)
        if not dialog.exec():
            return
        packages = dialog.selected_packages()
        command = self.session.updates_install_preview(packages) if self.session else ""
        package_count = len(packages)
        update_label = ntr(package_count, 'ui.update', 'ui.updates_7a435a')
        text = (
            f"{tr('ui.install')} {package_count} {update_label} ?\n\n"
            f"{tr('fragment.command')}\n{command}"
        )
        if QMessageBox.question(self, "Installer les mises à jour", text) != QMessageBox.Yes:
            return
        password = self._sudo_password()
        if password is False:
            return
        self._install_updates(packages, password)

    def _install_updates(
        self,
        packages: list[str],
        password: str | None,
        progress: UpdateInstallDialog | None = None,
    ) -> None:
        session = self.session
        if not session:
            return
        command = session.updates_install_preview(packages)
        if progress is None:
            progress = UpdateInstallDialog(command, self)
            progress.show()
            progress.raise_()
            progress.activateWindow()
        else:
            progress.set_running(command)

        operation = object()
        self._updates_operation = operation
        self.updates_installing = True
        self.statusBar().showMessage(tr("ui.installing_updates"))

        def release_state() -> bool:
            if self._updates_operation is not operation:
                return False
            self._updates_operation = None
            self.updates_installing = False
            return self.session is session

        def done(result: object) -> None:
            if not release_state():
                return
            code, output = result
            if int(code) == 0:
                progress.set_result(True, str(output))
                self.alerts.remove_alert("updates")
                self._notify_windows(
                    "Mises à jour terminées",
                    f"{len(packages)} "
                    f"{ntr(len(packages), 'ui.package_was_updated', 'ui.packages_were_updated')} "
                    f"{tr('ui.successfully')}",
                )
                self._check_updates()
                return
            lowered = str(output).lower()
            if any(
                word in lowered
                for word in ("password", "mot de passe", "authentication")
            ):
                session.sudo_password = None
                entered = self._sudo_password(force_prompt=True)
                if entered is not False and entered:
                    self._install_updates(
                        packages, str(entered), progress=progress
                    )
                    return
            message = str(output) or f"Échec avec le code {code}."
            progress.set_result(False, message)
            self.alerts.add_alert(
                "updates_install_failed", "Échec des mises à jour", message
            )

        def failed(text: str) -> None:
            if not release_state():
                return
            progress.set_result(False, text)
            self.alerts.add_alert(
                "updates_install_failed", "Échec des mises à jour", text
            )

        run_async(
            lambda: session.install_updates(packages, password),
            done,
            failed,
        )

    def _close_blockers(self) -> list[str]:
        blockers = []
        transfer_count = self.files.active_transfer_count()
        if transfer_count:
            blockers.append(
                f"• {transfer_count} "
                f"{ntr(transfer_count, 'ui.transfer_pending_or_in_progress', 'ui.transfers_pending_or_in_progress')}"
            )
        pending_files = self.temp_editor.pending_changes()
        if pending_files:
            pending_count = len(pending_files)
            blockers.append(
                f"• {pending_count} "
                f"{ntr(pending_count, 'ui.modified_temporary_file_not_yet_sent_back_to_the', 'ui.modified_temporary_files_not_yet_sent_back_to_the')}"
            )
        if self.updates_installing:
            blockers.append("• Une installation de mises à jour est en cours")
        return blockers

    def closeEvent(self, event) -> None:
        if self.updates_installing:
            QMessageBox.warning(
                self,
                "Mises à jour en cours",
                "Remote Linux ne peut pas être fermé pendant une opération "
                "APT/DPKG. Attendez la fin de l’installation pour éviter "
                "d’endommager le gestionnaire de paquets.",
            )
            event.ignore()
            return
        blockers = [
            item
            for item in self._close_blockers()
            if "mise à jour" not in item.lower()
        ]
        if blockers:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Opérations en cours")
            box.setText("Certaines opérations ne sont pas terminées.")
            box.setInformativeText("\n".join(blockers))
            stay_button = box.addButton(
                "Revenir à l’application", QMessageBox.RejectRole
            )
            quit_button = box.addButton(
                "Quitter quand même", QMessageBox.DestructiveRole
            )
            box.setDefaultButton(stay_button)
            box.exec()
            if box.clickedButton() is not quit_button:
                event.ignore()
                return
        self.disconnect_session()
        worker = self.connection_worker
        if worker and worker.isRunning() and not worker.wait(5000):
            QMessageBox.warning(
                self,
                "Connexion en cours d’arrêt",
                "La connexion réseau se ferme encore. Réessaie dans quelques secondes.",
            )
            event.ignore()
            return
        if not shutdown_log_readers(5000):
            QMessageBox.warning(
                self,
                "Journaux en cours d’arrêt",
                "Un lecteur de journaux se ferme encore. Réessaie dans quelques secondes.",
            )
            event.ignore()
            return
        if not self.files.shutdown_transfers(5000):
            QMessageBox.warning(
                self,
                "Transfert en cours d’arrêt",
                "Un transfert se ferme encore. Réessaie dans quelques secondes.",
            )
            event.ignore()
            return
        self.temp_editor.close()
        self.notifier.close()
        event.accept()
