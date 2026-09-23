from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Callable

from app.core.package_catalog import search_apt, search_dnf, search_yum

ExecuteFn = Callable[..., tuple[int, str]]
KNOWN_ARCHES = {
    "x86_64", "noarch", "aarch64", "i686", "i586", "ppc64le", "s390x", "src",
}


def normalize_distribution(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"ubuntu", "debian"}:
        return "ubuntu"
    if raw in {"redhat", "rhel", "fedora", "centos", "rocky", "almalinux"}:
        return "redhat"
    if raw in {"auto", "other"}:
        return raw
    return "other"


def distribution_label(value: str | None) -> str:
    normalized = normalize_distribution(value)
    return {
        "auto": "Détection automatique",
        "ubuntu": "Debian / Ubuntu",
        "redhat": "RHEL / Fedora",
        "other": "Linux non pris en charge",
    }[normalized]


@dataclass(frozen=True)
class DistributionAdapter:
    key: str
    label: str

    def update_count(self, execute: ExecuteFn) -> int:
        raise NotImplementedError

    def list_updates(self, execute: ExecuteFn) -> list[dict[str, str]]:
        raise NotImplementedError

    def install_updates_command(self, packages: list[str]) -> str:
        raise NotImplementedError

    def install_updates_preview(self, packages: list[str]) -> str:
        return "sudo " + self.install_updates_command(packages)

    def package_owner(self, execute: ExecuteFn, path: str) -> str:
        raise NotImplementedError

    def remove_package_command(self, package: str) -> str:
        raise NotImplementedError

    def quick_updates_command(self) -> str:
        raise NotImplementedError

    def package_manager_command(self) -> str:
        raise NotImplementedError

    def install_command(self, package: str) -> str:
        raise NotImplementedError

    def search_packages(self, execute: ExecuteFn, query: str) -> list[dict[str, str]]:
        raise NotImplementedError


class AptAdapter(DistributionAdapter):
    def __init__(self) -> None:
        super().__init__("ubuntu", "Debian / Ubuntu")

    def update_count(self, execute: ExecuteFn) -> int:
        command = (
            "timeout 25 sh -c \"apt list --upgradable 2>/dev/null | "
            "tail -n +2 | sed '/^$/d' | wc -l\""
        )
        code, output = execute(command, timeout=30)
        if code != 0:
            raise RuntimeError(output or "Vérification des mises à jour APT impossible.")
        return int(output.strip() or 0)

    def list_updates(self, execute: ExecuteFn) -> list[dict[str, str]]:
        code, output = execute(
            "LC_ALL=C apt list --upgradable 2>/dev/null | tail -n +2", timeout=40
        )
        if code != 0:
            raise RuntimeError(output or "Lecture des mises à jour APT impossible.")
        pattern = re.compile(
            r"^(?P<name>[^/]+)/\S+\s+(?P<new>\S+)\s+(?P<arch>\S+)\s+"
            r"\[upgradable from: (?P<old>[^\]]+)\]"
        )
        return [
            match.groupdict()
            for line in output.splitlines()
            if (match := pattern.match(line.strip()))
        ]

    def install_updates_command(self, packages: list[str]) -> str:
        args = " ".join(shlex.quote(name) for name in packages)
        return f"env DEBIAN_FRONTEND=noninteractive apt-get install --only-upgrade -y -- {args}"

    def package_owner(self, execute: ExecuteFn, path: str) -> str:
        _, output = execute(
            f"dpkg-query -S -- {shlex.quote(path)} 2>/dev/null | head -n 1"
        )
        return output.partition(":")[0].strip()

    def remove_package_command(self, package: str) -> str:
        return f"apt-get remove -y -- {shlex.quote(package)}"

    def quick_updates_command(self) -> str:
        return "apt list --upgradable"

    def package_manager_command(self) -> str:
        return "apt"

    def install_command(self, package: str) -> str:
        return f"apt-get install -y -- {shlex.quote(package)}"

    def search_packages(self, execute: ExecuteFn, query: str) -> list[dict[str, str]]:
        return search_apt(execute, query)


class RpmAdapter(DistributionAdapter):
    def __init__(self, manager: str) -> None:
        super().__init__("redhat", "RHEL / Fedora")
        object.__setattr__(self, "manager", manager)

    @staticmethod
    def _parse_check_update(output: str) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for raw in output.splitlines():
            line = raw.strip()
            if not line or line.startswith(("Last metadata", "Loaded plugins", "Obsoleting", "Security:")):
                continue
            parts = line.split()
            if len(parts) < 3 or "." not in parts[0]:
                continue
            name, arch = parts[0].rsplit(".", 1)
            if arch not in KNOWN_ARCHES or not name:
                continue
            rows.append({"name": name, "arch": arch, "new": parts[1], "old": ""})
        return rows

    @staticmethod
    def _installed_versions(output: str) -> dict[tuple[str, str], str]:
        versions: dict[tuple[str, str], str] = {}
        for line in output.splitlines():
            parts = line.strip().split("|", 2)
            if len(parts) != 3:
                continue
            version = parts[2][2:] if parts[2].startswith("0:") else parts[2]
            versions[(parts[0], parts[1])] = version
        return versions

    def _available(self, execute: ExecuteFn) -> list[dict[str, str]]:
        code, output = execute(f"LC_ALL=C {self.manager} -q check-update", timeout=60)
        if code not in {0, 100}:
            raise RuntimeError(
                output or f"Vérification des mises à jour {self.manager.upper()} impossible."
            )
        return self._parse_check_update(output)

    def update_count(self, execute: ExecuteFn) -> int:
        return len(self._available(execute))

    def list_updates(self, execute: ExecuteFn) -> list[dict[str, str]]:
        rows = self._available(execute)
        if not rows:
            return []
        code, installed = execute(
            "rpm -qa --qf '%{NAME}|%{ARCH}|%{EPOCHNUM}:%{VERSION}-%{RELEASE}\\n'",
            timeout=40,
        )
        versions = self._installed_versions(installed) if code == 0 else {}
        for row in rows:
            row["old"] = versions.get((row["name"], row["arch"]), "installé")
        return rows

    def install_updates_command(self, packages: list[str]) -> str:
        args = " ".join(shlex.quote(name) for name in packages)
        verb = "upgrade" if self.manager == "dnf" else "update"
        return f"{self.manager} {verb} -y {args}"

    def package_owner(self, execute: ExecuteFn, path: str) -> str:
        _, output = execute(
            f"rpm -qf --qf '%{{NAME}}\\n' {shlex.quote(path)} 2>/dev/null | head -n 1"
        )
        return output.strip()

    def remove_package_command(self, package: str) -> str:
        return f"{self.manager} remove -y {shlex.quote(package)}"

    def quick_updates_command(self) -> str:
        return f"{self.manager} check-update"

    def package_manager_command(self) -> str:
        return self.manager

    def install_command(self, package: str) -> str:
        return f"{self.manager} install -y {shlex.quote(package)}"

    def search_packages(self, execute: ExecuteFn, query: str) -> list[dict[str, str]]:
        return search_dnf(execute, query) if self.manager == "dnf" else search_yum(execute, query)


_ADAPTERS = {
    "apt": AptAdapter(),
    "dnf": RpmAdapter("dnf"),
    "yum": RpmAdapter("yum"),
}


def get_distribution_adapter(
    value: str | None,
    package_manager: str | None = None,
) -> DistributionAdapter:
    manager = str(package_manager or "").strip().lower()
    if manager in _ADAPTERS:
        return _ADAPTERS[manager]
    normalized = normalize_distribution(value)
    if normalized == "ubuntu":
        return _ADAPTERS["apt"]
    if normalized == "redhat":
        return _ADAPTERS["dnf"]
    raise RuntimeError("La famille Linux n'est pas encore prise en charge pour cette fonction.")


def select_package_manager(
    info: dict[str, str], capabilities: set[str]
) -> str:
    family = str(info.get("family") or "other")
    distro_id = str(info.get("id") or "").lower()
    version = str(info.get("version_id") or "")
    match = re.match(r"^(\d+)", version)
    major = int(match.group(1)) if match else None

    if family == "ubuntu" and "apt" in capabilities:
        return "apt"
    if family == "redhat":
        legacy = distro_id in {"rhel", "redhat", "centos", "ol", "scientific"}
        if legacy and major is not None and major <= 7 and "yum" in capabilities:
            return "yum"
        if "dnf" in capabilities:
            return "dnf"
        if "yum" in capabilities:
            return "yum"
    return next(
        (name for name in ("apt", "dnf", "yum") if name in capabilities),
        "",
    )


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    distro_id = values.get("ID", "").lower()
    like = values.get("ID_LIKE", "").lower().split()
    family = "other"
    if distro_id in {"ubuntu", "debian"} or any(item in {"ubuntu", "debian"} for item in like):
        family = "ubuntu"
    elif distro_id in {
        "rhel", "redhat", "centos", "rocky", "almalinux", "fedora",
        "ol", "scientific", "amzn",
    } or any(
        item in {"rhel", "redhat", "fedora", "centos"} for item in like
    ):
        family = "redhat"
    return {
        "family": family,
        "name": values.get("PRETTY_NAME") or values.get("NAME") or distro_id or "Linux",
        "id": distro_id,
        "version_id": values.get("VERSION_ID", ""),
        "version_codename": values.get("VERSION_CODENAME", ""),
    }
