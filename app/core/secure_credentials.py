from __future__ import annotations

import os
from typing import Final

from app.models import ConnectionProfile

try:
    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError
except ImportError:  # L'application reste utilisable sans le module optionnel.
    keyring = None

    class KeyringError(Exception):
        pass

    class PasswordDeleteError(KeyringError):
        pass


class _WindowsCredentialVault:
    """Direct access to Windows Credential Manager.

    This native implementation is used as a fallback when ``keyring`` has not
    loaded its Windows backend. Secrets are stored as generic credentials and
    are never written to the JSON configuration file.
    """

    CRED_TYPE_GENERIC: Final[int] = 1
    CRED_PERSIST_LOCAL_MACHINE: Final[int] = 2
    ERROR_NOT_FOUND: Final[int] = 1168

    def __init__(self) -> None:
        self.available = False
        self._advapi32 = None
        self._credential_type = None
        self._credential_pointer = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            byte_pointer = ctypes.POINTER(ctypes.c_ubyte)

            class CredentialW(ctypes.Structure):
                _fields_ = [
                    ("Flags", wintypes.DWORD),
                    ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", byte_pointer),
                    ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR),
                ]

            credential_pointer = ctypes.POINTER(CredentialW)
            advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
            advapi32.CredWriteW.argtypes = [ctypes.POINTER(CredentialW), wintypes.BOOL]
            advapi32.CredWriteW.restype = wintypes.BOOL
            advapi32.CredReadW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(credential_pointer),
            ]
            advapi32.CredReadW.restype = wintypes.BOOL
            advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            advapi32.CredDeleteW.restype = wintypes.BOOL
            advapi32.CredFree.argtypes = [ctypes.c_void_p]
            advapi32.CredFree.restype = None

            self._ctypes = ctypes
            self._wintypes = wintypes
            self._advapi32 = advapi32
            self._credential_type = CredentialW
            self._credential_pointer = credential_pointer
            self.available = True
        except (AttributeError, OSError, ImportError):
            self.available = False

    def set(self, target: str, username: str, secret: str) -> None:
        if not self.available or not self._advapi32 or not self._credential_type:
            raise RuntimeError("Le coffre Windows n'est pas disponible.")
        blob = secret.encode("utf-16-le")
        if len(blob) > 2560:
            raise ValueError("Le secret est trop long pour le coffre Windows.")
        buffer = self._ctypes.create_string_buffer(blob)
        credential = self._credential_type()
        credential.Flags = 0
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.Comment = "Secret enregistré par Remote Linux"
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = self._ctypes.cast(
            buffer, self._ctypes.POINTER(self._ctypes.c_ubyte)
        )
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.AttributeCount = 0
        credential.Attributes = None
        credential.TargetAlias = None
        credential.UserName = username or "Remote Linux"
        if not self._advapi32.CredWriteW(self._ctypes.byref(credential), False):
            error = self._ctypes.get_last_error()
            raise RuntimeError(f"Échec du coffre Windows (erreur {error}).")

    def get(self, target: str) -> str | None:
        if not self.available or not self._advapi32 or not self._credential_pointer:
            return None
        pointer = self._credential_pointer()
        ok = self._advapi32.CredReadW(
            target,
            self.CRED_TYPE_GENERIC,
            0,
            self._ctypes.byref(pointer),
        )
        if not ok:
            error = self._ctypes.get_last_error()
            if error == self.ERROR_NOT_FOUND:
                return None
            raise RuntimeError(f"Lecture du coffre Windows impossible (erreur {error}).")
        try:
            credential = pointer.contents
            if not credential.CredentialBlob or not credential.CredentialBlobSize:
                return None
            raw = self._ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
            return raw.decode("utf-16-le")
        finally:
            self._advapi32.CredFree(pointer)

    def delete(self, target: str) -> bool:
        if not self.available or not self._advapi32:
            return False
        ok = self._advapi32.CredDeleteW(target, self.CRED_TYPE_GENERIC, 0)
        if ok:
            return True
        error = self._ctypes.get_last_error()
        if error == self.ERROR_NOT_FOUND:
            return False
        raise RuntimeError(f"Suppression dans le coffre Windows impossible (erreur {error}).")


class SecureCredentialStore:
    """Store secrets in the operating system credential vault."""

    SERVICE_NAME = "Remote Linux"
    TARGET_PREFIX = "Remote Linux/"

    def __init__(self) -> None:
        self._windows_vault = _WindowsCredentialVault()

    @staticmethod
    def _sudo_account(profile: ConnectionProfile) -> str:
        return f"sudo:{profile.id}:{profile.user}"

    @staticmethod
    def _cloudflare_account(profile: ConnectionProfile) -> str:
        return f"cloudflare:{profile.id}"

    @staticmethod
    def _cloudflare_user_account(profile: ConnectionProfile) -> str:
        return f"cloudflare-user:{profile.id}"

    def is_available(self) -> bool:
        if self._windows_vault.available:
            return True
        if keyring is None:
            return False
        try:
            backend = keyring.get_keyring()
            priority = getattr(backend, "priority", 0)
            return bool(priority and float(priority) > 0)
        except (KeyringError, RuntimeError, TypeError, ValueError):
            return False

    def backend_name(self) -> str:
        if self._windows_vault.available:
            return "Gestionnaire d'identifiants Windows"
        if keyring is None:
            return "Aucun coffre disponible"
        try:
            backend = keyring.get_keyring()
            return backend.__class__.__name__
        except Exception:
            return "Aucun coffre disponible"

    def _target(self, account: str) -> str:
        return self.TARGET_PREFIX + account

    def _get_secret(self, account: str) -> str | None:
        if not self.is_available():
            return None
        if self._windows_vault.available:
            try:
                value = self._windows_vault.get(self._target(account))
                if value:
                    return value
            except RuntimeError:
                pass
        if keyring is None:
            return None
        try:
            value = keyring.get_password(self.SERVICE_NAME, account)
            return value or None
        except (KeyringError, RuntimeError):
            return None

    def _set_secret(self, account: str, username: str, secret: str) -> None:
        if not secret:
            raise ValueError("Le secret est vide.")
        if not self.is_available():
            raise RuntimeError("Le coffre d'identifiants sécurisé n'est pas disponible sur ce PC.")
        if self._windows_vault.available:
            self._windows_vault.set(self._target(account), username, secret)
            return
        try:
            keyring.set_password(self.SERVICE_NAME, account, secret)
        except (KeyringError, RuntimeError) as exc:
            raise RuntimeError(f"Impossible d'enregistrer le secret : {exc}") from exc

    def _delete_secret(self, account: str) -> bool:
        if not self.is_available():
            return False
        removed = False
        if self._windows_vault.available:
            try:
                removed = self._windows_vault.delete(self._target(account)) or removed
            except RuntimeError:
                pass
        if keyring is None:
            return removed
        try:
            keyring.delete_password(self.SERVICE_NAME, account)
            return True
        except PasswordDeleteError:
            return removed
        except (KeyringError, RuntimeError):
            return removed

    def get_sudo_password(self, profile: ConnectionProfile | None) -> str | None:
        if profile is None:
            return None
        return self._get_secret(self._sudo_account(profile))

    def set_sudo_password(self, profile: ConnectionProfile, password: str) -> None:
        self._set_secret(self._sudo_account(profile), profile.user, password)

    def delete_sudo_password(self, profile: ConnectionProfile | None) -> bool:
        if profile is None:
            return False
        return self._delete_secret(self._sudo_account(profile))

    def get_cloudflare_secret(self, profile: ConnectionProfile | None) -> str | None:
        if profile is None:
            return None
        return self._get_secret(self._cloudflare_account(profile))

    def set_cloudflare_secret(self, profile: ConnectionProfile, secret: str) -> None:
        username = profile.cloudflare_client_id or profile.name
        self._set_secret(self._cloudflare_account(profile), username, secret)

    def delete_cloudflare_secret(self, profile: ConnectionProfile | None) -> bool:
        if profile is None:
            return False
        return self._delete_secret(self._cloudflare_account(profile))

    def get_cloudflare_user_token(self, profile: ConnectionProfile | None) -> str | None:
        if profile is None:
            return None
        return self._get_secret(self._cloudflare_user_account(profile))

    def set_cloudflare_user_token(self, profile: ConnectionProfile, token: str) -> None:
        self._set_secret(self._cloudflare_user_account(profile), profile.user, token)

    def delete_cloudflare_user_token(self, profile: ConnectionProfile | None) -> bool:
        if profile is None:
            return False
        return self._delete_secret(self._cloudflare_user_account(profile))
