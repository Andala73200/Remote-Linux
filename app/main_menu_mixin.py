from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from app.core.async_task import run_async
from app.dialogs.about_dialog import AboutDialog
from app.i18n import tr


class MainMenuMixin:
    def build_menu_bar(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&Fichier")
        self._action(file_menu, "Nouvelle connexion…", self.add_profile, "Ctrl+Shift+N")
        self._action(file_menu, "Se connecter", self.connect_selected, "Ctrl+Shift+O")
        self._action(file_menu, "Se déconnecter", self.disconnect_session, "Ctrl+Shift+D")
        file_menu.addSeparator()
        self._action(file_menu, "Importer la configuration…", self._import_configuration)
        self._action(file_menu, "Exporter la configuration…", self._export_configuration)
        file_menu.addSeparator()
        self._action(file_menu, "Préférences…", self.open_preferences, "Ctrl+,")
        self._action(file_menu, "Quitter", self.close, "Alt+F4")

        edit_menu = bar.addMenu("&Édition")
        self._action(edit_menu, "Copier", self._copy, "Ctrl+Shift+C")
        self._action(edit_menu, "Coller", self._paste, "Ctrl+Shift+V")
        self._action(edit_menu, "Rechercher dans le terminal…", self._find_terminal, "Ctrl+Shift+F")
        edit_menu.addSeparator()
        self._action(edit_menu, "Effacer le terminal", self._clear_terminal, "Ctrl+Shift+L")
        self._action(edit_menu, "Renommer l’onglet terminal…", self._rename_terminal, "Ctrl+Shift+R")
        self._action(edit_menu, "Gérer les favoris…", lambda: self.favorites.manage(self))

        view_menu = bar.addMenu("&Affichage")
        for index, label in enumerate((
            "Terminal", "Services systemd", "Système", "Sauvegardes",
            "PostgreSQL", "Réseau && sécurité", "Tâches planifiées",
        )):
            self._action(view_menu, label, lambda checked=False, i=index: self.tabs.setCurrentIndex(i))
        view_menu.addSeparator()
        technical = self._action(view_menu, "Afficher les volumes techniques", self.system.storage_page.set_show_technical)
        technical.setCheckable(True)
        self.system.storage_page.show_technical.toggled.connect(technical.setChecked)
        level_menu = view_menu.addMenu("Niveau d’affichage système")
        group = QActionGroup(self)
        group.setExclusive(True)
        for label, value in (("Simple", "simple"), ("Avancé", "advanced"), ("Expert", "expert")):
            action = self._action(level_menu, label, lambda checked=False, v=value: self._set_system_level(v))
            action.setCheckable(True)
            action.setChecked(str(self.storage.settings.get("system_level")) == value)
            group.addAction(action)

        tools_menu = bar.addMenu("&Outils")
        self._action(tools_menu, "Vérifier les mises à jour", self._check_updates)
        self._action(tools_menu, "Journaux d’un service…", self._open_smart_logs)
        self._action(tools_menu, "Gestion des stockages", self._show_storage)
        self._action(tools_menu, "Actualiser l’inventaire système", self.system.refresh_inventory, "Ctrl+Shift+F5")
        self._action(tools_menu, "Utilisateurs et groupes Linux", self._open_users)
        tools_menu.addSeparator()
        self._action(tools_menu, "Déverrouiller sudo…", self.unlock_sudo)
        self._action(tools_menu, "Oublier le mot de passe sudo enregistré", lambda: self.forget_sudo_password(True))
        tools_menu.addSeparator()
        self._action(tools_menu, "Réseau, ports et sécurité", self._show_network_security)

        connection_menu = bar.addMenu("&Connexion")
        self._action(connection_menu, "Se connecter", self.connect_selected)
        self._action(connection_menu, "Se déconnecter", self.disconnect_session)
        self._action(connection_menu, "Reconnexion forcée", self._force_reconnect)
        connection_menu.addSeparator()
        self._action(connection_menu, "Nouveau terminal", self.terminals.add_terminal, "Ctrl+Shift+T")
        self._action(connection_menu, "Arborescence sur le dossier courant", lambda: self.files.navigate_to(self.terminals.current_path()))

        help_menu = bar.addMenu("&Aide")
        self._action(help_menu, "Raccourcis clavier", self._show_shortcuts)
        self._action(help_menu, "Diagnostic de la connexion", self._show_diagnostic)
        help_menu.addSeparator()
        self._action(help_menu, "À propos de Remote Linux", self._show_about)

    def _action(self, menu, text: str, slot, shortcut: str | None = None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _copy(self) -> None:
        widget = QApplication.focusWidget()
        if widget and hasattr(widget, "copy"):
            widget.copy()
        else:
            self.terminals.active_terminal().console.copy()

    def _paste(self) -> None:
        widget = QApplication.focusWidget()
        if widget and hasattr(widget, "paste"):
            widget.paste()
        else:
            self.terminals.active_terminal().console.paste()

    def _find_terminal(self) -> None:
        text, ok = QInputDialog.getText(self, "Rechercher", "Texte à rechercher dans le terminal :")
        if ok and text and not self.terminals.active_terminal().console.find(text):
            QMessageBox.information(self, "Rechercher", "Aucune occurrence trouvée après le curseur.")

    def _clear_terminal(self) -> None:
        self.terminals.active_terminal().console.clear()

    def _rename_terminal(self) -> None:
        current = self.terminals.active_title()
        value, ok = QInputDialog.getText(self, "Renommer le terminal", "Nom :", text=current)
        if ok and value.strip():
            self.terminals.rename_active(value.strip())

    def _set_system_level(self, value: str) -> None:
        self.storage.settings["system_level"] = value
        self.storage.save()
        self.system.apply_preferences()

    def _show_storage(self) -> None:
        self.tabs.setCurrentWidget(self.system)
        self.system.show_storage_tab()

    def _show_network_security(self) -> None:
        self.tabs.setCurrentWidget(self.network_security)
        self.network_security.pages.setCurrentWidget(self.network_security.ports)

    def _check_updates(self) -> None:
        if not self.session:
            QMessageBox.information(self, "Mises à jour", "Aucun serveur connecté.")
            return
        self.statusBar().showMessage(tr('ui.checking_for_updates'))
        session = self.session
        run_async(
            session.update_count,
            lambda count: self._updates_done(session, int(count)),
            lambda text: self._updates_failed(session, text),
        )

    def _open_smart_logs(self) -> None:
        favorite = next((item for item in self.storage.favorites if item.kind == "smart_logs"), None)
        if favorite:
            self.favorites.run(favorite, self)

    def _force_reconnect(self) -> None:
        if self.updates_installing:
            QMessageBox.warning(
                self,
                "Mises à jour en cours",
                "La reconnexion forcée est désactivée pendant l’installation des mises à jour.",
            )
            return
        if not self.current_profile:
            self.connect_selected()
            return
        self.manual_disconnect = False
        self.reconnect_timer.stop()
        self._detach_session(close=True)
        self._start_connection(self.current_profile, self.secret, reconnect=True)

    def _export_configuration(self) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self, tr('ui.export_configuration_9c1534'), "remote-linux-config.json",
            "JSON (*.json)",
        )
        if target:
            shutil.copy2(self.storage.path, target)

    def _import_configuration(self) -> None:
        source, _ = QFileDialog.getOpenFileName(
            self, tr('ui.import_configuration_723588'), "", "JSON (*.json)"
        )
        if not source:
            return
        try:
            payload = self.storage.validate_payload(
                json.loads(Path(source).read_text(encoding="utf-8"))
            )
            del payload
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.critical(self, tr('ui.import'), f"{tr('fragment.invalid_configuration')} {tr(str(exc))}")
            return
        if QMessageBox.question(self, tr('ui.import'), tr('ui.replace_the_current_configuration')) == QMessageBox.Yes:
            try:
                self.storage.import_configuration(Path(source))
                self._reload_profiles()
                QMessageBox.information(self, tr('ui.import'), tr('ui.configuration_imported'))
            except (OSError, ValueError, TypeError) as exc:
                QMessageBox.critical(self, tr('ui.import'), f"{tr('fragment.import_failed')} {tr(str(exc))}")

    def _show_shortcuts(self) -> None:
        QMessageBox.information(
            self, tr('ui.keyboard_shortcuts'),
            tr('ui.ctrl_shift_n_new_connection_ctrl_shift_t_new'),
        )

    def _show_diagnostic(self) -> None:
        alive = bool(self.session and self.session.is_alive())
        tunnel = bool(self.session and self.session.tunnel)
        ssh_state = tr('ui.active_feminine' if alive else 'ui.inactive')
        tunnel_state = tr('ui.active_masculine' if tunnel else 'ui.not_used')
        text = (
            f"{tr('ui.ssh_session')} {ssh_state}\n"
            f"{tr('ui.integrated_cloudflare_transport')} {tunnel_state}\n"
            f"{tr('ui.reconnection_attempts')} {self.reconnect_attempts}/10"
        )
        QMessageBox.information(self, tr('ui.diagnostics'), text)

    def _show_about(self) -> None:
        AboutDialog(self).exec()
