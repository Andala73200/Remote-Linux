from __future__ import annotations

import posixpath

from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox

from app.core.async_task import run_async


_FILE_TYPES = (
    ("Python (.py)", ".py", "#!/usr/bin/env python3\n\n"),
    ("JSON (.json)", ".json", "{\n}\n"),
    ("YAML (.yaml)", ".yaml", "---\n"),
    (
        "Service systemd (.service)",
        ".service",
        "[Unit]\nDescription=\nAfter=network.target\n\n"
        "[Service]\nType=simple\nExecStart=\nRestart=on-failure\n\n"
        "[Install]\nWantedBy=multi-user.target\n",
    ),
)


class RemoteNewItemMixin:
    def _add_new_menu(self, menu: QMenu, remote_dir: str) -> None:
        new_menu = menu.addMenu("Nouveau")
        new_menu.addAction("Dossier…", lambda: self._create_directory(remote_dir))
        file_menu = new_menu.addMenu("Fichier")
        for label, extension, template in _FILE_TYPES:
            file_menu.addAction(
                label,
                lambda _checked=False, ext=extension, body=template: self._create_file(
                    remote_dir, ext, body
                ),
            )

    def _create_file(self, parent: str, extension: str, template: str) -> None:
        name, ok = QInputDialog.getText(
            self,
            "Nouveau fichier",
            f"Nom du fichier ({extension}) :",
        )
        clean = name.strip()
        if not ok or not clean:
            return
        if "/" in clean or clean in {".", ".."}:
            QMessageBox.warning(self, "Nouveau fichier", "Nom de fichier invalide.")
            return
        if not clean.casefold().endswith(extension.casefold()):
            clean += extension
        path = posixpath.join(parent.rstrip("/") or "/", clean)
        session = self.session
        if not session:
            return

        def done(_result: object) -> None:
            self.info.setText(f"Fichier créé : {path}")
            self._record(path, "créé")
            self.reload_root()

        run_async(
            lambda: session.create_remote_text_file(path, template),
            done,
            self._show_error,
            guard=lambda: self.session is session,
        )
