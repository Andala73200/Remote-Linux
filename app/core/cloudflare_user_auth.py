from __future__ import annotations

import base64
import http.client
import json
import ssl
import time
import webbrowser
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.version import APP_VERSION

USER_AGENT = f"cloudflared/remote-linux-{APP_VERSION}"

@dataclass
class AccessApplication:
    hostname: str
    audience: str


def _decode_base64url(value: str) -> bytes:
    value = value.strip()
    value += "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value.encode("ascii"))


def token_expiration(token: str) -> int:
    try:
        payload = token.split(".")[1]
        data = json.loads(_decode_base64url(payload).decode("utf-8"))
        return int(data.get("exp", 0) or 0)
    except (IndexError, ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return 0


def token_is_usable(token: str | None, leeway_seconds: int = 60) -> bool:
    if not token:
        return False
    expires = token_expiration(token)
    return bool(expires and expires > int(time.time()) + leeway_seconds)


def discover_application(
    hostname: str,
    timeout: float = 12.0,
    cancelled: Callable[[], bool] | None = None,
) -> AccessApplication:
    current_url = f"https://{hostname}/"
    context = ssl.create_default_context()
    for _ in range(8):
        if cancelled and cancelled():
            raise RuntimeError("Connexion annulée.")
        parsed = urlsplit(current_url)
        port = parsed.port or 443
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        connection = http.client.HTTPSConnection(
            parsed.hostname, port, timeout=timeout, context=context
        )
        try:
            connection.request("HEAD", path, headers={"User-Agent": USER_AGENT})
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            response.read()
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            raise RuntimeError(f"Impossible d'interroger l'application Cloudflare : {exc}") from exc
        finally:
            connection.close()

        audience = headers.get("cf-access-aud", "").strip()
        if audience:
            return AccessApplication(hostname, audience)

        location = headers.get("location", "")
        if not location:
            raise RuntimeError(
                "Cloudflare n'a pas renvoyé l'identifiant de l'application Access."
            )
        current_url = urljoin(current_url, location)
        target = urlsplit(current_url)
        if "/cdn-cgi/access/login" in target.path:
            audience = parse_qs(target.query).get("kid", [""])[0].strip()
            if audience:
                return AccessApplication(hostname, audience)
    raise RuntimeError("Trop de redirections pendant la détection Cloudflare Access.")


def _build_login_url(application: AccessApplication, public_key: str) -> str:
    base_url = f"https://{application.hostname}/"
    first_query = urlencode({"token": public_key, "aud": application.audience})
    redirect_url = f"{base_url}?{first_query}"
    query = urlencode(
        {
            "token": public_key,
            "aud": application.audience,
            "redirect_url": redirect_url,
            "send_org_token": "true",
            "edge_token_transfer": "true",
            "close_interstitial": "true",
        }
    )
    return f"https://{application.hostname}/cdn-cgi/access/cli?{query}"


def _load_nacl():
    try:
        from nacl.public import Box, PrivateKey, PublicKey
    except ImportError as exc:
        raise RuntimeError(
            "Le mode Cloudflare utilisateur nécessite PyNaCl. "
            "Installe les dépendances depuis requirements.txt."
        ) from exc
    return Box, PrivateKey, PublicKey


def _decrypt_transfer(body: bytes, service_public_key: str, private_key: Any) -> str:
    Box, _PrivateKey, PublicKey = _load_nacl()
    try:
        encrypted = base64.b64decode(body.strip())
        server_key = PublicKey(_decode_base64url(service_public_key))
        decrypted = Box(private_key, server_key).decrypt(encrypted)
        payload = json.loads(decrypted.decode("utf-8"))
        token = str(payload.get("app_token", "")).strip()
    except Exception as exc:
        raise RuntimeError("La réponse d'authentification Cloudflare est illisible.") from exc
    if not token:
        raise RuntimeError("Cloudflare n'a renvoyé aucun jeton de session utilisateur.")
    return token


def fetch_user_token(
    hostname: str,
    log_callback=None,
    timeout: float = 60.0,
    attempts: int = 10,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    log = log_callback or (lambda _text: None)
    application = discover_application(hostname, cancelled=cancelled)
    _Box, PrivateKey, _PublicKey = _load_nacl()
    private_key = PrivateKey.generate()
    public_key = base64.urlsafe_b64encode(bytes(private_key.public_key)).decode("ascii")
    login_url = _build_login_url(application, public_key)
    transfer_url = (
        "https://login.cloudflareaccess.org/transfer/"
        + quote(public_key, safe="-_=.")
    )

    log(">>> Ouverture de l'authentification Cloudflare dans le navigateur…\n")
    if not webbrowser.open(login_url):
        log(f">>> Ouvre manuellement cette adresse dans ton navigateur :\n{login_url}\n")
    log(">>> En attente de la validation Cloudflare…\n")

    for _ in range(attempts):
        if cancelled and cancelled():
            raise RuntimeError("Connexion annulée.")
        request = Request(transfer_url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    continue
                service_key = response.headers.get("service-public-key", "").strip()
                if not service_key:
                    raise RuntimeError("Clé de transfert Cloudflare absente.")
                return _decrypt_transfer(response.read(), service_key, private_key)
        except HTTPError as exc:
            if exc.code >= 500:
                raise RuntimeError(f"Service d'authentification Cloudflare indisponible ({exc.code}).") from exc
        except (URLError, TimeoutError, OSError):
            continue
    raise TimeoutError("Authentification Cloudflare non validée dans le délai prévu.")
