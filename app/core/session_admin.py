import re
import shlex
import time

from app.core.distribution import (
    get_distribution_adapter, parse_os_release, select_package_manager,
)

SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9.+:-]+$")


class SessionAdminMixin:
    def _distribution_adapter(self):
        return get_distribution_adapter(
            getattr(self.profile, "distribution", "auto"),
            getattr(self, "_package_manager", ""),
        )

    def systemctl(self, action: str, service: str, password: str | None = None) -> tuple[int, str]:
        if action not in {"start", "stop", "restart", "reload", "enable", "disable"}:
            raise ValueError("Action systemctl non autorisée.")
        if not SERVICE_NAME_RE.fullmatch(service):
            raise ValueError("Nom de service invalide.")
        return self._sudo_command(
            f"systemctl {action} -- {shlex.quote(service)}", password, timeout=40
        )

    def validate_sudo(self, password: str) -> tuple[int, str]:
        if not password:
            return 1, "Mot de passe sudo vide."
        return self._sudo_command("-v", password, timeout=30, raw_sudo=True)

    def distribution_info(self) -> dict[str, str]:
        command = """
cat /etc/os-release 2>/dev/null
printf '__REMOTE_LINUX_CAPABILITIES__\\n'
for name in apt apt-cache dnf yum systemctl rc-service ufw firewall-cmd nft smartctl psql pgbackrest; do
  command -v "$name" >/dev/null 2>&1 && printf '%s\\n' "$name"
done
""".strip()
        code, output = self.execute(command, timeout=10)
        if code != 0 or not output:
            return {"family": "other", "name": "Linux", "id": ""}
        release, _, capability_text = output.partition(
            "__REMOTE_LINUX_CAPABILITIES__"
        )
        info = parse_os_release(release)
        capabilities = set(capability_text.split())
        manager = select_package_manager(info, capabilities)
        info["package_manager"] = manager
        self._package_manager = manager
        self._detected_distribution = dict(info)
        info["service_manager"] = (
            "systemd" if "systemctl" in capabilities else
            "openrc" if "rc-service" in capabilities else ""
        )
        info["firewall"] = next(
            (name for name in ("ufw", "firewall-cmd", "nft") if name in capabilities),
            "",
        )
        info["smart"] = "yes" if "smartctl" in capabilities else "no"
        info["postgresql"] = "yes" if "psql" in capabilities else "no"
        info["pgbackrest"] = "yes" if "pgbackrest" in capabilities else "no"
        return info

    def list_services(self) -> list[dict[str, str]]:
        units = "LC_ALL=C systemctl list-units --type=service --all --no-legend --plain --no-pager"
        files = "LC_ALL=C systemctl list-unit-files --type=service --no-legend --no-pager"
        code, output = self.execute(f"{units}; printf '\n__FILES__\n'; {files}")
        if code != 0 and not output:
            raise RuntimeError("Impossible de lire les services systemd.")
        units_text, _, files_text = output.partition("__FILES__")
        enabled = {}
        for line in files_text.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                enabled[parts[0]] = parts[1]
        services = []
        for line in units_text.splitlines():
            parts = line.split(None, 5)
            if parts and parts[0] in {"●", "○", "×"}:
                parts = parts[1:]
            if len(parts) < 4:
                continue
            services.append({
                "unit": parts[0], "load": parts[1], "active": parts[2],
                "sub": parts[3],
                "description": " ".join(parts[4:]) if len(parts) > 4 else "",
                "enabled": enabled.get(parts[0], "-"),
            })
        return services

    def service_logs(self, service: str, count: int = 200) -> str:
        if not SERVICE_NAME_RE.fullmatch(service):
            raise ValueError("Nom de service invalide.")
        code, output = self.execute(
            f"journalctl -u {shlex.quote(service)} -n {int(count)} --no-pager"
        )
        if code != 0 and not output:
            raise RuntimeError("Impossible de lire les journaux du service.")
        return output

    def update_count(self) -> int:
        return self._distribution_adapter().update_count(self.execute)

    def list_updates(self) -> list[dict[str, str]]:
        return self._distribution_adapter().list_updates(self.execute)

    def updates_install_preview(self, packages: list[str]) -> str:
        clean = sorted({name for name in packages if PACKAGE_NAME_RE.fullmatch(name)})
        return self._distribution_adapter().install_updates_preview(clean)

    def quick_updates_command(self) -> str:
        return self._distribution_adapter().quick_updates_command()

    def install_updates(self, packages: list[str], password: str | None) -> tuple[int, str]:
        clean = sorted({name for name in packages if PACKAGE_NAME_RE.fullmatch(name)})
        if not clean:
            return 1, "Aucun paquet valide sélectionné."
        base = self._distribution_adapter().install_updates_command(clean)
        return self._sudo_command(base, password, timeout=1800)

    def service_details(self, service: str) -> dict[str, str | bool]:
        if not SERVICE_NAME_RE.fullmatch(service):
            raise ValueError("Nom de service invalide.")
        command = (
            "systemctl show --no-pager --property=FragmentPath,UnitFileState,LoadState "
            f"-- {shlex.quote(service)}"
        )
        code, output = self.execute(command)
        if code != 0 and not output:
            raise RuntimeError("Impossible d’identifier le fichier du service.")
        values = dict(line.partition("=")[::2] for line in output.splitlines() if "=" in line)
        path = values.get("FragmentPath", "")
        package = self._distribution_adapter().package_owner(self.execute, path) if path else ""
        return {
            "service": service, "path": path,
            "unit_state": values.get("UnitFileState", ""),
            "load_state": values.get("LoadState", ""),
            "package": package,
            "local": path.startswith("/etc/systemd/system/"),
            "generated": path.startswith("/run/systemd/"),
        }

    def delete_service(
        self,
        service: str,
        password: str | None,
        allow_nonlocal: bool,
        remove_package: bool = False,
    ) -> tuple[int, str]:
        details = self.service_details(service)
        path = str(details.get("path") or "")
        if not path:
            return 1, "Ce service ne possède pas de fichier d’unité supprimable."
        if bool(details.get("generated")):
            return 1, "Les services générés dans /run/systemd ne peuvent pas être supprimés ici."
        if not bool(details.get("local")) and not allow_nonlocal:
            return 1, "La suppression des services non créés localement est désactivée."
        package = str(details.get("package") or "")
        if remove_package:
            if not package or not PACKAGE_NAME_RE.fullmatch(package):
                return 1, "Aucun paquet valide n’a été identifié pour ce service."
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = f"/var/backups/remote-linux/systemd/{stamp}"
            remove_command = self._distribution_adapter().remove_package_command(package)
            script = f"""
set -eu
unit={shlex.quote(service)}
unit_path={shlex.quote(path)}
package={shlex.quote(package)}
backup={shlex.quote(backup)}
mkdir -p -- "$backup"
cp -a -- "$unit_path" "$backup/"
{remove_command}
systemctl daemon-reload
systemctl reset-failed -- "$unit" 2>/dev/null || true
printf 'Paquet %s désinstallé. Sauvegarde du service : %s\n' "$package" "$backup"
""".strip()
            return self._sudo_command(
                f"sh -c {shlex.quote(script)}",
                password,
                timeout=1800,
            )
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = f"/var/backups/remote-linux/systemd/{stamp}"
        dropin = f"/etc/systemd/system/{service}.d"
        script = f"""
set -eu
unit={shlex.quote(service)}
unit_path={shlex.quote(path)}
backup={shlex.quote(backup)}
dropin={shlex.quote(dropin)}
systemctl stop -- "$unit" 2>/dev/null || true
systemctl disable -- "$unit" 2>/dev/null || true
mkdir -p -- "$backup"
cp -a -- "$unit_path" "$backup/"
if [ -d "$dropin" ]; then cp -a -- "$dropin" "$backup/"; rm -rf -- "$dropin"; fi
rm -f -- "$unit_path"
systemctl daemon-reload
systemctl reset-failed -- "$unit" 2>/dev/null || true
printf 'Service supprimé. Sauvegarde : %s\n' "$backup"
""".strip()
        return self._sudo_command(f"sh -c {shlex.quote(script)}", password, timeout=90)

    def _sudo_command(
        self,
        base_command: str,
        password: str | None,
        timeout: float = 90,
        raw_sudo: bool = False,
    ) -> tuple[int, str]:
        self._ensure_open()
        if raw_sudo:
            command = (
                f"sudo -S -p '' {base_command}"
                if password else f"sudo -n {base_command}"
            )
        else:
            command = f"sudo -S -p '' {base_command}" if password else f"sudo -n {base_command}"
        with self._exec_lock:
            self._ensure_open()
            stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            if password:
                stdin.write(password + "\n")
                stdin.flush()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
            return code, (out + err).strip()
