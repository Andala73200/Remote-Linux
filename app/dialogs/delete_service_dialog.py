from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QVBoxLayout,
)


class DeleteServiceDialog(QDialog):
    def __init__(self, details: dict[str, object], parent=None):
        super().__init__(parent)
        self.details = details
        service = str(details.get("service") or "")
        path = str(details.get("path") or "")
        package = str(details.get("package") or "Aucun paquet identifié")
        local = bool(details.get("local"))
        self.expected = f"SUPPRIMER {service}"
        self.setWindowTitle("Supprimer un service systemd")
        self.setMinimumWidth(620)
        warning = QLabel(
            "⚠ ACTION DESTRUCTIVE\n\n"
            "La définition systemd sera sauvegardée, puis supprimée. "
            "L’application et ses autres fichiers ne seront pas supprimés."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "background:#4a1717;color:#ffb3b3;border:1px solid #a84444;"
            "padding:12px;font-weight:bold;border-radius:6px;"
        )
        form = QFormLayout()
        form.addRow("Service :", QLabel(service))
        form.addRow("Fichier :", QLabel(path or "Aucun fichier"))
        form.addRow("Origine :", QLabel("Créé localement" if local else "Système / paquet"))
        form.addRow("Paquet :", QLabel(package))
        self.package_checkbox = QCheckBox(
            "Désinstaller le paquet associé plutôt que supprimer son fichier systemd"
        )
        self.package_checkbox.setChecked(bool(package and not local and package != "Aucun paquet identifié"))
        self.package_checkbox.setVisible(bool(package and not local and package != "Aucun paquet identifié"))
        self.package_checkbox.setToolTip(
            "Méthode recommandée : le gestionnaire de paquets retire proprement le service et ses fichiers."
        )
        notes = QPlainTextEdit()
        notes.setReadOnly(True)
        notes.setMaximumHeight(95)
        notes.setPlainText(
            "Le service sera arrêté et désactivé avant suppression.\n"
            "Une sauvegarde sera créée dans /var/backups/remote-linux/systemd/.\n"
            "systemctl daemon-reload et reset-failed seront exécutés ensuite."
        )
        self.confirm = QLineEdit()
        self.confirm.setPlaceholderText(self.expected)
        self.confirm.textChanged.connect(self._validate)
        form.addRow(f"Saisis « {self.expected} » :", self.confirm)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.delete_button = self.buttons.addButton("Supprimer définitivement", QDialogButtonBox.DestructiveRole)
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(warning)
        layout.addLayout(form)
        layout.addWidget(self.package_checkbox)
        layout.addWidget(notes)
        layout.addWidget(self.buttons)

    def remove_package(self) -> bool:
        return self.package_checkbox.isVisible() and self.package_checkbox.isChecked()

    def _validate(self, text: str) -> None:
        self.delete_button.setEnabled(text.strip() == self.expected)
