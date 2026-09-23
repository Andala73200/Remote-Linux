from __future__ import annotations

import os
import posixpath
import shlex
import time
from collections.abc import Callable
from pathlib import Path

import paramiko

ProgressCallback = Callable[[int], None]
StatusCallback = Callable[[str], None]
CancelCallback = Callable[[], None]


class StreamStalled(RuntimeError):
    pass


class SshStreamChannel:
    CHUNK_SIZE = 64 * 1024
    STALL_TIMEOUT = 12.0
    WARNING_AFTER = 4.0

    def __init__(
        self,
        client: paramiko.SSHClient,
        status: StatusCallback | None,
        check_cancel: CancelCallback,
    ) -> None:
        self.client = client
        self.status = status
        self.check_cancel = check_cancel

    def upload(
        self,
        local_path: Path,
        remote_path: str,
        offset: int,
        file_total: int,
        progress: ProgressCallback,
    ) -> None:
        operator = ">>" if offset else ">"
        channel = self._open(f"cat {operator} {shlex.quote(remote_path)}")
        transferred = offset
        last_activity = time.monotonic()
        warned = False
        try:
            with local_path.open("rb") as local_file:
                local_file.seek(offset)
                while transferred < file_total:
                    self.check_cancel()
                    chunk = local_file.read(
                        min(self.CHUNK_SIZE, file_total - transferred)
                    )
                    if not chunk:
                        raise EOFError(
                            "Lecture locale interrompue avant la fin du fichier."
                        )
                    view = memoryview(chunk)
                    while view:
                        self.check_cancel()
                        if channel.send_ready():
                            sent = channel.send(view)
                            if sent <= 0:
                                raise EOFError(
                                    "Le canal SSH a été fermé pendant l’envoi."
                                )
                            view = view[sent:]
                            transferred += sent
                            last_activity = time.monotonic()
                            warned = False
                            progress(transferred)
                        else:
                            warned = self._check_stall(
                                channel,
                                last_activity,
                                warned,
                                local_path.name,
                            )
                            time.sleep(0.02)
            channel.shutdown_write()
            self._wait_exit(channel, last_activity, local_path.name)
        finally:
            self._close(channel)

    def download(
        self,
        remote_path: str,
        local_part: Path,
        offset: int,
        file_total: int,
        progress: ProgressCallback,
    ) -> None:
        quoted = shlex.quote(remote_path)
        if offset:
            command = f"tail -c +{offset + 1} -- {quoted}"
        else:
            command = f"cat -- {quoted}"
        channel = self._open(command)
        transferred = offset
        last_activity = time.monotonic()
        warned = False
        mode = "ab" if offset else "wb"
        try:
            with local_part.open(mode) as local_file:
                while transferred < file_total:
                    self.check_cancel()
                    if channel.recv_ready():
                        data = channel.recv(
                            min(self.CHUNK_SIZE, file_total - transferred)
                        )
                        if not data:
                            raise EOFError(
                                "Le serveur a interrompu le fichier avant sa fin."
                            )
                        local_file.write(data)
                        transferred += len(data)
                        last_activity = time.monotonic()
                        warned = False
                        progress(transferred)
                    elif channel.exit_status_ready():
                        break
                    else:
                        warned = self._check_stall(
                            channel,
                            last_activity,
                            warned,
                            posixpath.basename(remote_path),
                        )
                        time.sleep(0.02)
                local_file.flush()
                os.fsync(local_file.fileno())
            if transferred != file_total:
                raise EOFError(
                    "Le serveur a interrompu le fichier avant sa fin."
                )
            self._wait_exit(
                channel,
                last_activity,
                posixpath.basename(remote_path),
            )
        finally:
            self._close(channel)

    def _open(self, command: str) -> paramiko.Channel:
        transport = self.client.get_transport()
        if not transport or not transport.is_active():
            raise paramiko.SSHException("La connexion SSH n’est plus active.")
        try:
            channel = transport.open_session(timeout=5.0)
        except TypeError:
            channel = transport.open_session()
        channel.settimeout(1.0)
        channel.exec_command(command)
        return channel

    def _wait_exit(
        self,
        channel: paramiko.Channel,
        last_activity: float,
        name: str,
    ) -> None:
        warned = False
        stderr = bytearray()
        while not channel.exit_status_ready():
            self.check_cancel()
            if channel.recv_stderr_ready():
                stderr.extend(channel.recv_stderr(4096))
                last_activity = time.monotonic()
            warned = self._check_stall(
                channel,
                last_activity,
                warned,
                name,
            )
            time.sleep(0.02)
        code = channel.recv_exit_status()
        while channel.recv_stderr_ready() and len(stderr) < 16384:
            stderr.extend(channel.recv_stderr(4096))
        if code != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise OSError(
                message or f"La commande distante a échoué ({code})."
            )

    def _check_stall(
        self,
        channel: paramiko.Channel,
        last_activity: float,
        warned: bool,
        name: str,
    ) -> bool:
        elapsed = time.monotonic() - last_activity
        if elapsed >= self.STALL_TIMEOUT:
            self._close(channel)
            raise StreamStalled(
                f"Aucune donnée transférée depuis {self.STALL_TIMEOUT:.0f} secondes."
            )
        if elapsed >= self.WARNING_AFTER and not warned:
            if self.status:
                self.status(
                    f"Le transfert de {name} ne répond plus, "
                    "vérification en cours…"
                )
            return True
        return warned

    @staticmethod
    def _close(channel: paramiko.Channel | None) -> None:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
