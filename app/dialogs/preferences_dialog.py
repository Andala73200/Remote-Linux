from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from app.i18n_catalog import LANGUAGE_CHOICES
from app.i18n import tr
from app.core.terminal_shortcut import (
    DEFAULT_COMPLETION_SHORTCUT, validate_completion_shortcut,
)

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QKeySequenceEdit,
    QSpinBox,
    QVBoxLayout,
)


class PreferencesDialog(QDialog):
    forget_sudo_requested = Signal()
    ssh_keys_requested = Signal()

    def __init__(
        self,
        settings: dict[str, object],
        profile_name: str = "",
        sudo_saved: bool = False,
        secure_available: bool = True,
        secure_backend: str = "",
        ssh_keys_available: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Préférences")
        self.setMinimumWidth(570)
        self.settings = settings

        self.language = QComboBox()
        self.language.addItem("Automatic", "auto")
        for code, native_name in LANGUAGE_CHOICES:
            self.language.addItem(native_name, code)
        self.language.setToolTip("Use the Windows display language.")
        self._select(self.language, str(settings.get("language", "auto")))

        self.protected = QCheckBox("Activer le mode protégé")
        self.protected.setChecked(bool(settings.get("protected_mode", True)))
        self.preview = QComboBox()
        self.preview.addItem("Toujours afficher", "always")
        self.preview.addItem("Seulement pour les actions sensibles", "sensitive")
        self.preview.addItem("Ne pas demander", "never")
        self._select(self.preview, str(settings.get("command_preview", "sensitive")))
        self.system_level = QComboBox()
        self.system_level.addItem("Simple", "simple")
        self.system_level.addItem("Avancé", "advanced")
        self.system_level.addItem("Expert", "expert")
        self._select(self.system_level, str(settings.get("system_level", "advanced")))
        self.disk_alert = QSpinBox()
        self.disk_alert.setRange(50, 100)
        self.disk_alert.setSuffix(" %")
        self.disk_alert.setValue(int(settings.get("disk_alert_percent", 85)))
        self.temp_alert = QSpinBox()
        self.temp_alert.setRange(40, 120)
        self.temp_alert.setSuffix(" °C")
        self.temp_alert.setValue(int(settings.get("temperature_alert_c", 75)))
        self.delete_nonlocal = QCheckBox(
            "Autoriser la suppression des services non créés localement"
        )
        self.delete_nonlocal.setChecked(
            bool(settings.get("allow_delete_nonlocal_services", False))
        )
        self.delete_nonlocal.setToolTip(
            "Permet de supprimer aussi les unités fournies par le système ou un paquet. "
            "Option désactivée par défaut."
        )

        form = QFormLayout()
        form.addRow("Langue :", self.language)
        form.addRow("Sécurité :", self.protected)
        form.addRow("Commande réelle :", self.preview)
        form.addRow("Détails système :", self.system_level)
        form.addRow("Alerte disque :", self.disk_alert)
        form.addRow("Alerte température :", self.temp_alert)
        form.addRow("Services systemd :", self.delete_nonlocal)

        notification_box = QGroupBox("Notifications")
        notification_layout = QVBoxLayout(notification_box)
        self.windows_notifications = QCheckBox("Afficher les notifications Windows")
        self.windows_notifications.setChecked(
            bool(settings.get("windows_notifications", True))
        )
        self.windows_notifications.setToolTip(
            "Affiche une notification Windows lorsque Remote Linux est réduit "
            "ou en arrière-plan et qu’un événement important survient."
        )
        notification_layout.addWidget(self.windows_notifications)

        terminal_box = QGroupBox("Terminal")
        terminal_layout = QVBoxLayout(terminal_box)
        self.right_click_paste = QCheckBox("Clic droit = coller immédiatement")
        self.right_click_paste.setChecked(
            bool(settings.get("terminal_right_click_paste", False))
        )
        self.right_click_paste.setToolTip(
            "Quand cette option est activée, un clic droit dans le terminal colle "
            "immédiatement le contenu du presse-papiers au lieu d’ouvrir le menu contextuel."
        )
        terminal_layout.addWidget(self.right_click_paste)

        cd_row = QHBoxLayout()
        cd_row.addWidget(QLabel(tr("ui.cd_tree_sync")))
        self.cd_tree_sync = QComboBox()
        self.cd_tree_sync.addItem(tr("ui.ask_each_time"), "ask")
        self.cd_tree_sync.addItem(tr("ui.always_follow"), "always")
        self.cd_tree_sync.addItem(tr("ui.never_follow"), "never")
        self._select(
            self.cd_tree_sync,
            str(settings.get("terminal_cd_tree_sync", "ask")),
        )
        cd_row.addWidget(self.cd_tree_sync, 1)
        terminal_layout.addLayout(cd_row)

        self.venv_prompt = QCheckBox(tr('ui.suggest_venv_activation'))
        self.venv_prompt.setChecked(
            bool(settings.get("terminal_venv_prompt_enabled", True))
        )
        terminal_layout.addWidget(self.venv_prompt)

        shortcut_row = QHBoxLayout()
        shortcut_label = QLabel(tr("ui.graphical_completion_shortcut"))
        current_shortcut = str(
            settings.get("terminal_completion_shortcut", DEFAULT_COMPLETION_SHORTCUT)
            or DEFAULT_COMPLETION_SHORTCUT
        )
        self.completion_shortcut = QKeySequenceEdit(QKeySequence(current_shortcut))
        self.completion_shortcut.setMaximumSequenceLength(1)
        self.completion_shortcut.setClearButtonEnabled(True)
        self.completion_shortcut.setToolTip(
            tr("ui.graphical_completion_shortcut_tooltip")
        )
        self.completion_shortcut_info = QLabel("ⓘ")
        self.completion_shortcut_info.setToolTip(
            tr("ui.graphical_completion_forbidden_info")
        )
        shortcut_row.addWidget(shortcut_label)
        shortcut_row.addWidget(self.completion_shortcut, 1)
        shortcut_row.addWidget(self.completion_shortcut_info)
        terminal_layout.addLayout(shortcut_row)

        ssh_box = QGroupBox("Clés SSH du serveur")
        ssh_layout = QVBoxLayout(ssh_box)
        ssh_info = QLabel(
            "Consulte, ajoute ou retire les clés de ~/.ssh/authorized_keys "
            "pour l’utilisateur SSH actuellement connecté."
        )
        ssh_info.setWordWrap(True)
        self.ssh_keys_button = QPushButton("Gérer les clés SSH autorisées…")
        self.ssh_keys_button.setEnabled(ssh_keys_available)
        self.ssh_keys_button.setToolTip(
            "Une connexion active est nécessaire."
            if not ssh_keys_available else "Ouvrir la gestion des clés du serveur connecté."
        )
        self.ssh_keys_button.clicked.connect(self.ssh_keys_requested)
        ssh_layout.addWidget(ssh_info)
        ssh_layout.addWidget(self.ssh_keys_button)

        credential_box = QGroupBox("Mot de passe sudo")
        credential_layout = QVBoxLayout(credential_box)
        self.save_sudo = QCheckBox(
            "Enregistrer le mot de passe sudo dans le coffre sécurisé de Windows"
        )
        self.save_sudo.setChecked(
            bool(settings.get("save_sudo_password", False) or sudo_saved)
        )
        self.save_sudo.setToolTip(
            "Le mot de passe sera enregistré lors de la prochaine authentification sudo. "
            "Il ne sera jamais écrit dans le fichier de configuration."
        )
        self.sudo_status = QLabel()
        self.sudo_status.setWordWrap(True)
        self.backend_status = QLabel()
        self.backend_status.setWordWrap(True)
        self.backend_status.setStyleSheet("color: #AEB8C5;")
        self.backend_status.setText(
            f"Coffre utilisé : {secure_backend}" if secure_available and secure_backend else ""
        )
        self.forget_sudo_button = QPushButton("Oublier le mot de passe enregistré")
        self.forget_sudo_button.clicked.connect(self._forget_sudo)
        row = QHBoxLayout()
        row.addWidget(self.forget_sudo_button)
        row.addStretch(1)
        credential_layout.addWidget(self.save_sudo)
        credential_layout.addWidget(self.sudo_status)
        credential_layout.addWidget(self.backend_status)
        credential_layout.addLayout(row)
        self.set_sudo_state(profile_name, sudo_saved, secure_available)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Enregistrer")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self._accept_preferences)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(notification_box)
        layout.addWidget(terminal_box)
        layout.addWidget(ssh_box)
        layout.addWidget(credential_box)
        layout.addWidget(buttons)

    def set_sudo_state(self, profile_name: str, saved: bool, secure_available: bool) -> None:
        can_configure = bool(profile_name and secure_available)
        self.save_sudo.setEnabled(can_configure)
        if not profile_name:
            self.sudo_status.setText(
                "Sélectionne ou connecte un profil pour gérer son mot de passe sudo."
            )
            self.forget_sudo_button.setEnabled(False)
            return
        if not secure_available:
            self.sudo_status.setText(
                "Le coffre sécurisé n'est pas disponible. Vérifie que l'application "
                "est lancée sous Windows avec le même compte utilisateur."
            )
            self.forget_sudo_button.setEnabled(False)
            return
        if saved:
            self.sudo_status.setText(
                f"Un mot de passe sudo est enregistré de façon sécurisée pour « {profile_name} »."
            )
            self.forget_sudo_button.setEnabled(True)
        else:
            self.sudo_status.setText(
                f"Aucun mot de passe sudo n'est encore enregistré pour « {profile_name} ». "
                "Si la case est cochée, il le sera à la prochaine demande sudo."
            )
            self.forget_sudo_button.setEnabled(False)

    def _forget_sudo(self) -> None:
        self.forget_sudo_requested.emit()

    def _accept_preferences(self) -> None:
        shortcut = self._completion_shortcut_text()
        valid, _reason = validate_completion_shortcut(shortcut)
        if not valid:
            QMessageBox.warning(
                self,
                tr("ui.invalid_shortcut"),
                tr("ui.invalid_completion_shortcut"),
            )
            return
        self.accept()

    def _completion_shortcut_text(self) -> str:
        return self.completion_shortcut.keySequence().toString(
            QKeySequence.SequenceFormat.PortableText
        ).strip()

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def values(self) -> dict[str, object]:
        return {
            "language": self.language.currentData(),
            "protected_mode": self.protected.isChecked(),
            "command_preview": self.preview.currentData(),
            "system_level": self.system_level.currentData(),
            "disk_alert_percent": self.disk_alert.value(),
            "temperature_alert_c": self.temp_alert.value(),
            "allow_delete_nonlocal_services": self.delete_nonlocal.isChecked(),
            "save_sudo_password": self.save_sudo.isChecked(),
            "windows_notifications": self.windows_notifications.isChecked(),
            "terminal_right_click_paste": self.right_click_paste.isChecked(),
            "terminal_venv_prompt_enabled": self.venv_prompt.isChecked(),
            "terminal_cd_tree_sync": self.cd_tree_sync.currentData(),
            "terminal_completion_shortcut": self._completion_shortcut_text(),
        }
