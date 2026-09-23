from __future__ import annotations

import os
import posixpath

from PySide6.QtCore import QDir, QTemporaryDir, QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.i18n import ntr, tr


class RemoteFileTransferMixin:
    def _upload_drop(self, local_paths: list[str], item) -> None:
        target = self.root_path
        if item and item.data(0, self.PATH_ROLE):
            target = str(item.data(0, self.PATH_ROLE))
            if not item.data(0, self.DIR_ROLE):
                target = posixpath.dirname(target)
        self._upload(local_paths, target)

    def _choose_upload(self, remote_dir: str) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, tr('ui.upload_files'))
        if files:
            self._upload(files, remote_dir)

    def _upload(self, paths: list[str], remote_dir: str) -> None:
        if not self.session:
            return
        self.info.setText(f"{tr('ui.transfer_added_to')} {remote_dir}")
        self.transfers.enqueue_upload(
            self.session, paths, remote_dir,
            lambda count: self._upload_done(count, remote_dir),
        )

    def _upload_done(self, count: object, remote_dir: str) -> None:
        file_label = ntr(int(count), 'ui.file_sent', 'ui.files_sent')
        self.info.setText(f"{count} {file_label}")
        self._record(remote_dir, "envoi", True)
        self.reload_root()

    def _download(self, remote_path: str, is_dir: bool = False) -> None:
        if not self.session:
            return
        if is_dir:
            local_dir = QFileDialog.getExistingDirectory(
                self, tr('ui.download_folder_to')
            )
            if not local_dir:
                return
            target = os.path.join(local_dir, posixpath.basename(remote_path.rstrip("/")))
            if os.path.exists(target) and not self._confirm_overwrite(target, is_directory=True):
                return
            self.transfers.enqueue_download(
                self.session, [remote_path], local_dir, True,
                lambda result: self._download_done(remote_path, str(result[0]) if result else target),
            )
            return
        target, _ = QFileDialog.getSaveFileName(
            self, tr('ui.download_action'), posixpath.basename(remote_path)
        )
        if not target or os.path.exists(target) and not self._confirm_overwrite(target, is_directory=False):
            return
        self.transfers.enqueue_download_file(
            self.session, remote_path, target,
            lambda result: self._download_done(remote_path, str(result)),
        )

    def _download_done(self, remote_path: str, target: str) -> None:
        self.info.setText(f"{tr('ui.downloaded')} {target}")
        self._record(remote_path, "téléchargé")

    def _prepare_remote_drag(self, remote_paths: list[str]) -> None:
        """Stage remote items asynchronously, then start a native Windows drag."""
        session = self.session
        if not session or not remote_paths:
            return
        paths_key = tuple(dict.fromkeys(str(path) for path in remote_paths if path))
        if not paths_key:
            return

        prepared = getattr(self, "_prepared_remote_drag", None)
        if prepared:
            same_request = (
                prepared.get("session") is session
                and prepared.get("remote_paths") == paths_key
            )
            if same_request:
                self._launch_prepared_drag(int(prepared["token"]))
                return
            self._discard_prepared_drag()

        if bool(getattr(self, "_drag_prepare_active", False)):
            self.info.setText(tr('ui.temporary_download_before_drag_and_drop'))
            return

        template = QDir.tempPath() + "/remote-linux-drag-XXXXXX"
        temp = QTemporaryDir(template)
        if not temp.isValid():
            QMessageBox.critical(
                self, tr('ui.drag_and_drop'), tr('ui.unable_to_create_the_temporary_folder')
            )
            return

        token = int(getattr(self, "_drag_prepare_serial", 0)) + 1
        self._drag_prepare_serial = token
        self._drag_prepare_active = True
        self.info.setText(tr('ui.temporary_download_before_drag_and_drop'))
        self.transfers.enqueue_download(
            session,
            list(paths_key),
            temp.path(),
            True,
            on_finished=lambda ok, result: self._remote_drag_download_finished(
                token, session, paths_key, temp, ok, result
            ),
            initial_status=tr('ui.temporary_download_before_drag_and_drop'),
            retryable=False,
        )

    def _remote_drag_download_finished(
        self,
        token: int,
        session,
        remote_paths: tuple[str, ...],
        temp: QTemporaryDir,
        success: bool,
        result: object,
    ) -> None:
        if token != int(getattr(self, "_drag_prepare_serial", 0)):
            temp.remove()
            return
        self._drag_prepare_active = False
        if not success or self.session is not session:
            temp.remove()
            return

        local_paths = [str(path) for path in (result or [])]
        if not local_paths or not all(os.path.exists(path) for path in local_paths):
            temp.remove()
            self._show_error(tr('ui.unknown_transfer_error'))
            return

        self._prepared_remote_drag = {
            "token": token,
            "session": session,
            "remote_paths": remote_paths,
            "local_paths": local_paths,
            "temp": temp,
        }
        QTimer.singleShot(120000, lambda t=token: self._expire_prepared_drag(t))

        same_selection = tuple(self.tree.selected_remote_paths()) == remote_paths
        if QApplication.mouseButtons() & Qt.LeftButton and same_selection:
            QTimer.singleShot(0, lambda t=token: self._launch_prepared_drag(t))
        else:
            self.info.setText(tr('ui.drag_ready_drag_again'))

    def _launch_prepared_drag(self, token: int) -> None:
        prepared = getattr(self, "_prepared_remote_drag", None)
        if not prepared or int(prepared.get("token", -1)) != token:
            return
        if prepared.get("session") is not self.session:
            self._discard_prepared_drag()
            return

        local_paths = list(prepared.get("local_paths") or [])
        temp = prepared.get("temp")
        self._prepared_remote_drag = None
        if not local_paths or not all(os.path.exists(path) for path in local_paths):
            if isinstance(temp, QTemporaryDir):
                temp.remove()
            return

        self.tree.start_local_drag(local_paths)
        self.info.setText(self.root_path)
        if isinstance(temp, QTemporaryDir):
            QTimer.singleShot(60000, temp.remove)

    def _expire_prepared_drag(self, token: int) -> None:
        prepared = getattr(self, "_prepared_remote_drag", None)
        if not prepared or int(prepared.get("token", -1)) != token:
            return
        self._discard_prepared_drag()

    def _discard_prepared_drag(self) -> None:
        prepared = getattr(self, "_prepared_remote_drag", None)
        self._prepared_remote_drag = None
        if not prepared:
            return
        temp = prepared.get("temp")
        if isinstance(temp, QTemporaryDir):
            temp.remove()

    def _confirm_overwrite(self, path: str, is_directory: bool = False) -> bool:
        if is_directory:
            text = (
                "Le dossier Windows existe déjà :\n"
                f"{path}\n\n"
                "Le téléchargement sera fusionné avec ce dossier. Les fichiers portant "
                "le même nom seront remplacés ; les autres fichiers locaux seront conservés.\n\n"
                "Continuer ?"
            )
            title = "Fusion de dossiers"
        else:
            text = f"Le fichier Windows existe déjà :\n{path}\n\nLe remplacer ?"
            title = "Remplacement Windows"
        return QMessageBox.question(self, title, text) == QMessageBox.Yes
