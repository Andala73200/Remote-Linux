from __future__ import annotations

from dataclasses import dataclass
import posixpath
import uuid

import paramiko


@dataclass(frozen=True)
class RemoteMetadata:
    exists: bool
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    atime: int | None = None
    mtime: int | None = None


class SftpTransferMetadata:
    def __init__(self, client: paramiko.SSHClient, timeout: float) -> None:
        self.client = client
        self.timeout = timeout

    def remote_part_path(self, remote_path: str) -> str:
        directory = posixpath.dirname(remote_path) or "/"
        name = posixpath.basename(remote_path)
        token = uuid.uuid4().hex[:12]
        return posixpath.join(
            directory,
            f".{name}.remote-linux-part-{token}",
        )

    def target_metadata(self, target: str) -> RemoteMetadata:
        sftp = self._open()
        try:
            try:
                attrs = sftp.stat(target)
            except OSError as exc:
                if self._is_missing(exc):
                    return RemoteMetadata(False)
                raise
            return RemoteMetadata(
                True,
                int(attrs.st_mode) if attrs.st_mode is not None else None,
                int(attrs.st_uid) if attrs.st_uid is not None else None,
                int(attrs.st_gid) if attrs.st_gid is not None else None,
                int(attrs.st_atime) if attrs.st_atime is not None else None,
                int(attrs.st_mtime) if attrs.st_mtime is not None else None,
            )
        finally:
            self._close(sftp)

    def prepare_part(
        self,
        remote_part: str,
        file_total: int,
        attempt: int,
    ) -> int:
        sftp = self._open()
        try:
            if attempt == 1:
                self._remove_if_exists(sftp, remote_part)
            offset = self._size_or_zero(sftp, remote_part)
            if offset > file_total:
                self._remove_if_exists(sftp, remote_part)
                return 0
            return offset
        finally:
            self._close(sftp)

    def replace(
        self,
        source: str,
        target: str,
        metadata: RemoteMetadata,
    ) -> None:
        sftp = self._open()
        try:
            try:
                sftp.posix_rename(source, target)
            except (AttributeError, OSError) as exc:
                if isinstance(exc, OSError):
                    text = str(exc).lower()
                    if "unsupported" not in text and "not supported" not in text:
                        raise
                self._safe_replace_fallback(sftp, source, target, metadata.exists)
            self._restore_metadata(sftp, target, metadata)
        finally:
            self._close(sftp)

    def cleanup(self, path: str) -> None:
        sftp = self._open()
        try:
            self._remove_if_exists(sftp, path)
        finally:
            self._close(sftp)

    def _safe_replace_fallback(
        self,
        sftp: paramiko.SFTPClient,
        source: str,
        target: str,
        target_exists: bool,
    ) -> None:
        backup = f"{target}.remote-linux-backup-{uuid.uuid4().hex[:12]}"
        moved_original = False
        try:
            if target_exists:
                sftp.rename(target, backup)
                moved_original = True
            sftp.rename(source, target)
        except Exception:
            if moved_original:
                try:
                    self._remove_if_exists(sftp, target)
                    sftp.rename(backup, target)
                except Exception:
                    pass
            raise
        else:
            if moved_original:
                self._remove_if_exists(sftp, backup)

    @staticmethod
    def _restore_metadata(
        sftp: paramiko.SFTPClient,
        target: str,
        metadata: RemoteMetadata,
    ) -> None:
        if not metadata.exists:
            return
        if metadata.mode is not None:
            try:
                sftp.chmod(target, metadata.mode & 0o7777)
            except OSError:
                pass
        if metadata.uid is not None and metadata.gid is not None:
            try:
                sftp.chown(target, metadata.uid, metadata.gid)
            except OSError:
                pass
        if metadata.atime is not None and metadata.mtime is not None:
            try:
                sftp.utime(target, (metadata.atime, metadata.mtime))
            except OSError:
                pass

    def _open(self) -> paramiko.SFTPClient:
        transport = self.client.get_transport()
        if not transport or not transport.is_active():
            raise paramiko.SSHException("La connexion SSH n’est plus active.")
        sftp = self.client.open_sftp()
        sftp.get_channel().settimeout(self.timeout)
        return sftp

    @classmethod
    def _size_or_zero(
        cls,
        sftp: paramiko.SFTPClient,
        path: str,
    ) -> int:
        try:
            return int(sftp.stat(path).st_size or 0)
        except OSError as exc:
            if cls._is_missing(exc):
                return 0
            raise

    @classmethod
    def _remove_if_exists(
        cls,
        sftp: paramiko.SFTPClient,
        path: str,
    ) -> None:
        try:
            sftp.remove(path)
        except OSError as exc:
            if not cls._is_missing(exc):
                raise

    @staticmethod
    def _is_missing(exc: OSError) -> bool:
        return (
            getattr(exc, "errno", None) == 2
            or "no such file" in str(exc).lower()
        )

    @staticmethod
    def _close(sftp: paramiko.SFTPClient | None) -> None:
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                pass
