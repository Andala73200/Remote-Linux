from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QMessageBox, QVBoxLayout,
)


class StorageActionDialog(QDialog):
    DESTRUCTIVE = {"format", "initialize", "remove_partition"}

    def __init__(self, action: str, payload: dict, parent=None):
        super().__init__(parent)
        self.action = action
        self.payload = dict(payload)
        self.path = str(payload.get("path") or "")
        self.setMinimumWidth(570)
        self.setWindowTitle(self._title())
        self.mountpoint = QLineEdit(str(payload.get("mount") or self._default_mount()))
        self.filesystem = QComboBox()
        for label, value in (("ext4 — recommandé", "ext4"), ("XFS", "xfs"), ("Btrfs", "btrfs"), ("exFAT", "exfat"), ("NTFS", "ntfs")):
            self.filesystem.addItem(label, value)
        self.label_edit = QLineEdit(str(payload.get("label") or ""))
        self.automount = QCheckBox("Monter automatiquement au démarrage avec UUID et option nofail")
        self.automount.setChecked(action in {"automount", "initialize"})
        self.confirmation = QLineEdit()
        self.confirmation.setPlaceholderText(self._confirmation_text())
        warning = QLabel(self._warning_html())
        warning.setWordWrap(True)
        warning.setTextFormat(Qt.RichText)
        form = QFormLayout()
        if action in {"mount", "automount", "initialize"}:
            form.addRow("Point de montage :", self.mountpoint)
        if action in {"format", "initialize"}:
            form.addRow("Système de fichiers :", self.filesystem)
            form.addRow("Nom du volume :", self.label_edit)
        if action in {"mount", "automount", "initialize"}:
            form.addRow("Démarrage :", self.automount)
        if action in self.DESTRUCTIVE:
            form.addRow("Confirmation :", self.confirmation)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_button = self.buttons.button(QDialogButtonBox.Ok)
        self.ok_button.setText(self._ok_text())
        self.ok_button.setStyleSheet("background:#8f2832;" if action in self.DESTRUCTIVE else "")
        self.buttons.accepted.connect(self._accept_checked)
        self.buttons.rejected.connect(self.reject)
        self.confirmation.textChanged.connect(self._update_ok)
        layout = QVBoxLayout(self)
        layout.addWidget(warning)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        self._update_ok()

    def values(self) -> dict:
        return {
            **self.payload,
            "path": self.path,
            "mountpoint": self.mountpoint.text().strip(),
            "filesystem": str(self.filesystem.currentData()),
            "label": self.label_edit.text().strip(),
            "automount": self.automount.isChecked(),
        }

    def _accept_checked(self) -> None:
        if self.action in {"mount", "automount", "initialize"} and not self.mountpoint.text().strip().startswith("/"):
            QMessageBox.warning(self, "Point de montage", "Le point de montage doit être un chemin Linux absolu.")
            return
        self.accept()

    def _update_ok(self) -> None:
        enabled = self.action not in self.DESTRUCTIVE or self.confirmation.text().strip() == self._confirmation_text()
        self.ok_button.setEnabled(enabled)

    def _default_mount(self) -> str:
        label = str(self.payload.get("label") or "").strip()
        name = str(self.payload.get("name") or "volume").strip()
        safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in (label or name))
        return f"/mnt/{safe or 'volume'}"

    def _title(self) -> str:
        return {
            "mount": "Monter un volume", "unmount": "Démonter un volume",
            "automount": "Montage automatique", "format": "Formater une partition",
            "initialize": "Initialiser un disque", "remove_partition": "Supprimer une partition",
        }.get(self.action, "Gestion du stockage")

    def _ok_text(self) -> str:
        return {
            "mount": "Monter", "unmount": "Démonter", "automount": "Enregistrer",
            "format": "FORMATER", "initialize": "INITIALISER", "remove_partition": "SUPPRIMER",
        }.get(self.action, "Valider")

    def _confirmation_text(self) -> str:
        verb = {"format": "FORMATER", "initialize": "INITIALISER", "remove_partition": "SUPPRIMER"}.get(self.action, "CONFIRMER")
        return f"{verb} {self.path}"

    def _warning_html(self) -> str:
        model = " ".join(str(self.payload.get(key) or "") for key in ("vendor", "model")).strip()
        size = int(self.payload.get("size") or 0)
        common = f"<b>Périphérique :</b> {self.path}<br><b>Modèle :</b> {model or 'Non communiqué'}<br><b>Taille brute :</b> {size:,} octets"
        if self.action == "format":
            return f"<div style='color:#ff7b83'><b>⚠ ACTION DESTRUCTIVE</b><br>{common}<br><br>Le formatage rendra les données actuelles inaccessibles. Le volume sera démonté avant l’opération.</div>"
        if self.action == "initialize":
            return f"<div style='color:#ff4d5a'><b>⛔ ACTION EXTRÊME</b><br>{common}<br><br>La table de partitions et toutes les signatures du disque seront supprimées, puis une nouvelle partition sera créée.</div>"
        if self.action == "remove_partition":
            return f"<div style='color:#ff7b83'><b>⚠ ACTION DESTRUCTIVE</b><br>{common}<br><br>La partition sera retirée de la table du disque. Ses données ne seront plus accessibles normalement.</div>"
        if self.action == "unmount":
            return f"<b>⏏ Démontage</b><br>{common}<br><br>Les applications utilisant ce volume peuvent être interrompues."
        return f"<b>Gestion du stockage</b><br>{common}"
