from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget

from app.core.async_task import run_async
from app.core.permission_errors import is_permission_denied
from app.core.session import RemoteSession
from app.dialogs.diff_dialog import DiffDialog
from app.i18n import tr

MAX_TEXT_PREVIEW = 2 * 1024 * 1024


class RemoteConflictError(RuntimeError):
    pass


@dataclass
class WatchedFile:
    profile_id: str
    remote_path: str
    local_path: Path
    digest: str
    local_size: int
    local_mtime_ns: int
    remote_signature: tuple[int, int, int]
    original_text: str | None
    changed_at: float = 0.0
    prompting: bool = False
    alerted: bool = False
    prompted_digest: str = ""


class TempEditManager(QObject):
    file_changed = Signal(str)
    file_resolved = Signal(str)

    def __init__(
        self,
        parent: QWidget,
        session_provider: Callable[[], RemoteSession | None],
        sudo_password_provider: Callable[[], object] | None = None,
    ):
        super().__init__(parent)
        self.parent_widget = parent
        self.session_provider = session_provider
        self.sudo_password_provider = sudo_password_provider
        self.root = Path(tempfile.mkdtemp(prefix="RemoteLinux_"))
        self.watched: dict[str, WatchedFile] = {}
        self.watched_by_identity: dict[str, WatchedFile] = {}
        self.opening: set[str] = set()
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._poll)
        self.timer.start()

    def open_remote(self, session: RemoteSession, remote_path: str) -> None:
        if self.closed:
            return
        safe_name = os.path.basename(remote_path) or "fichier"
        identity = f"{session.profile.id}:{remote_path}"
        existing = self.watched_by_identity.get(identity)
        if existing and existing.local_path.exists():
            local_changed = (
                existing.alerted
                or existing.prompting
                or self._digest(existing.local_path) != existing.digest
            )
            if local_changed:
                self._open_local(existing.local_path)
                return
        elif existing:
            self.watched.pop(str(existing.local_path), None)
            self.watched_by_identity.pop(identity, None)
            existing = None
        if identity in self.opening:
            return
        self.opening.add(identity)
        prefix = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16] + "_"
        folder = Path(tempfile.mkdtemp(prefix=prefix, dir=self.root))
        local = folder / safe_name

        def task() -> tuple[int, int, int]:
            signature = session.remote_file_signature(remote_path)
            session.download_file(remote_path, str(local))
            return signature

        def done(signature: object) -> None:
            self.opening.discard(identity)
            if self.closed:
                return
            current = self.watched_by_identity.get(identity)
            if current and current.local_path.exists():
                local_changed = (
                    current.alerted
                    or current.prompting
                    or self._digest(current.local_path) != current.digest
                )
                if local_changed:
                    shutil.rmtree(folder, ignore_errors=True)
                    self._open_local(current.local_path)
                    return
                self.watched.pop(str(current.local_path), None)
            size, mtime_ns = self._local_state(local)
            watched = WatchedFile(
                profile_id=session.profile.id,
                remote_path=remote_path,
                local_path=local,
                digest=self._digest(local),
                local_size=size,
                local_mtime_ns=mtime_ns,
                remote_signature=tuple(signature),
                original_text=self._read_text(local),
            )
            self.watched[str(local)] = watched
            self.watched_by_identity[identity] = watched
            self._open_local(local)

        def failed(text: str) -> None:
            self.opening.discard(identity)
            shutil.rmtree(folder, ignore_errors=True)
            if self.closed:
                return
            QMessageBox.critical(
                self.parent_widget,
                "Téléchargement",
                text,
            )

        run_async(
            task,
            done,
            failed,
        )

    def _open_local(self, local_path: Path) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(local_path))):
            QMessageBox.warning(
                self.parent_widget,
                "Ouverture",
                f"{tr('ui.unable_to_open')}\n{local_path}",
            )

    def _poll(self) -> None:
        now = time.monotonic()
        for watched in list(self.watched.values()):
            if not watched.local_path.exists() or watched.prompting:
                continue
            size, mtime_ns = self._local_state(watched.local_path)
            changed = (
                size != watched.local_size
                or mtime_ns != watched.local_mtime_ns
            )
            if not changed:
                watched.changed_at = 0.0
                continue
            if not watched.changed_at:
                watched.changed_at = now
                continue
            if now - watched.changed_at < 1.5:
                continue
            digest = self._digest(watched.local_path)
            if digest == watched.digest:
                self._accept_local_state(watched)
                continue
            if not watched.alerted:
                watched.alerted = True
                self.file_changed.emit(watched.remote_path)
            # Never nag again for the exact same file contents. A new save
            # produces a new digest and is allowed to raise a new decision.
            if digest == watched.prompted_digest:
                watched.changed_at = 0.0
                continue
            watched.prompted_digest = digest
            watched.prompting = True
            QTimer.singleShot(
                0,
                lambda item=watched, value=digest: self._ask_upload(item, value),
            )

    def _ask_upload(self, watched: WatchedFile, digest: str) -> None:
        box = QMessageBox(self.parent_widget)
        box.setWindowTitle(tr('ui.remote_file_modified'))
        box.setText(
            f"{tr('ui.the_file_was_modified_locally')}\n{watched.remote_path}"
        )
        compare = box.addButton(tr('ui.compare'), QMessageBox.ActionRole)
        current_text = self._read_text(watched.local_path)
        compare.setEnabled(
            watched.original_text is not None and current_text is not None
        )
        upload = box.addButton(tr('ui.update_server'), QMessageBox.AcceptRole)
        backup = box.addButton(
            tr('ui.save_and_update'),
            QMessageBox.ActionRole,
        )
        ignore = box.addButton(tr('ui.ignore'), QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()

        if clicked is compare:
            DiffDialog(
                watched.remote_path,
                watched.original_text or "",
                current_text or "",
                self.parent_widget,
            ).exec()
            # Comparing is informative, not a request to nag again. The same
            # content remains pending but will only prompt after another save.
            watched.prompting = False
            watched.changed_at = 0.0
            return
        if clicked is ignore:
            self._accept_local_state(watched)
            return
        if clicked not in {upload, backup}:
            # Closing the dialog dismisses this exact revision. It remains
            # visible as pending, but the popup will not loop indefinitely.
            watched.prompting = False
            watched.changed_at = 0.0
            return
        self._upload(
            watched,
            digest,
            make_backup=clicked is backup,
            force=False,
        )

    def _upload(
        self,
        watched: WatchedFile,
        digest: str,
        make_backup: bool,
        force: bool,
        sudo_password: str | None = None,
    ) -> None:
        def task() -> tuple[str, tuple[int, int, int], str, str | None]:
            session = self._current_session(watched)
            current = session.remote_file_signature(watched.remote_path)
            if not force and current != watched.remote_signature:
                raise RemoteConflictError(
                    "The file was modified on the server after it was opened."
                )

            # Upload an immutable snapshot. Editors may rewrite or atomically
            # replace the local file while the asynchronous upload is running.
            handle, snapshot_name = tempfile.mkstemp(
                prefix="RemoteLinuxUpload_",
                dir=self.root,
            )
            os.close(handle)
            snapshot = Path(snapshot_name)
            try:
                shutil.copyfile(watched.local_path, snapshot)
                uploaded_digest = self._digest(snapshot)
                uploaded_text = self._read_text(snapshot)
                if make_backup:
                    backup_path = (
                        session.backup_remote_sudo(
                            watched.remote_path, sudo_password
                        )
                        if sudo_password
                        else session.backup_remote(watched.remote_path)
                    )
                else:
                    backup_path = ""
                if sudo_password:
                    session.upload_file_sudo(
                        str(snapshot), watched.remote_path, sudo_password
                    )
                else:
                    session.upload_file(str(snapshot), watched.remote_path)
                signature = session.remote_file_signature(watched.remote_path)
                return backup_path, signature, uploaded_digest, uploaded_text
            finally:
                try:
                    snapshot.unlink()
                except OSError:
                    pass

        def done(result: object) -> None:
            backup_path, signature, uploaded_digest, uploaded_text = result
            watched.remote_signature = tuple(signature)
            watched.original_text = uploaded_text

            current_digest = self._digest(watched.local_path)
            if current_digest == uploaded_digest:
                self._accept_local_state(watched)
            else:
                # The editor saved again during the upload. Keep that newer
                # revision pending instead of falsely marking it as uploaded.
                watched.digest = uploaded_digest
                watched.prompted_digest = ""
                watched.local_size = -1
                watched.local_mtime_ns = -1
                watched.changed_at = 0.0
                watched.prompting = False
                watched.alerted = True

            suffix = f"\n{tr('ui.backup')} {backup_path}" if backup_path else ""
            QMessageBox.information(
                self.parent_widget,
                tr('ui.file_uploaded'),
                f"{tr('ui.the_server_has_been_updated')}{suffix}",
            )

        def failed(text: str) -> None:
            if isinstance(text, str) and (
                "modified on the server" in text
                or "modifié sur le serveur" in text
            ):
                answer = QMessageBox.warning(
                    self.parent_widget,
                    tr('ui.remote_conflict'),
                    text + f"\n\n{tr('ui.overwrite_the_remote_version_anyway')}",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer == QMessageBox.Yes:
                    self._upload(
                        watched, digest, make_backup, force=True,
                        sudo_password=sudo_password,
                    )
                    return
                watched.prompting = False
                watched.changed_at = 0.0
                return
            if not sudo_password and is_permission_denied(text):
                answer = QMessageBox.question(
                    self.parent_widget,
                    "Droits insuffisants",
                    f"L’envoi SFTP n’a pas les droits pour modifier :\n"
                    f"{watched.remote_path}\n\nRéessayer avec sudo ?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if answer == QMessageBox.No:
                    watched.prompting = False
                    watched.changed_at = 0.0
                    return
                if self.sudo_password_provider:
                    password = self.sudo_password_provider()
                    if password is False or not str(password):
                        watched.prompting = False
                        watched.changed_at = 0.0
                        return
                    self._upload(
                        watched, digest, make_backup, force,
                        sudo_password=str(password),
                    )
                    return
            watched.prompting = False
            watched.changed_at = 0.0
            QMessageBox.critical(
                self.parent_widget,
                tr('ui.upload_failed'),
                text,
            )

        run_async(task, done, failed)

    def _current_session(self, watched: WatchedFile) -> RemoteSession:
        session = self.session_provider()
        if (
            not session
            or not session.is_alive()
            or session.profile.id != watched.profile_id
        ):
            raise RuntimeError(
                "Le serveur d’origine n’est plus connecté. Reconnectez ce profil avant l’envoi."
            )
        return session

    def _accept_local_state(self, watched: WatchedFile) -> None:
        # Read the digest from the revision that is actually on disk now. This
        # avoids keeping a stale digest when an editor performs a second write
        # while a dialog is open.
        watched.digest = self._digest(watched.local_path)
        watched.local_size, watched.local_mtime_ns = self._local_state(
            watched.local_path
        )
        watched.changed_at = 0.0
        watched.prompting = False
        watched.alerted = False
        watched.prompted_digest = ""
        self.file_resolved.emit(watched.remote_path)

    def pending_changes(self) -> list[str]:
        pending = []
        for watched in self.watched.values():
            if not watched.local_path.exists():
                continue
            size, mtime_ns = self._local_state(watched.local_path)
            metadata_changed = (
                size != watched.local_size
                or mtime_ns != watched.local_mtime_ns
            )
            if not metadata_changed:
                if watched.alerted or watched.prompting:
                    pending.append(watched.remote_path)
                continue

            # Some editors touch the temporary file after a successful upload
            # (lock release, timestamp normalisation, atomic-save cleanup). The
            # contents are already synchronized in that case, so metadata alone
            # must not keep a phantom pending change alive at application exit.
            if self._digest(watched.local_path) == watched.digest:
                self._accept_local_state(watched)
                continue
            pending.append(watched.remote_path)
        return pending

    @staticmethod
    def _local_state(path: Path) -> tuple[int, int]:
        try:
            info = path.stat()
            return int(info.st_size), int(info.st_mtime_ns)
        except OSError:
            return 0, 0

    @staticmethod
    def _digest(path: Path) -> str:
        try:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return ""

    @staticmethod
    def _read_text(path: Path) -> str | None:
        try:
            if path.stat().st_size > MAX_TEXT_PREVIEW:
                return None
            data = path.read_bytes()
            if b"\x00" in data:
                return None
            return data.decode("utf-8", errors="replace")
        except OSError:
            return None

    def close(self) -> None:
        self.closed = True
        self.timer.stop()
        self.opening.clear()
        self.watched.clear()
        self.watched_by_identity.clear()
        shutil.rmtree(self.root, ignore_errors=True)
