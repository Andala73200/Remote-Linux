import posixpath
import shlex
import stat
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

import paramiko

from app.core.transfer_stream import SftpTransferEngine, TransferCancelled

ProgressCallback = Callable[[int, int, str], None]
StatusCallback = Callable[[str], None]


class SessionFilesMixin:
    def _identity(self) -> tuple[int, set[int]]:
        if self._identity_cache:
            return self._identity_cache
        code, output = self.execute("id -u; id -G")
        if code != 0:
            self._identity_cache = (-1, set())
        else:
            lines = output.splitlines()
            uid = int(lines[0]) if lines else -1
            groups = {int(value) for value in lines[1].split()} if len(lines) > 1 else set()
            self._identity_cache = (uid, groups)
        return self._identity_cache

    def list_directory(self, path: str) -> list[dict[str, object]]:
        uid, groups = self._identity()
        with self._sftp_lock:
            sftp = self._open_timed_sftp()
            try:
                entries = []
                for item in sftp.listdir_attr(path):
                    mode = int(item.st_mode or 0)
                    owner = int(item.st_uid if item.st_uid is not None else -1)
                    group = int(item.st_gid if item.st_gid is not None else -1)
                    is_dir = stat.S_ISDIR(mode)
                    entries.append({
                        "name": item.filename,
                        "path": posixpath.join(path.rstrip("/") or "/", item.filename),
                        "is_dir": is_dir,
                        "is_link": stat.S_ISLNK(mode),
                        "size": int(item.st_size or 0),
                        "mtime": int(item.st_mtime or 0),
                        "mode": mode,
                        "mode_text": stat.filemode(mode),
                        "uid": owner,
                        "gid": group,
                        "rights": self._effective_rights(
                            mode, owner, group, uid, groups, is_dir
                        ),
                    })
                return sorted(
                    entries,
                    key=lambda row: (not bool(row["is_dir"]), str(row["name"]).lower()),
                )
            finally:
                sftp.close()

    @staticmethod
    def _effective_rights(
        mode: int, owner: int, group: int, uid: int, groups: set[int], is_dir: bool
    ) -> str:
        if uid == 0:
            return "RWX" if is_dir or mode & 0o111 else "RW"
        shift = 6 if uid == owner else 3 if group in groups else 0
        bits = (mode >> shift) & 0b111
        value = (
            ("R" if bits & 4 else "")
            + ("W" if bits & 2 else "")
            + ("X" if bits & 1 else "")
        )
        return value or "🔒"

    def remote_file_signature(self, path: str) -> tuple[int, int, int]:
        with self._sftp_lock:
            sftp = self._open_timed_sftp()
            try:
                attrs = sftp.lstat(path)
                return (
                    int(attrs.st_size or 0),
                    int(attrs.st_mtime or 0),
                    int(attrs.st_mode or 0),
                )
            finally:
                sftp.close()

    def download_file(
        self,
        remote_path: str,
        local_path: str,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        status: StatusCallback | None = None,
    ) -> None:
        with self._sftp_lock:
            sftp = self._open_timed_sftp()
            try:
                file_total = int(sftp.stat(remote_path).st_size or 0)
            finally:
                sftp.close()
            engine = SftpTransferEngine(self.client, progress, status, cancel_event)
            engine.download(
                remote_path, Path(local_path), file_total, 0, file_total, overwrite=True
            )

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        status: StatusCallback | None = None,
    ) -> None:
        local = Path(local_path)
        with self._sftp_lock:
            engine = SftpTransferEngine(
                self.client,
                progress,
                status,
                cancel_event,
                robust_upload=self.profile.kind == "cloudflare",
            )
            engine.upload(local, remote_path, 0, int(local.stat().st_size))

    def upload_file_sudo(
        self,
        local_path: str,
        remote_path: str,
        password: str,
    ) -> None:
        if not password:
            raise RuntimeError("Mot de passe sudo vide.")
        remote_dir = posixpath.join(
            self.remote_home(), f".remote-linux-{uuid.uuid4().hex}"
        )
        remote_temp = posixpath.join(remote_dir, "upload")
        with self._sftp_lock:
            sftp = self._open_timed_sftp()
            try:
                sftp.mkdir(remote_dir, mode=0o700)
            finally:
                sftp.close()
        try:
            self.upload_file(local_path, remote_temp)
            command = (
                f"cp -- {shlex.quote(remote_temp)} "
                f"{shlex.quote(remote_path)}"
            )
            code, output = self._sudo_command(command, password, timeout=90)
            if code != 0:
                raise RuntimeError(
                    output or "Écriture du fichier avec sudo impossible."
                )
        finally:
            try:
                self.execute(
                    f"rm -rf -- {shlex.quote(remote_dir)}",
                    timeout=15,
                )
            except Exception:
                pass

    def create_remote_text_file(self, path: str, content: str = "") -> None:
        with self._sftp_lock:
            sftp = self._open_timed_sftp()
            try:
                with sftp.open(path, "x") as stream:
                    stream.write(str(content).encode("utf-8"))
            finally:
                sftp.close()

    def upload_paths(
        self,
        local_paths: list[str],
        remote_dir: str,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        status: StatusCallback | None = None,
    ) -> int:
        total = sum(self._local_size(Path(path)) for path in local_paths)
        state = {"done": 0, "count": 0}
        with self._sftp_lock:
            sftp = self._open_timed_sftp()
            engine = SftpTransferEngine(
                self.client,
                progress,
                status,
                cancel_event,
                robust_upload=self.profile.kind == "cloudflare",
            )
            try:
                for local in local_paths:
                    self._upload_entry(
                        sftp,
                        engine,
                        Path(local),
                        remote_dir,
                        total,
                        state,
                        cancel_event,
                    )
                return int(state["count"])
            finally:
                sftp.close()

    def _upload_entry(
        self,
        sftp: paramiko.SFTPClient,
        engine: SftpTransferEngine,
        local: Path,
        remote_dir: str,
        total: int,
        state: dict[str, int],
        cancel_event: threading.Event | None,
    ) -> None:
        self._check_cancel(cancel_event)
        if local.is_symlink():
            raise RuntimeError(
                f"Lien symbolique local ignoré pour éviter de suivre une cible inattendue : {local}"
            )
        remote = posixpath.join(remote_dir.rstrip("/") or "/", local.name)
        if local.is_dir():
            try:
                sftp.mkdir(remote)
            except OSError:
                pass
            for child in local.iterdir():
                self._upload_entry(
                    sftp, engine, child, remote, total, state, cancel_event
                )
            return
        size = int(local.stat().st_size)
        engine.upload(local, remote, state["done"], total)
        state["done"] += size
        state["count"] += 1

    def download_paths(
        self,
        remote_paths: list[str],
        local_dir: str,
        overwrite: bool = True,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        status: StatusCallback | None = None,
    ) -> list[str]:
        target_root = Path(local_dir)
        target_root.mkdir(parents=True, exist_ok=True)
        with self._sftp_lock:
            sftp = self._open_timed_sftp()
            engine = SftpTransferEngine(self.client, progress, status, cancel_event)
            try:
                total = sum(
                    self._remote_size(sftp, path, cancel_event) for path in remote_paths
                )
                state = {"done": 0}
                results = []
                for remote in remote_paths:
                    local = target_root / posixpath.basename(remote.rstrip("/"))
                    self._download_entry(
                        sftp,
                        engine,
                        remote,
                        local,
                        overwrite,
                        total,
                        state,
                        cancel_event,
                    )
                    results.append(str(local))
                return results
            finally:
                sftp.close()

    def _download_entry(
        self,
        sftp: paramiko.SFTPClient,
        engine: SftpTransferEngine,
        remote: str,
        local: Path,
        overwrite: bool,
        total: int,
        state: dict[str, int],
        cancel_event: threading.Event | None,
    ) -> None:
        self._check_cancel(cancel_event)
        attrs = sftp.lstat(remote)
        if stat.S_ISLNK(attrs.st_mode):
            raise RuntimeError(
                f"Lien symbolique distant non téléchargé automatiquement : {remote}"
            )
        if stat.S_ISDIR(attrs.st_mode):
            local.mkdir(parents=True, exist_ok=True)
            for child in sftp.listdir_attr(remote):
                child_remote = posixpath.join(
                    remote.rstrip("/") or "/", child.filename
                )
                self._download_entry(
                    sftp,
                    engine,
                    child_remote,
                    local / child.filename,
                    overwrite,
                    total,
                    state,
                    cancel_event,
                )
            return
        file_total = int(attrs.st_size or 0)
        engine.download(
            remote, local, file_total, state["done"], total, overwrite
        )
        state["done"] += file_total

    def _remote_size(
        self,
        sftp: paramiko.SFTPClient,
        path: str,
        cancel_event: threading.Event | None,
    ) -> int:
        self._check_cancel(cancel_event)
        attrs = sftp.lstat(path)
        if not stat.S_ISDIR(attrs.st_mode):
            return int(attrs.st_size or 0)
        return sum(
            self._remote_size(
                sftp,
                posixpath.join(path.rstrip("/") or "/", child.filename),
                cancel_event,
            )
            for child in sftp.listdir_attr(path)
        )

    @staticmethod
    def _local_size(path: Path) -> int:
        if path.is_symlink():
            raise RuntimeError(
                f"Lien symbolique local ignoré : {path}"
            )
        if path.is_dir():
            return sum(SessionFilesMixin._local_size(child) for child in path.iterdir())
        return int(path.stat().st_size)

    @staticmethod
    def _check_cancel(cancel_event: threading.Event | None) -> None:
        if cancel_event and cancel_event.is_set():
            raise TransferCancelled("Transfert annulé.")

    def _open_timed_sftp(self) -> paramiko.SFTPClient:
        sftp = self.client.open_sftp()
        sftp.get_channel().settimeout(SftpTransferEngine.IO_TIMEOUT)
        return sftp
