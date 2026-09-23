import posixpath
import shlex
import stat
import threading
import time
from concurrent.futures import CancelledError

import paramiko

from app.core.bounded_output import read_channel_output
from app.core.session_admin import SessionAdminMixin
from app.core.session_database import SessionDatabaseMixin
from app.core.session_files import SessionFilesMixin
from app.core.session_health import SessionHealthMixin
from app.core.session_packages import SessionPackageMixin
from app.core.session_security import SessionSecurityMixin
from app.core.session_ssh_keys import SessionSSHKeysMixin
from app.core.session_tasks import SessionTasksMixin
from app.core.smart_probe import SmartProbeMixin
from app.core.storage_ops import StorageOperationsMixin
from app.core.system_probe import INVENTORY_COMMAND, SNAPSHOT_COMMAND, parse_inventory, parse_snapshot
from app.core.tunnel import CloudflareTunnel
from app.i18n import tr
from app.models import ConnectionProfile


class RemoteSession(
    SessionAdminMixin,
    SessionPackageMixin,
    SessionDatabaseMixin,
    SessionSecurityMixin,
    SessionSSHKeysMixin,
    SessionTasksMixin,
    SmartProbeMixin,
    SessionHealthMixin,
    SessionFilesMixin,
    StorageOperationsMixin,
):
    def __init__(
        self,
        profile: ConnectionProfile,
        client: paramiko.SSHClient,
        tunnel: CloudflareTunnel | None = None,
    ):
        self.profile = profile
        self.client = client
        self.tunnel = tunnel
        transport = self.client.get_transport()
        if transport:
            transport.set_keepalive(15)
        self.sudo_password: str | None = None
        self._closing = threading.Event()
        self._exec_lock = threading.Lock()
        self._sftp_lock = threading.Lock()
        self._shells: set[paramiko.Channel] = set()
        self._home: str | None = None
        self._identity_cache: tuple[int, set[int]] | None = None

    def open_shell(self, width: int = 140, height: int = 40) -> paramiko.Channel:
        self._ensure_open()
        channel = self.client.invoke_shell(term="xterm-256color", width=width, height=height)
        self._shells.add(channel)
        return channel

    def close_shell(self, channel: paramiko.Channel | None) -> None:
        if channel:
            try:
                channel.close()
            except Exception:
                pass
            self._shells.discard(channel)

    def is_alive(self) -> bool:
        if self._closing.is_set():
            return False
        transport = self.client.get_transport()
        if not transport or not transport.is_active():
            return False
        if self.tunnel and not self.tunnel.is_alive():
            return False
        return True

    def execute(self, command: str, timeout: float = 40.0) -> tuple[int, str]:
        self._ensure_open()
        with self._exec_lock:
            self._ensure_open()
            stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            del stdin, stderr
            channel = stdout.channel
            try:
                code, capture = read_channel_output(
                    channel,
                    timeout,
                    cancel_event=self._closing,
                )
                marker = tr('ui.remote_output_truncated').format(
                    omitted=capture.omitted, total=capture.total
                )
                return code, capture.render_text(marker).strip()
            finally:
                try:
                    channel.close()
                except Exception:
                    pass

    def open_stream(self, command: str) -> paramiko.Channel:
        self._ensure_open()
        transport = self.client.get_transport()
        if not transport or not transport.is_active():
            raise RuntimeError("La connexion SSH n’est plus active.")
        channel = transport.open_session(timeout=10.0)
        channel.settimeout(10.0)
        try:
            channel.exec_command(command)
            return channel
        except Exception:
            channel.close()
            raise

    def remote_home(self) -> str:
        self._ensure_open()
        if self._home:
            return self._home
        with self._sftp_lock:
            sftp = self.client.open_sftp()
            try:
                self._home = sftp.normalize(".")
            finally:
                sftp.close()
        return self._home

    def make_directory(self, path: str) -> None:
        with self._sftp_lock:
            sftp = self.client.open_sftp()
            try:
                sftp.mkdir(path)
            finally:
                sftp.close()

    def rename_remote(self, old_path: str, new_path: str) -> None:
        with self._sftp_lock:
            sftp = self.client.open_sftp()
            try:
                sftp.rename(old_path, new_path)
            finally:
                sftp.close()

    def remote_directory_has_entries(self, path: str) -> bool:
        with self._sftp_lock:
            sftp = self.client.open_sftp()
            try:
                entries = sftp.listdir_iter(path, read_aheads=1)
                return next(entries, None) is not None
            finally:
                sftp.close()

    def remove_remote(self, path: str, is_dir: bool) -> None:
        with self._sftp_lock:
            sftp = self.client.open_sftp()
            try:
                if is_dir:
                    self._remove_remote_directory(sftp, path)
                else:
                    sftp.remove(path)
            finally:
                sftp.close()

    def _remove_remote_directory(self, sftp, path: str) -> None:
        for entry in sftp.listdir_attr(path):
            child = posixpath.join(path.rstrip('/') or '/', entry.filename)
            mode = int(entry.st_mode or 0)
            if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
                self._remove_remote_directory(sftp, child)
            else:
                sftp.remove(child)
        sftp.rmdir(path)

    def backup_remote(self, path: str) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"{path}.bak-{stamp}"
        code, output = self.execute(f"cp -- {shlex.quote(path)} {shlex.quote(backup)}")
        if code != 0:
            raise RuntimeError(output or "Création de la sauvegarde distante impossible.")
        return backup

    def backup_remote_sudo(self, path: str, password: str) -> str:
        if not password:
            raise RuntimeError("Mot de passe sudo vide.")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"{path}.bak-{stamp}"
        command = f"cp -- {shlex.quote(path)} {shlex.quote(backup)}"
        code, output = self._sudo_command(command, password, timeout=90)
        if code != 0:
            raise RuntimeError(
                output or "Création de la sauvegarde distante avec sudo impossible."
            )
        return backup

    def system_snapshot(self) -> dict[str, object]:
        code, output = self.execute(SNAPSHOT_COMMAND, timeout=20)
        if not output:
            raise RuntimeError("Lecture des informations système impossible.")
        snapshot = parse_snapshot(output)
        if not snapshot.get("cpus") or "mem" not in snapshot:
            raise RuntimeError("Informations système reçues mais incomplètes.")
        # Some SSH servers close a completed channel without sending an exit status.
        # A usable snapshot is authoritative in that case, including status -1.
        return snapshot

    def system_inventory(self) -> dict[str, object]:
        code, output = self.execute(INVENTORY_COMMAND, timeout=25)
        if code != 0 and not output:
            raise RuntimeError("Inventaire système indisponible.")
        return parse_inventory(output)

    def close(self) -> None:
        if self._closing.is_set():
            return
        self._closing.set()
        for channel in list(self._shells):
            self.close_shell(channel)
        try:
            # Never destroy Paramiko's Transport while execute()/SFTP is still
            # using one of its channels. execute() sees _closing and cancels its
            # output drain before releasing _exec_lock.
            with self._exec_lock:
                with self._sftp_lock:
                    self.client.close()
        finally:
            if self.tunnel:
                self.tunnel.stop()

    def _ensure_open(self) -> None:
        if self._closing.is_set():
            raise CancelledError()
