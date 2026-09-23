import base64
import hashlib
import threading

import paramiko
from PySide6.QtCore import QThread, Signal

from app.config import KNOWN_HOSTS_FILE
from app.core.session import RemoteSession
from app.core.tunnel import CloudflareTunnel
from app.models import ConnectionProfile


class InteractiveHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, worker: "ConnectionWorker"):
        self.worker = worker

    def missing_host_key(self, client, hostname, key) -> None:
        accepted = self.worker.request_host_key(hostname, key)
        if not accepted:
            raise paramiko.SSHException("Clé d'hôte refusée par l'utilisateur.")
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(KNOWN_HOSTS_FILE))


class ConnectionWorker(QThread):
    connected = Signal(object)
    failed = Signal(str)
    status = Signal(str)
    log = Signal(str)
    host_key_required = Signal(str, str, str)
    cloudflare_user_token_acquired = Signal(str)

    def __init__(
        self,
        profile: ConnectionProfile,
        secret: str | None,
        cloudflare_secret: str | None = None,
        cloudflare_user_token: str | None = None,
    ):
        super().__init__()
        self.profile = profile
        self.secret = secret or None
        self.cloudflare_secret = cloudflare_secret or None
        self.cloudflare_user_token = cloudflare_user_token or None
        self._decision_event = threading.Event()
        self._host_key_decision = False
        self._resource_lock = threading.Lock()
        self._client = None
        self._tunnel = None
        self._connection_socket = None

    def cancel(self) -> None:
        self.requestInterruption()
        self.submit_host_key_decision(False)
        with self._resource_lock:
            client = self._client
            tunnel = self._tunnel
            connection_socket = self._connection_socket
        if client:
            try:
                client.close()
            except Exception:
                pass
        if connection_socket:
            try:
                connection_socket.close()
            except Exception:
                pass
        if tunnel:
            try:
                tunnel.stop()
            except Exception:
                pass

    def request_host_key(self, hostname: str, key: paramiko.PKey) -> bool:
        digest = hashlib.sha256(key.asbytes()).digest()
        fingerprint = "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
        self._decision_event.clear()
        self.host_key_required.emit(hostname, key.get_name(), fingerprint)
        while not self._decision_event.wait(0.2):
            if self.isInterruptionRequested():
                return False
        return self._host_key_decision

    def submit_host_key_decision(self, accepted: bool) -> None:
        self._host_key_decision = accepted
        self._decision_event.set()

    def run(self) -> None:
        tunnel = None
        client = None
        connection_socket = None
        try:
            target_host = self.profile.host
            target_port = self.profile.port
            if self.profile.kind == "cloudflare":
                self.status.emit("Ouverture du tunnel Cloudflare…")
                tunnel = CloudflareTunnel(
                    self.profile,
                    self.log.emit,
                    self.cloudflare_secret,
                    self.cloudflare_user_token,
                    self.cloudflare_user_token_acquired.emit,
                    self.isInterruptionRequested,
                )
                with self._resource_lock:
                    self._tunnel = tunnel
                connection_socket = tunnel.start(timeout=30)
                with self._resource_lock:
                    self._connection_socket = connection_socket
                if self.isInterruptionRequested():
                    raise RuntimeError("Connexion annulée.")
                target_host = self.profile.cloudflare_host
                target_port = 22
                self.status.emit("Canal Cloudflare actif")

            if self.isInterruptionRequested():
                raise RuntimeError("Connexion annulée.")
            self.status.emit("Connexion SSH…")
            client = paramiko.SSHClient()
            with self._resource_lock:
                self._client = client
            client.load_system_host_keys()
            if KNOWN_HOSTS_FILE.exists():
                client.load_host_keys(str(KNOWN_HOSTS_FILE))
            client.set_missing_host_key_policy(InteractiveHostKeyPolicy(self))
            kwargs = {
                "hostname": target_host,
                "port": int(target_port),
                "username": self.profile.user,
                "timeout": 30,
                "banner_timeout": 120,
                "auth_timeout": 60,
                "channel_timeout": 15,
                "look_for_keys": False,
                "allow_agent": False,
            }
            if connection_socket is not None:
                kwargs["sock"] = connection_socket
            if self.profile.auth_method == "key":
                kwargs["key_filename"] = self.profile.key_path
                kwargs["passphrase"] = self.secret
            else:
                kwargs["password"] = self.secret
            client.connect(**kwargs)
            if self.isInterruptionRequested():
                raise RuntimeError("Connexion annulée.")
            session = RemoteSession(self.profile, client, tunnel)
            self.connected.emit(session)
        except Exception as exc:
            if client:
                client.close()
            elif connection_socket:
                connection_socket.close()
            if tunnel:
                tunnel.stop()
            self.failed.emit(str(exc))
        finally:
            with self._resource_lock:
                self._client = None
                self._tunnel = None
                self._connection_socket = None
