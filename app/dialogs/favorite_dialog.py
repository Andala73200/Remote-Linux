from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from app.core.cd_command import is_cd_command
from app.i18n import tr
from app.models import FavoriteCommand


class FavoriteDialog(QDialog):
    def __init__(self, favorite: FavoriteCommand | None = None, command: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Commande favorite")
        self.setMinimumWidth(540)
        self.favorite = favorite or FavoriteCommand(command=command)
        self.name_edit = QLineEdit(self.favorite.name)
        self.folder_edit = QLineEdit(self.favorite.folder)
        self.folder_edit.setPlaceholderText("Maintenance/Mises à jour")
        self.command_edit = QPlainTextEdit(self.favorite.command)
        self.command_edit.setMinimumHeight(110)
        self.confirm_check = QCheckBox("Demander une confirmation avant l'exécution")
        self.confirm_check.setChecked(self.favorite.confirm)
        self.follow_tree_check = QCheckBox(tr("ui.follow_cd_in_file_tree"))
        self.follow_tree_check.setChecked(bool(self.favorite.follow_tree))
        self.command_edit.textChanged.connect(self._update_cd_option)
        hint = QLabel("Dossiers séparés par / — 3 niveaux maximum")
        hint.setStyleSheet("color: #9aa1aa;")
        form = QFormLayout()
        form.addRow("Nom :", self.name_edit)
        form.addRow("Dossier :", self.folder_edit)
        form.addRow("", hint)
        form.addRow("Commande :", self.command_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.confirm_check)
        layout.addWidget(self.follow_tree_check)
        layout.addWidget(buttons)
        self._update_cd_option()

    def _update_cd_option(self) -> None:
        is_cd = is_cd_command(self.command_edit.toPlainText())
        self.follow_tree_check.setVisible(is_cd)
        self.follow_tree_check.setEnabled(is_cd)

    def _validate(self) -> None:
        name = self.name_edit.text().strip()
        command = self.command_edit.toPlainText().strip()
        parts = [part.strip() for part in self.folder_edit.text().split("/") if part.strip()]
        if not name or not command:
            return
        if len(parts) > 3:
            QMessageBox.warning(self, "Dossier", "Trois niveaux de dossiers maximum.")
            return
        self.accept()

    def result_favorite(self) -> FavoriteCommand:
        parts = [part.strip() for part in self.folder_edit.text().split("/") if part.strip()]
        self.favorite.name = self.name_edit.text().strip()
        self.favorite.folder = "/".join(parts) or "Général"
        self.favorite.command = self.command_edit.toPlainText().strip()
        self.favorite.confirm = self.confirm_check.isChecked()
        self.favorite.follow_tree = (
            self.follow_tree_check.isChecked()
            and is_cd_command(self.favorite.command)
        )
        return self.favorite
