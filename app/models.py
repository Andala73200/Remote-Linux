from dataclasses import asdict, dataclass, field
from typing import Any
import uuid


@dataclass
class ConnectionProfile:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "Nouveau serveur"
    kind: str = "ssh"
    user: str = ""
    distribution: str = "auto"
    show_health_popup: bool = True
    auth_method: str = "password"
    key_path: str = ""
    host: str = ""
    port: int = 22
    cloudflare_host: str = ""
    cloudflare_auth_mode: str = "service_token"
    cloudflare_client_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConnectionProfile":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in data.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def connection_type(self) -> str:
        if self.kind != "cloudflare":
            return "ssh"
        return "cloudflare_user" if self.cloudflare_auth_mode == "user_login" else "cloudflare_service"


@dataclass
class FavoriteCommand:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "Nouvelle commande"
    command: str = ""
    folder: str = "Général"
    confirm: bool = False
    follow_tree: bool = False
    kind: str = "command"
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FavoriteCommand":
        payload = dict(data)
        if "folder" not in payload and "category" in payload:
            payload["folder"] = payload.get("category") or "Général"
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in payload.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
