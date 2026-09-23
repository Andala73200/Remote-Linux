from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from app.i18n import tr


class UpdateInstallDialog(QDialog):
    """Modeless update window shown for the whole installation lifecycle."""

    def __init__(self, command: str, parent=None):
        super().__init__(parent)
        self.command = command
        self.resize(920, 620)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Consolas", 10))

        copy_command = QPushButton(tr("ui.copy_command"))
        copy_command.clicked.connect(self._copy_command)
        close = QPushButton(tr("ui.close"))
        close.clicked.connect(self.close)

        buttons = QHBoxLayout()
        buttons.addWidget(copy_command)
        buttons.addStretch(1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.output, 1)
        layout.addLayout(buttons)
        self.set_running(command)

    def _copy_command(self) -> None:
        QApplication.clipboard().setText(self.command)

    def set_running(self, command: str) -> None:
        self.command = command
        self.setWindowTitle(tr("ui.installing_updates"))
        self.status.setText(tr("ui.installation_in_progress"))
        self.output.setPlainText(f"{tr('ui.command_to_run')}\n{command}")

    def set_result(self, success: bool, text: str) -> None:
        title = tr("ui.updates_installed" if success else "ui.installation_failed")
        self.setWindowTitle(title)
        self.status.setText(title)
        self.output.setPlainText(str(text or ""))
