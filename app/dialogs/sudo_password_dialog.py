from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit,
    QVBoxLayout,
)

from app.i18n import tr


class SudoPasswordDialog(QDialog):
    def __init__(
        self,
        profile_name: str,
        default_password: str = "",
        secure_available: bool = True,
        secure_checked: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Privilèges administrateur")
        self.setMinimumWidth(500)

        title = QLabel(f"{tr('ui.sudo_password_for')} <b>{profile_name}</b>")
        self.password_edit = QLineEdit(default_password)
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("Mot de passe sudo")
        self.password_edit.selectAll()

        self.remember_session = QCheckBox("Mémoriser jusqu’à la déconnexion")
        self.remember_session.setChecked(True)
        self.remember_secure = QCheckBox("Enregistrer de manière sécurisée sur ce PC")
        self.remember_secure.setChecked(bool(secure_checked and secure_available))
        self.remember_secure.setEnabled(secure_available)

        if secure_available:
            info_text = (
                "Le mot de passe enregistré est confié au coffre d’identifiants de Windows. "
                "Il n’est jamais écrit dans le fichier de configuration."
            )
        else:
            info_text = (
                "Le coffre d’identifiants sécurisé n’est pas disponible. "
                "Le mot de passe pourra seulement être conservé en mémoire pour cette session."
            )
        info = QLabel(info_text)
        info.setWordWrap(True)
        info.setStyleSheet("color: #AEB8C5;")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Déverrouiller")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.password_edit)
        layout.addWidget(self.remember_session)
        layout.addWidget(self.remember_secure)
        layout.addWidget(info)
        layout.addWidget(buttons)
        self.password_edit.setFocus()

    def _accept_if_valid(self) -> None:
        if self.password_edit.text():
            self.accept()
            return
        self.password_edit.setFocus()

    def password(self) -> str:
        return self.password_edit.text()

    def remember_for_session(self) -> bool:
        return self.remember_session.isChecked()

    def remember_on_pc(self) -> bool:
        return self.remember_secure.isChecked()
