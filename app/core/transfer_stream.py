from __future__ import annotations

import os
import posixpath
import socket
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import paramiko

from app.core.chunked_command_upload import (
    ChunkCommandStalled,
    ChunkedCommandUploader,
)
from app.core.sftp_transfer_metadata import SftpTransferMetadata
from app.core.ssh_stream_channel import SshStreamChannel, StreamStalled

ProgressCallback = Callable[[int, int, str], None]
StatusCallback = Callable[[str], None]


class TransferCancelled(RuntimeError):
    pass


class TransferStalled(RuntimeError):
    pass


class SftpTransferEngine:
    IO_TIMEOUT = SshStreamChannel.STALL_TIMEOUT
    MAX_ATTEMPTS = 3

    def __init__(
        self,
        client: paramiko.SSHClient,
        progress: ProgressCallback | None = None,
        status: StatusCallback | None = None,
        cancel_event=None,
        robust_upload: bool = False,
    ) -> None:
        self.progress = progress
        self.status = status
        self.cancel_event = cancel_event
        self.robust_upload = robust_upload
        self.stream = SshStreamChannel(client, status, self._check_cancel)
        self.chunked = ChunkedCommandUploader(
            client, status, self._check_cancel
        )
        self.metadata = SftpTransferMetadata(client, self.IO_TIMEOUT)

    def upload(
        self,
        local_path: Path,
        remote_path: str,
        base: int,
        total: int,
    ) -> None:
        file_total = int(local_path.stat().st_size)
        remote_part = self.metadata.remote_part_path(remote_path)
        target_metadata = self.metadata.target_metadata(remote_path)
        error: Exception | None = None

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            self._check_cancel()
            try:
                offset = self.metadata.prepare_part(
                    remote_part,
                    file_total,
                    attempt,
                )
                self._emit(base + offset, total, str(local_path))
                uploader = (
                    self.chunked.upload
                    if self.robust_upload
                    else self.stream.upload
                )
                uploader(
                    local_path,
                    remote_part,
                    offset,
                    file_total,
                    lambda value: self._emit(
                        base + value,
                        total,
                        str(local_path),
                    ),
                )
                self.metadata.replace(
                    remote_part,
                    remote_path,
                    target_metadata,
                )
                self._emit(base + file_total, total, str(local_path))
                return
            except TransferCancelled:
                raise
            except Exception as exc:
                error = exc
                if not self._retry(attempt, local_path.name, exc):
                    try:
                        self.metadata.cleanup(remote_part)
                    except Exception:
                        pass
                    raise self._final_error(exc) from exc

        try:
            self.metadata.cleanup(remote_part)
        except Exception:
            pass
        raise self._final_error(
            error or RuntimeError("Erreur de transfert inconnue.")
        )

    def download(
        self,
        remote_path: str,
        local_path: Path,
        file_total: int,
        base: int,
        total: int,
        overwrite: bool,
    ) -> None:
        if local_path.exists() and not overwrite:
            raise FileExistsError(f"Le fichier existe déjà : {local_path}")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_part = local_path.with_name(
            f".{local_path.name}.remote-linux-part-{uuid.uuid4().hex[:12]}"
        )
        error: Exception | None = None

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            self._check_cancel()
            try:
                offset = self._local_offset(local_part, file_total, attempt)
                self._emit(base + offset, total, remote_path)
                self.stream.download(
                    remote_path,
                    local_part,
                    offset,
                    file_total,
                    lambda value: self._emit(
                        base + value,
                        total,
                        remote_path,
                    ),
                )
                os.replace(local_part, local_path)
                self._emit(base + file_total, total, remote_path)
                return
            except TransferCancelled:
                raise
            except Exception as exc:
                error = exc
                name = posixpath.basename(remote_path)
                if not self._retry(attempt, name, exc):
                    local_part.unlink(missing_ok=True)
                    raise self._final_error(exc) from exc

        local_part.unlink(missing_ok=True)
        raise self._final_error(
            error or RuntimeError("Erreur de transfert inconnue.")
        )

    @staticmethod
    def _local_offset(
        local_part: Path,
        file_total: int,
        attempt: int,
    ) -> int:
        if attempt == 1:
            local_part.unlink(missing_ok=True)
        offset = local_part.stat().st_size if local_part.exists() else 0
        if offset > file_total:
            local_part.unlink(missing_ok=True)
            return 0
        return offset

    def _retry(
        self,
        attempt: int,
        name: str,
        exc: Exception,
    ) -> bool:
        if not self._is_transient(exc) or attempt >= self.MAX_ATTEMPTS:
            return False
        if self.status:
            self.status(
                f"Transfert interrompu sur {name}. Nouvelle tentative "
                f"{attempt + 1}/{self.MAX_ATTEMPTS}… "
                f"({self._short_error(exc)})"
            )
        end = time.monotonic() + 1.5
        while time.monotonic() < end:
            self._check_cancel()
            time.sleep(0.1)
        return True

    def _emit(
        self,
        transferred: int,
        total: int,
        current: str,
    ) -> None:
        if self.progress:
            self.progress(
                max(0, int(transferred)),
                max(0, int(total)),
                current,
            )

    def _check_cancel(self) -> None:
        if self.cancel_event and self.cancel_event.is_set():
            raise TransferCancelled("Transfert annulé.")

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        if isinstance(
            exc,
            (
                StreamStalled,
                ChunkCommandStalled,
                socket.timeout,
                TimeoutError,
                EOFError,
                paramiko.SSHException,
            ),
        ):
            return True
        if isinstance(exc, OSError):
            text = str(exc).lower()
            return any(
                word in text
                for word in (
                    "socket",
                    "timed out",
                    "timeout",
                    "connection",
                    "channel closed",
                    "server connection dropped",
                    "end of file",
                    "aucune donnée",
                )
            )
        return False

    @classmethod
    def _final_error(cls, exc: Exception) -> RuntimeError:
        if cls._is_transient(exc):
            return TransferStalled(
                "Le transfert ne progresse plus ou la connexion SSH a été "
                "interrompue. Plusieurs tentatives ont échoué. Utilisez "
                "« Relancer » après vérification de la connexion."
            )
        return RuntimeError(str(exc))

    @staticmethod
    def _short_error(exc: Exception) -> str:
        text = str(exc).strip()
        return text if text else exc.__class__.__name__
