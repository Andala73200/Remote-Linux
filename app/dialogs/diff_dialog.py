import difflib

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QPlainTextEdit, QPushButton, QVBoxLayout


class DiffDialog(QDialog):
    def __init__(self, remote_path: str, before: str, after: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Comparaison — {remote_path}")
        self.resize(1050, 700)
        diff = difflib.unified_diff(
            before.splitlines(), after.splitlines(), fromfile="Version téléchargée",
            tofile="Version locale modifiée", lineterm="",
        )
        output = QPlainTextEdit()
        output.setReadOnly(True)
        output.setFont(QFont("Consolas", 10))
        output.setPlainText("\n".join(diff) or "Aucune différence textuelle détectée.")
        close = QPushButton("Fermer")
        close.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(output)
        layout.addWidget(close)
