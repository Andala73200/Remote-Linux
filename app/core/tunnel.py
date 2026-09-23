from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

import websocket

from app.core.cloudflare_auth import AccessAuth, authenticate_service_token, clean_credential
from app.core.cloudflare_user_auth import fetch_user_token, token_is_usable
from app.core.cloudflare_socket import CloudflareSocket
from app.models import ConnectionProfile
from app.version import APP_VERSION



class CloudflareTunnel:
    """Standalone Cloudflare Access transport without an external executable."""

    USER_AGENT = f"cloudflared/remote-linux-{APP_VERSION}"
    CLIENT_ID_HEADER = "CF-Access-Client-Id"
    CLIENT_SECRET_HEADER = "CF-Access-Client-Secret"

    def __init__(
        self,
        profile: ConnectionProfile,
        log_callback=None,
        service_token_secret: str | None = None,
        user_token: str | None = None,
        user_token_callback=None,
        cancelled: Callable[[], bool] | None = None,
    ):
        self.profile = profile
        self.log_callback = log_callback or (lambda _text: None)
        self.service_token_secret = service_token_secret or None
        self.user_token = user_token or None
        self.user_token_callback = user_token_callback or (lambda _token: None)
        self.cancelled = cancelled or (lambda: False)
        self.socket: CloudflareSocket | None = None

    @staticmethod
    def _urls(host_value: str) -> tuple[str, str, int]:
        raw = host_value.strip()
        if not raw:
            raise RuntimeError("L'hôte Cloudflare est manquant.")
        if "://" not in raw:
            raw = "https://" + raw
        parsed = urlsplit(raw)
        hostname = parsed.hostname or ""
        if not hostname:
            raise RuntimeError("L'hôte Cloudflare est invalide.")
        if parsed.scheme not in ("", "https", "wss"):
            raise RuntimeError(
                "Cloudflare Access exige une adresse HTTPS/WSS chiffrée."
            )
        port = parsed.port or 443
        netloc = hostname if port == 443 else f"{hostname}:{port}"
        scheme = "wss"
        url = urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
        return url, hostname, port

    def _open_websocket(
        self,
        url: str,
        hostname: str,
        headers: dict[str, str],
        timeout: float,
        cookie: str = "",
    ) -> websocket.WebSocket:
        headers = dict(headers)
        headers["User-Agent"] = self.USER_AGENT
        parsed = urlsplit(url)
        host_header = parsed.hostname or hostname
        if parsed.port and parsed.port != 443:
            host_header = f"{host_header}:{parsed.port}"
        if self.cancelled():
            raise RuntimeError("Connexion annulée.")
        return websocket.create_connection(
            url,
            header=headers,
            cookie=cookie or None,
            host=host_header,
            timeout=timeout,
            enable_multithread=True,
            suppress_origin=True,
            redirect_limit=0,
        )

    @staticmethod
    def _failure_details(exc: websocket.WebSocketBadStatusException) -> tuple[int, str, str]:
        status = int(getattr(exc, "status_code", 0) or 0)
        headers = getattr(exc, "resp_headers", None) or {}
        values = {str(k).lower(): str(v) for k, v in headers.items()} if hasattr(headers, "items") else {}
        return status, values.get("cf-ray", ""), values.get("cf-access-aud", "")

    def _service_websocket(self, url: str, hostname: str, port: int, timeout: float):
        client_id = clean_credential(self.profile.cloudflare_client_id or "", self.CLIENT_ID_HEADER)
        secret = clean_credential(self.service_token_secret or "", self.CLIENT_SECRET_HEADER)
        if not client_id or not secret:
            raise RuntimeError("Le Client ID ou le Client Secret Cloudflare est manquant.")
        if not client_id.endswith(".access"):
            raise RuntimeError("Le Client ID Cloudflare doit normalement se terminer par .access.")
        self.log_callback(">>> Validation du jeton de service Cloudflare…\n")
        auth: AccessAuth = authenticate_service_token(
            hostname, port, client_id, secret, self.USER_AGENT, timeout
        )
        if auth.status in (401, 403) and not auth.token:
            raise RuntimeError("Cloudflare a refusé le jeton de service.")
        headers = {
            self.CLIENT_ID_HEADER: client_id,
            self.CLIENT_SECRET_HEADER: secret,
        }
        if auth.token:
            headers["Cf-Access-Token"] = auth.token
        return self._open_websocket(url, hostname, headers, timeout, auth.cookie)

    def _fetch_user_token(self, hostname: str) -> str:
        token = fetch_user_token(
            hostname,
            self.log_callback,
            cancelled=self.cancelled,
        )
        self.user_token = token
        self.user_token_callback(token)
        self.log_callback(">>> Session utilisateur Cloudflare enregistrée.\n")
        return token

    def _user_websocket(self, url: str, hostname: str, timeout: float):
        cached = token_is_usable(self.user_token)
        token = self.user_token if cached else self._fetch_user_token(hostname)
        try:
            return self._open_websocket(
                url, hostname, {"Cf-Access-Token": token or ""}, timeout
            )
        except websocket.WebSocketBadStatusException as exc:
            status, _ray, _aud = self._failure_details(exc)
            if cached and status in (301, 302, 303, 307, 308, 401, 403):
                self.log_callback(">>> Session Cloudflare expirée. Nouvelle authentification…\n")
                token = self._fetch_user_token(hostname)
                return self._open_websocket(
                    url, hostname, {"Cf-Access-Token": token}, timeout
                )
            raise

    def start(self, timeout: float = 30.0) -> CloudflareSocket:
        url, hostname, port = self._urls(self.profile.cloudflare_host)
        self.log_callback(f">>> Connexion Cloudflare intégrée vers {hostname}…\n")
        try:
            if self.profile.cloudflare_auth_mode == "user_login":
                ws = self._user_websocket(url, hostname, timeout)
            else:
                ws = self._service_websocket(url, hostname, port, timeout)
        except websocket.WebSocketBadStatusException as exc:
            status, cf_ray, audience = self._failure_details(exc)
            details = ", ".join(
                value for value in (
                    f"CF-Ray {cf_ray}" if cf_ray else "",
                    f"AUD {audience}" if audience else "",
                ) if value
            )
            suffix = f" ({details})" if details else ""
            if status in (301, 302, 303, 307, 308, 401, 403):
                raise RuntimeError("Cloudflare a refusé l'authentification" + suffix + ".") from exc
            raise RuntimeError(f"Connexion Cloudflare refusée (HTTP {status or 'inconnu'}){suffix}.") from exc
        except websocket.WebSocketTimeoutException as exc:
            raise TimeoutError("Cloudflare n'a pas répondu dans le délai prévu.") from exc
        except (RuntimeError, TimeoutError):
            raise
        except Exception as exc:
            raise RuntimeError(f"Connexion Cloudflare impossible : {exc}") from exc

        if self.cancelled():
            try:
                ws.close()
            finally:
                raise RuntimeError("Connexion annulée.")
        self.socket = CloudflareSocket(ws, hostname, timeout)
        self.log_callback(">>> Canal Cloudflare établi.\n")
        return self.socket

    def is_alive(self) -> bool:
        return bool(self.socket and not self.socket.closed)

    def stop(self) -> None:
        if self.socket:
            self.socket.close()
            self.socket = None
