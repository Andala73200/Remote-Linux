from __future__ import annotations

import base64
import binascii
import hashlib
import posixpath
import shlex
import time


class SessionSSHKeysMixin:
    def ssh_authorized_keys(self) -> list[dict[str, object]]:
        rows = []
        for line_number, line in enumerate(self._read_authorized_key_lines(), start=1):
            parsed = self._parse_public_key(line)
            if parsed:
                parsed["line"] = line_number
                parsed["raw"] = line
                rows.append(parsed)
        return rows

    def add_ssh_authorized_key(self, public_key: str) -> str:
        clean = public_key.strip()
        parsed = self._parse_public_key(clean, require_plain=True)
        if not parsed or "\n" in clean or "\r" in clean:
            raise ValueError("La clé publique SSH est invalide ou contient plusieurs lignes.")
        lines = self._read_authorized_key_lines()
        for line in lines:
            existing = self._parse_public_key(line)
            if existing and existing["fingerprint"] == parsed["fingerprint"]:
                raise ValueError("Cette clé est déjà autorisée sur le serveur.")
        lines.append(clean)
        self._write_authorized_key_lines(lines)
        return str(parsed["fingerprint"])

    def remove_ssh_authorized_key(self, fingerprint: str) -> None:
        lines = self._read_authorized_key_lines()
        kept, removed = [], False
        for line in lines:
            parsed = self._parse_public_key(line)
            if parsed and parsed["fingerprint"] == fingerprint:
                removed = True
            else:
                kept.append(line)
        if not removed:
            raise ValueError("La clé sélectionnée n’existe plus dans authorized_keys.")
        self._write_authorized_key_lines(kept)

    def _read_authorized_key_lines(self) -> list[str]:
        home = self.remote_home()
        path = posixpath.join(home, ".ssh", "authorized_keys")
        with self._sftp_lock:
            sftp = self.client.open_sftp()
            try:
                try:
                    with sftp.open(path, "r") as stream:
                        raw = stream.read()
                except FileNotFoundError:
                    return []
            finally:
                sftp.close()
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        return text.splitlines()

    def _write_authorized_key_lines(self, lines: list[str]) -> None:
        home = self.remote_home()
        directory = posixpath.join(home, ".ssh")
        path = posixpath.join(directory, "authorized_keys")
        temporary = f"{path}.remote-linux-{int(time.time() * 1000)}"
        content = "\n".join(lines).rstrip("\n")
        if content:
            content += "\n"
        with self._sftp_lock:
            sftp = self.client.open_sftp()
            try:
                self._ensure_ssh_directory(sftp, directory)
                with sftp.open(temporary, "w") as stream:
                    stream.write(content.encode("utf-8"))
                sftp.chmod(temporary, 0o600)
                self._replace_authorized_keys(sftp, temporary, path)
            finally:
                try:
                    sftp.remove(temporary)
                except FileNotFoundError:
                    pass
                sftp.close()

    @staticmethod
    def _ensure_ssh_directory(sftp, directory: str) -> None:
        try:
            sftp.stat(directory)
        except FileNotFoundError:
            sftp.mkdir(directory, mode=0o700)
        sftp.chmod(directory, 0o700)

    @staticmethod
    def _replace_authorized_keys(sftp, temporary: str, path: str) -> None:
        try:
            sftp.posix_rename(temporary, path)
            return
        except (AttributeError, OSError):
            pass
        backup = path + ".remote-linux.bak"
        try:
            sftp.remove(backup)
        except FileNotFoundError:
            pass
        try:
            sftp.rename(path, backup)
        except FileNotFoundError:
            backup = ""
        try:
            sftp.rename(temporary, path)
        except Exception:
            if backup:
                sftp.rename(backup, path)
            raise

    @staticmethod
    def _parse_public_key(
        line: str, require_plain: bool = False
    ) -> dict[str, str] | None:
        try:
            parts = shlex.split(line, comments=False)
        except ValueError:
            return None
        key_types = {
            "ssh-ed25519", "ssh-rsa", "ssh-dss", "sk-ssh-ed25519@openssh.com",
            "sk-ecdsa-sha2-nistp256@openssh.com",
        }
        index = next((
            i for i, value in enumerate(parts)
            if value in key_types or value.startswith("ecdsa-")
        ), -1)
        if index < 0 or index + 1 >= len(parts) or (require_plain and index != 0):
            return None
        if require_plain and parts[index] == "ssh-dss":
            return None
        try:
            decoded = base64.b64decode(parts[index + 1], validate=True)
        except (ValueError, binascii.Error):
            return None
        if len(decoded) < 32:
            return None
        algorithm_size = int.from_bytes(decoded[:4], "big")
        algorithm = decoded[4:4 + algorithm_size].decode("ascii", errors="ignore")
        if algorithm != parts[index]:
            return None
        digest = base64.b64encode(hashlib.sha256(decoded).digest()).decode().rstrip("=")
        return {
            "type": parts[index], "fingerprint": f"SHA256:{digest}",
            "comment": " ".join(parts[index + 2:]),
        }
