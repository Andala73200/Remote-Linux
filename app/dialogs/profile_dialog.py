from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.core.secure_credentials import SecureCredentialStore
from app.models import ConnectionProfile
from app.i18n import tr


class ProfileDialog(QDialog):
    def __init__(
        self,
        profile: ConnectionProfile | None = None,
        credentials: SecureCredentialStore | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Profil de connexion")
        self.setMinimumWidth(660)
        self.profile = profile or ConnectionProfile()
        self.credentials = credentials
        self._saved_cf_secret = bool(
            credentials and credentials.get_cloudflare_secret(self.profile)
        )
        self._saved_user_session = bool(
            credentials and credentials.get_cloudflare_user_token(self.profile)
        )

        self.name_edit = QLineEdit(self.profile.name)
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("SSH direct", "ssh")
        self.kind_combo.addItem("SSH via Cloudflare — Jeton de service", "cloudflare_service")
        self.kind_combo.addItem("SSH via Cloudflare — Connexion utilisateur", "cloudflare_user")
        self.user_edit = QLineEdit(self.profile.user)
        self.distribution_combo = QComboBox()
        self.distribution_combo.addItem("Détection automatique (recommandé)", "auto")
        self.distribution_combo.addItem("Debian / Ubuntu", "ubuntu")
        self.distribution_combo.addItem("RHEL / Fedora", "redhat")
        self.auth_combo = QComboBox()
        self.auth_combo.addItem("Mot de passe", "password")
        self.auth_combo.addItem("Clé privée", "key")
        self.key_edit = QLineEdit(self.profile.key_path)
        self.key_button = QPushButton("Parcourir…")
        self.key_button.clicked.connect(self._browse_key)

        self.direct_group = QGroupBox("Connexion SSH directe")
        self.host_edit = QLineEdit(self.profile.host)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(self.profile.port or 22)
        direct_form = QFormLayout(self.direct_group)
        direct_form.addRow("Hôte :", self.host_edit)
        direct_form.addRow("Port :", self.port_spin)

        self.cloudflare_group = QGroupBox("Cloudflare Access")
        self.cf_host_edit = QLineEdit(self.profile.cloudflare_host)
        self.cf_host_edit.setPlaceholderText("ssh.exemple.fr")
        cf_form = QFormLayout(self.cloudflare_group)
        cf_form.addRow("Hôte Cloudflare :", self.cf_host_edit)

        self.token_widget = QWidget()
        token_form = QFormLayout(self.token_widget)
        token_form.setContentsMargins(0, 0, 0, 0)
        self.cf_client_id_edit = QLineEdit(self.profile.cloudflare_client_id)
        self.cf_client_id_edit.setPlaceholderText("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.access")
        self.cf_secret_edit = QLineEdit()
        self.cf_secret_edit.setEchoMode(QLineEdit.Password)
        self.cf_secret_edit.setPlaceholderText(
            "Secret déjà enregistré — laisser vide pour le conserver"
            if self._saved_cf_secret
            else "Secret du jeton de service"
        )
        self.show_secret = QCheckBox("Afficher le secret")
        self.show_secret.toggled.connect(
            lambda checked: self.cf_secret_edit.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        self.cf_help_button = QToolButton()
        self.cf_help_button.setText("?")
        self.cf_help_button.setToolTip("Où trouver le jeton de service Cloudflare ?")
        self.cf_help_button.clicked.connect(self._show_service_token_help)
        id_row = QHBoxLayout()
        id_row.addWidget(self.cf_client_id_edit, 1)
        id_row.addWidget(self.cf_help_button)
        token_form.addRow("Client ID :", id_row)
        secret_box = QWidget()
        secret_layout = QVBoxLayout(secret_box)
        secret_layout.setContentsMargins(0, 0, 0, 0)
        secret_layout.addWidget(self.cf_secret_edit)
        secret_layout.addWidget(self.show_secret)
        token_form.addRow("Client Secret :", secret_box)
        cf_form.addRow(self.token_widget)

        self.user_widget = QWidget()
        user_layout = QVBoxLayout(self.user_widget)
        user_layout.setContentsMargins(0, 0, 0, 0)
        user_info = QLabel(
            "Remote Linux ouvrira ton navigateur lors de la première connexion. "
            "La session Cloudflare sera ensuite conservée dans le coffre sécurisé de Windows."
        )
        user_info.setWordWrap(True)
        user_info.setStyleSheet("color: #8FD3A8; font-weight: 600;")
        self.user_session_label = QLabel()
        self.forget_user_session_button = QPushButton("Oublier la session Cloudflare")
        self.forget_user_session_button.clicked.connect(self._forget_user_session)
        user_layout.addWidget(user_info)
        user_layout.addWidget(self.user_session_label)
        user_layout.addWidget(self.forget_user_session_button, 0)
        cf_form.addRow(self.user_widget)
        self._update_user_session_label()

        integrated = QLabel(
            "La connexion Cloudflare est gérée directement par Remote Linux. "
            "Aucun exécutable ou script Cloudflare n'est nécessaire sur ce PC."
        )
        integrated.setWordWrap(True)
        integrated.setStyleSheet("color: #8FD3A8; font-weight: 600;")
        cf_form.addRow("", integrated)

        form = QFormLayout()
        form.addRow("Nom :", self.name_edit)
        form.addRow("Type de connexion :", self.kind_combo)
        form.addRow("Utilisateur SSH :", self.user_edit)
        form.addRow("Distribution Linux :", self.distribution_combo)
        form.addRow("Authentification SSH :", self.auth_combo)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit)
        key_row.addWidget(self.key_button)
        form.addRow("Clé privée :", key_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Enregistrer")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.direct_group)
        layout.addWidget(self.cloudflare_group)
        layout.addWidget(buttons)

        self._set_combo_data(self.kind_combo, self.profile.connection_type())
        self._set_combo_data(self.auth_combo, self.profile.auth_method)
        self._set_combo_data(self.distribution_combo, self.profile.distribution)
        self.kind_combo.currentIndexChanged.connect(self._update_visibility)
        self.auth_combo.currentIndexChanged.connect(self._update_visibility)
        self._update_visibility()

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _update_visibility(self) -> None:
        connection_type = self.kind_combo.currentData()
        is_cloudflare = connection_type in ("cloudflare_service", "cloudflare_user")
        self.direct_group.setVisible(not is_cloudflare)
        self.cloudflare_group.setVisible(is_cloudflare)
        self.token_widget.setVisible(connection_type == "cloudflare_service")
        self.user_widget.setVisible(connection_type == "cloudflare_user")
        use_key = self.auth_combo.currentData() == "key"
        self.key_edit.setEnabled(use_key)
        self.key_button.setEnabled(use_key)

    def _update_user_session_label(self) -> None:
        state = tr('ui.yes' if self._saved_user_session else 'ui.no')
        self.user_session_label.setText(
            f"{tr('ui.saved_user_session')} {state}"
        )
        self.forget_user_session_button.setEnabled(self._saved_user_session)

    def _forget_user_session(self) -> None:
        if self.credentials and self.credentials.delete_cloudflare_user_token(self.profile):
            self._saved_user_session = False
            self._update_user_session_label()

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr('ui.select_private_key')
        )
        if path:
            self.key_edit.setText(path)

    def _show_service_token_help(self) -> None:
        QMessageBox.information(
            self,
            "Jeton de service Cloudflare",
            "Dans Cloudflare Zero Trust :\n\n"
            "Access → Service credentials → Service Tokens, puis crée un jeton.\n\n"
            "Copie le Client ID et le Client Secret, puis autorise ce jeton dans "
            "une politique « Service Auth » de l'application SSH.",
        )

    def _validate(self) -> None:
        connection_type = self.kind_combo.currentData()
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Profil incomplet", "Le nom du profil est obligatoire.")
            return
        if not self.user_edit.text().strip():
            QMessageBox.warning(self, "Profil incomplet", "L'utilisateur SSH est obligatoire.")
            return
        if connection_type == "ssh" and not self.host_edit.text().strip():
            QMessageBox.warning(self, "Profil incomplet", "L'hôte SSH est obligatoire.")
            return
        if connection_type in ("cloudflare_service", "cloudflare_user"):
            if not self.cf_host_edit.text().strip():
                QMessageBox.warning(self, "Profil incomplet", "L'hôte Cloudflare est obligatoire.")
                return
            if not self.credentials or not self.credentials.is_available():
                QMessageBox.critical(self, "Coffre Windows indisponible", "Le coffre sécurisé de Windows doit être disponible.")
                return
        if connection_type == "cloudflare_service":
            if not self.cf_client_id_edit.text().strip():
                QMessageBox.warning(self, "Profil incomplet", "Le Client ID Cloudflare est obligatoire.")
                return
            if not self.cf_secret_edit.text() and not self._saved_cf_secret:
                QMessageBox.warning(self, "Profil incomplet", "Le Client Secret Cloudflare est obligatoire.")
                return
        if self.auth_combo.currentData() == "key" and not self.key_edit.text().strip():
            QMessageBox.warning(self, "Profil incomplet", "Sélectionne une clé privée SSH.")
            return
        self.accept()

    def result_profile(self) -> ConnectionProfile:
        connection_type = str(self.kind_combo.currentData())
        self.profile.name = self.name_edit.text().strip()
        self.profile.kind = "ssh" if connection_type == "ssh" else "cloudflare"
        self.profile.cloudflare_auth_mode = "user_login" if connection_type == "cloudflare_user" else "service_token"
        self.profile.user = self.user_edit.text().strip()
        self.profile.distribution = str(self.distribution_combo.currentData())
        self.profile.auth_method = str(self.auth_combo.currentData())
        self.profile.key_path = self.key_edit.text().strip()
        self.profile.host = self.host_edit.text().strip()
        self.profile.port = self.port_spin.value()
        self.profile.cloudflare_host = self.cf_host_edit.text().strip()
        self.profile.cloudflare_client_id = self.cf_client_id_edit.text().strip()
        return self.profile

    def cloudflare_secret(self) -> str | None:
        if self.kind_combo.currentData() != "cloudflare_service":
            return None
        value = self.cf_secret_edit.text()
        return value if value else None
