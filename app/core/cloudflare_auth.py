from __future__ import annotations

import http.client
import ssl
from dataclasses import dataclass
from http.cookies import SimpleCookie


@dataclass
class AccessAuth:
    token: str = ""
    cookie: str = ""
    status: int = 0
    cf_ray: str = ""
    audience: str = ""
    location: str = ""


def clean_credential(value: str, header_name: str) -> str:
    cleaned = (value or "").strip()
    prefix = header_name.lower() + ":"
    if cleaned.lower().startswith(prefix):
        cleaned = cleaned[len(prefix):].strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "'\"":
        cleaned = cleaned[1:-1].strip()
    return cleaned


def extract_authorization_cookie(values: list[str]) -> tuple[str, str]:
    for value in values:
        cookie = SimpleCookie()
        try:
            cookie.load(value)
        except Exception:
            continue
        morsel = cookie.get("CF_Authorization")
        if morsel and morsel.value:
            return morsel.value, f"CF_Authorization={morsel.value}"
    return "", ""


def authenticate_service_token(
    hostname: str,
    port: int,
    client_id: str,
    secret: str,
    user_agent: str,
    timeout: float,
) -> AccessAuth:
    """Request an application JWT from Access before opening the WebSocket."""
    connection = http.client.HTTPSConnection(
        hostname,
        port,
        timeout=min(timeout, 15.0),
        context=ssl.create_default_context(),
    )
    headers = {
        "CF-Access-Client-Id": client_id,
        "CF-Access-Client-Secret": secret,
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Connection": "close",
    }
    try:
        connection.request("GET", "/cdn-cgi/access/authorized", headers=headers)
        response = connection.getresponse()
        all_headers = response.getheaders()
        response.read(4096)
        header_map = {key.lower(): value for key, value in all_headers}
        set_cookie = [
            value for key, value in all_headers if key.lower() == "set-cookie"
        ]
        token, cookie = extract_authorization_cookie(set_cookie)
        return AccessAuth(
            token=token,
            cookie=cookie,
            status=int(response.status or 0),
            cf_ray=header_map.get("cf-ray", ""),
            audience=header_map.get("cf-access-aud", ""),
            location=header_map.get("location", ""),
        )
    except (OSError, http.client.HTTPException, TimeoutError):
        # The direct WebSocket attempt is still made if the HTTP check does not respond.
        return AccessAuth()
    finally:
        connection.close()
