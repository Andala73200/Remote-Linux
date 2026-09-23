import json
import copy
import os
import shutil
import tempfile
import time
from pathlib import Path

from app.config import DATA_FILE
from app.models import ConnectionProfile, FavoriteCommand
from app.package_favorite_storage import PackageFavoriteStorageMixin

DEFAULT_SETTINGS = {
    "language": "auto",
    "protected_mode": True,
    "command_preview": "sensitive",
    "system_level": "advanced",
    "disk_alert_percent": 85,
    "temperature_alert_c": 75,
    "allow_delete_nonlocal_services": False,
    "save_sudo_password": False,
    "smart_defaults_added": False,
    "file_column_rights_visible": True,
    "file_column_size_visible": True,
    "service_favorites_only": False,
    "windows_notifications": True,
    "terminal_right_click_paste": False,
    "terminal_venv_prompt_enabled": True,
    "terminal_cd_tree_sync": "ask",
    "terminal_completion_shortcut": "²",
    "package_favorites": {"pip": [], "apt": [], "dnf": [], "yum": []},
    "package_favorites_only": {"pip": False, "apt": False, "dnf": False, "yum": False},
    "backup_verifications": {},
    "network_port_favorites": {},
}

class Storage(PackageFavoriteStorageMixin):
    def __init__(self, path: Path = DATA_FILE):
        self.path = path
        self.profiles: list[ConnectionProfile] = []
        self.favorites: list[FavoriteCommand] = []
        self.service_favorites: dict[str, list[str]] = {}
        self.recent_files: dict[str, list[dict[str, object]]] = {}
        self.settings: dict[str, object] = copy.deepcopy(DEFAULT_SETTINGS)
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._create_defaults()
            self.save()
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except UnicodeError:
            self._recover_corrupt_file()
            return
        except OSError:
            raise  # I/O failure is not proof of corruption; preserve the file.
        try:
            payload = json.loads(raw)
            self.validate_payload(payload)
            self.profiles = [ConnectionProfile.from_dict(item) for item in payload.get("profiles", [])]
            self.favorites = [FavoriteCommand.from_dict(item) for item in payload.get("favorites", [])]
            raw_services = payload.get("service_favorites", {})
            self.service_favorites = {
                str(profile_id): sorted({str(name) for name in names})
                for profile_id, names in raw_services.items() if isinstance(names, list)
            }
            raw_recent = payload.get("recent_files", {})
            self.recent_files = {
                str(profile_id): [dict(item) for item in items if isinstance(item, dict)][:30]
                for profile_id, items in raw_recent.items() if isinstance(items, list)
            }
            self.settings = copy.deepcopy(DEFAULT_SETTINGS)
            self.settings.update(payload.get("settings", {}))
        except (ValueError, TypeError, AttributeError, KeyError):
            self._recover_corrupt_file()
            return
        if not self.profiles:
            self._create_defaults()
        migrated = self._migrate_cloudflare_profiles()
        distro_favorites_migrated = self._migrate_distribution_favorites()
        if self._ensure_smart_defaults() or migrated or distro_favorites_migrated:
            self.save()

    @staticmethod
    def validate_payload(payload: object) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("The configuration root must be a JSON object.")
        profiles = payload.get("profiles", [])
        favorites = payload.get("favorites", [])
        services = payload.get("service_favorites", {})
        recent = payload.get("recent_files", {})
        settings = payload.get("settings", {})
        if not isinstance(profiles, list) or not all(
            isinstance(item, dict) for item in profiles
        ):
            raise ValueError("The profile list is invalid.")
        if not isinstance(favorites, list) or not all(
            isinstance(item, dict) for item in favorites
        ):
            raise ValueError("The favorites list is invalid.")
        if not isinstance(services, dict) or not all(
            isinstance(names, list) for names in services.values()
        ):
            raise ValueError("Service favorites are invalid.")
        if not isinstance(recent, dict) or not all(
            isinstance(items, list)
            and all(isinstance(item, dict) for item in items)
            for items in recent.values()
        ):
            raise ValueError("Recent files are invalid.")
        if not isinstance(settings, dict):
            raise ValueError("Preferences are invalid.")
        for item in profiles:
            profile = ConnectionProfile.from_dict(item)
            port = int(profile.port)
            if not 1 <= port <= 65535:
                raise ValueError("An SSH port is out of range.")
        for item in favorites:
            favorite = FavoriteCommand.from_dict(item)
            if not isinstance(favorite.params, dict):
                raise ValueError("Favorite parameters are invalid.")
        return payload

    def import_configuration(self, source: Path) -> Path | None:
        payload = json.loads(source.read_text(encoding="utf-8"))
        self.validate_payload(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if self.path.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = self.path.with_name(
                f"{self.path.stem}.avant-import-{stamp}{self.path.suffix}"
            )
            shutil.copy2(self.path, backup)
        handle, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.import-",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
            self.load()
        except Exception:
            if backup and backup.exists():
                shutil.copy2(backup, self.path)
                self.load()
            raise
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        return backup

    def save(self) -> None:
        payload = {
            "schema_version": 1,
            "profiles": [profile.to_dict() for profile in self.profiles],
            "favorites": [favorite.to_dict() for favorite in self.favorites],
            "service_favorites": self.service_favorites,
            "recent_files": self.recent_files,
            "settings": self.settings,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, indent=2, ensure_ascii=False)
        handle, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
            text=True,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def purge_profile_data(self, profile_id: str) -> None:
        """Remove all cached/profile-scoped descendants while keeping the profile."""
        self.service_favorites.pop(profile_id, None)
        self.recent_files.pop(profile_id, None)
        for setting_name in ("network_port_favorites", "backup_verifications"):
            records = self.settings.get(setting_name, {})
            if isinstance(records, dict):
                records.pop(profile_id, None)
        self.save()

    def remove_profile_data(self, profile_id: str) -> None:
        self.profiles = [item for item in self.profiles if item.id != profile_id]
        self.service_favorites.pop(profile_id, None)
        self.recent_files.pop(profile_id, None)
        for setting_name in ("network_port_favorites", "backup_verifications"):
            records = self.settings.get(setting_name, {})
            if isinstance(records, dict):
                records.pop(profile_id, None)
        self.save()

    def _recover_corrupt_file(self) -> None:
        self._backup_corrupt_file()
        self._create_defaults()
        self.save()

    def _backup_corrupt_file(self) -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.stem}.corrompu-{stamp}{self.path.suffix}")
        shutil.copy2(self.path, backup)
        return backup

    def favorite_services(self, profile_id: str) -> set[str]:
        return set(self.service_favorites.get(profile_id, []))

    def set_service_favorite(self, profile_id: str, service: str, enabled: bool) -> None:
        names = self.favorite_services(profile_id)
        names.add(service) if enabled else names.discard(service)
        self.service_favorites[profile_id] = sorted(names)
        self.save()

    def add_recent_file(self, profile_id: str, path: str, action: str, is_dir: bool = False) -> None:
        items = self.recent_files.setdefault(profile_id, [])
        items[:] = [item for item in items if item.get("path") != path]
        items.insert(0, {"path": path, "action": action, "is_dir": is_dir, "time": int(time.time())})
        del items[30:]
        self.save()

    def recent_for(self, profile_id: str) -> list[dict[str, object]]:
        return list(self.recent_files.get(profile_id, []))

    def _create_defaults(self) -> None:
        self.profiles = [ConnectionProfile(
            name="Andala-Terra", kind="cloudflare", user="andala",
            auth_method="password", cloudflare_host="ssh.andala-terra.fr",
            cloudflare_auth_mode="service_token",
        )]
        self.favorites = []
        self.service_favorites = {}
        self.recent_files = {}
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self._ensure_smart_defaults()

    def _migrate_cloudflare_profiles(self) -> bool:
        changed = False
        for profile in self.profiles:
            if profile.kind != "cloudflare":
                continue
            if profile.cloudflare_auth_mode not in ("service_token", "user_login"):
                profile.cloudflare_auth_mode = "service_token"
                changed = True
        return changed

    def _migrate_distribution_favorites(self) -> bool:
        changed = False
        known_commands = {
            "apt list --upgradable",
            "if command -v dnf >/dev/null 2>&1; then dnf check-update; else apt list --upgradable; fi",
        }
        for favorite in self.favorites:
            if (
                favorite.name == "Mises à jour disponibles"
                and favorite.kind == "command"
                and favorite.command.strip() in known_commands
            ):
                favorite.kind = "smart_updates"
                favorite.command = ""
                changed = True
        return changed

    def _ensure_smart_defaults(self) -> bool:
        if bool(self.settings.get("smart_defaults_added", False)):
            return False
        defaults = [
            FavoriteCommand(name="Journaux d’un service", folder="Services", kind="smart_logs"),
            FavoriteCommand(name="État détaillé d’un service", folder="Services", kind="smart_service_status"),
            FavoriteCommand(name="Redémarrer un service", folder="Services", kind="smart_service_restart", confirm=True),
            FavoriteCommand(name="Processus les plus gourmands", folder="Système", kind="smart_top"),
            FavoriteCommand(name="Espace disque", folder="Système", command="df -hT", kind="command"),
            FavoriteCommand(name="Ports en écoute", folder="Réseau", command="ss -lntup", kind="command"),
            FavoriteCommand(name="Tester un port", folder="Réseau", kind="smart_test_port"),
            FavoriteCommand(name="Ping", folder="Réseau", kind="smart_ping"),
            FavoriteCommand(name="Rechercher un fichier", folder="Fichiers", kind="smart_find_file"),
            FavoriteCommand(name="Plus gros fichiers", folder="Fichiers", kind="smart_large_files"),
            FavoriteCommand(name="Mises à jour disponibles", folder="Maintenance/Mises à jour", kind="smart_updates"),
        ]
        existing = {(fav.kind, fav.name) for fav in self.favorites}
        for favorite in defaults:
            if (favorite.kind, favorite.name) not in existing:
                self.favorites.append(favorite)
        self.settings["smart_defaults_added"] = True
        return True
