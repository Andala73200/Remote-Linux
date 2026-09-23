from __future__ import annotations

import base64
import shlex
import socket
import time
from collections.abc import Callable
from pathlib import Path

import paramiko

ProgressCallback = Callable[[int], None]
StatusCallback = Callable[[str], None]
CancelCallback = Callable[[], None]


class ChunkCommandStalled(TimeoutError):
    pass


class ChunkedCommandUploader:
    """Envoi fiable par petits blocs idempotents via des commandes SSH courtes."""

    CHUNK_SIZE = 6 * 1024
    COMMAND_TIMEOUT = 12.0
    MAX_CHUNK_ATTEMPTS = 3

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
        if file_total == 0:
            self._run_with_retry(
                f": > {shlex.quote(remote_path)}",
                local_path.name,
                offset,
            )
            progress(0)
            return

        transferred = offset
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
                command = self._write_command(
                    remote_path,
                    transferred,
                    chunk,
                )
                self._run_with_retry(
                    command,
                    local_path.name,
                    transferred,
                )
                transferred += len(chunk)
                progress(transferred)

    @staticmethod
    def _write_command(
        remote_path: str,
        offset: int,
        chunk: bytes,
    ) -> str:
        encoded = base64.b64encode(chunk).decode("ascii")
        return (
            f"printf '%s' {shlex.quote(encoded)} | base64 -d | "
            f"dd of={shlex.quote(remote_path)} bs=64K seek={offset} "
            "oflag=seek_bytes conv=notrunc status=none"
        )

    def _run_with_retry(
        self,
        command: str,
        name: str,
        offset: int,
    ) -> None:
        error: Exception | None = None
        for attempt in range(1, self.MAX_CHUNK_ATTEMPTS + 1):
            self.check_cancel()
            try:
                self._run(command)
                return
            except Exception as exc:
                error = exc
                if not self._is_transient(exc) or attempt >= self.MAX_CHUNK_ATTEMPTS:
                    raise
                if self.status:
                    self.status(
                        f"Reprise sécurisée de {name} à partir de "
                        f"{offset} octets ({attempt + 1}/"
                        f"{self.MAX_CHUNK_ATTEMPTS})…"
                    )
                self._pause_before_retry()
        if error:
            raise error

    def _run(self, command: str) -> None:
        transport = self.client.get_transport()
        if not transport or not transport.is_active():
            raise paramiko.SSHException("La connexion SSH n’est plus active.")

        channel: paramiko.Channel | None = None
        stderr = bytearray()
        try:
            try:
                channel = transport.open_session(timeout=5.0)
            except TypeError:
                channel = transport.open_session()
            channel.settimeout(2.0)
            channel.exec_command(command)
            deadline = time.monotonic() + self.COMMAND_TIMEOUT

            while not channel.exit_status_ready():
                self.check_cancel()
                while channel.recv_stderr_ready() and len(stderr) < 16384:
                    stderr.extend(channel.recv_stderr(4096))
                if time.monotonic() >= deadline:
                    raise ChunkCommandStalled(
                        "Le serveur n’a pas confirmé l’écriture du bloc."
                    )
                time.sleep(0.02)

            code = channel.recv_exit_status()
            while channel.recv_stderr_ready() and len(stderr) < 16384:
                stderr.extend(channel.recv_stderr(4096))
            if code != 0:
                message = stderr.decode("utf-8", errors="replace").strip()
                raise OSError(
                    message or f"L’écriture distante a échoué ({code})."
                )
        finally:
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass

    def _pause_before_retry(self) -> None:
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            self.check_cancel()
            time.sleep(0.05)

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        return isinstance(
            exc,
            (
                ChunkCommandStalled,
                socket.timeout,
                TimeoutError,
                EOFError,
                paramiko.SSHException,
            ),
        )
