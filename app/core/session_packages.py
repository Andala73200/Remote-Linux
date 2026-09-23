from __future__ import annotations

import json
import posixpath
import re
import shlex

from app.core.pip_repository_search import (
    build_repository_search_command, parse_repository_search,
)


PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9.+:-]+$")
PIP_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class SessionPackageMixin:
    def package_manager_command(self) -> str:
        return self._distribution_adapter().package_manager_command()

    def package_search(
        self, query: str, manager: str = "", venv_path: str = ""
    ) -> dict[str, object]:
        clean = str(query).strip()
        if not clean or len(clean) > 100 or any(char in clean for char in "\r\n\0"):
            raise ValueError("La recherche doit contenir entre 1 et 100 caractères.")
        if manager == "pip":
            return {"manager": "pip", "rows": self._search_pip(clean, venv_path)}
        adapter = self._distribution_adapter()
        return {
            "manager": adapter.package_manager_command(),
            "rows": adapter.search_packages(self.execute, clean),
        }

    def package_install_preview(
        self, package: str, manager: str = "", venv_path: str = ""
    ) -> str:
        if manager == "pip":
            self._validate_pip_package(package)
            return f"{self._pip_command(venv_path)} install --upgrade {shlex.quote(package)}"
        if not PACKAGE_NAME_RE.fullmatch(package):
            raise ValueError("Nom de paquet invalide.")
        return "sudo " + self._distribution_adapter().install_command(package)

    def install_package(
        self, package: str, password: str | None,
        manager: str = "", venv_path: str = "",
    ) -> tuple[int, str]:
        if manager == "pip":
            try:
                self._validate_pip_package(package)
            except ValueError as exc:
                return 1, str(exc)
            command = (
                f"{self._pip_command(venv_path)} install --upgrade "
                f"{shlex.quote(package)}"
            )
            return self.execute(command, timeout=1800)
        if not PACKAGE_NAME_RE.fullmatch(package):
            return 1, "Nom de paquet invalide."
        command = self._distribution_adapter().install_command(package)
        return self._sudo_command(command, password, timeout=1800)

    def project_venv(self, path: str) -> str:
        clean = str(path or "")
        if (
            not clean.startswith("/")
            or len(clean) > 4096
            or any(char in clean for char in "\r\n\0")
        ):
            return ""
        base = clean.rstrip("/") or "/"
        candidate = posixpath.join(base, ".venv")
        activate = posixpath.join(candidate, "bin", "activate")
        python = posixpath.join(candidate, "bin", "python")
        code, _output = self.execute(
            f"test -f {shlex.quote(activate)} && test -x {shlex.quote(python)}",
            timeout=10,
        )
        return candidate if code == 0 else ""

    def _search_pip(self, package: str, venv_path: str) -> list[dict[str, object]]:
        self._validate_pip_package(package)
        pip = self._pip_command(venv_path)
        code, output = self.execute(
            f"LC_ALL=C {pip} index versions --json {shlex.quote(package)}",
            timeout=90,
        )
        output = self._clean_pip_output(output)
        payload = self._pip_json(output) if code == 0 else {}
        if payload:
            name = str(payload.get("name") or package)
            versions = [str(value) for value in payload.get("versions", [])]
            latest = str(payload.get("latest") or (versions[0] if versions else ""))
            installed = str(payload.get("installed_version") or "")
        else:
            code, output = self.execute(
                f"LC_ALL=C {pip} index versions {shlex.quote(package)}",
                timeout=90,
            )
            output = self._clean_pip_output(output)
            if code != 0:
                if "No matching distribution found for" in output:
                    return self._search_pip_repositories(package, venv_path)
                raise RuntimeError(
                    output or "pip index failed without an error message."
                )
            name, latest, versions, installed = self._parse_pip_index(
                package, output
            )
        show_code, show_output = self.execute(
            f"LC_ALL=C {pip} show {shlex.quote(package)}", timeout=30
        )
        if show_code == 0:
            for line in show_output.splitlines():
                if line.startswith("Version:"):
                    installed = line.partition(":")[2].strip()
                    break
        return [{
            "name": name,
            "version": latest,
            "installed_version": installed,
            "versions": versions[:12],
        }]

    def _search_pip_repositories(
        self, query: str, venv_path: str
    ) -> list[dict[str, object]]:
        command = build_repository_search_command(
            self._pip_python(venv_path), query, limit=50
        )
        code, output = self.execute(command, timeout=180)
        if code != 0:
            raise RuntimeError(output or "pip repository search failed.")
        payload = parse_repository_search(output)
        rows = list(payload.get("rows") or [])
        errors = [str(value) for value in payload.get("errors") or []]
        if not rows and errors:
            raise RuntimeError("\n".join(errors))
        return [{
            "name": str(row.get("name") or ""),
            "version": "",
            "installed_version": "",
            "versions": [],
            "repo": str(row.get("repo") or ""),
            "popularity_rank": int(row.get("popularity_rank", 1000000)),
            "partial": True,
        } for row in rows if isinstance(row, dict) and row.get("name")]

    @staticmethod
    def _pip_command(venv_path: str) -> str:
        return f"{SessionPackageMixin._pip_python(venv_path)} -m pip"

    @staticmethod
    def _pip_python(venv_path: str) -> str:
        clean = str(venv_path or "")
        if (
            clean.startswith("/")
            and len(clean) <= 4096
            and not any(char in clean for char in "\r\n\0")
        ):
            python = posixpath.join(clean, "bin", "python")
            return shlex.quote(python)
        return "python3"

    @staticmethod
    def _validate_pip_package(package: str) -> None:
        if not PIP_PACKAGE_NAME_RE.fullmatch(str(package or "")):
            raise ValueError("ui.invalid_python_package_name")

    @staticmethod
    def _pip_json(output: str) -> dict[str, object]:
        start, end = output.find("{"), output.rfind("}")
        if start < 0 or end < start:
            return {}
        try:
            payload = json.loads(output[start : end + 1])
        except (TypeError, ValueError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _clean_pip_output(output: str) -> str:
        lines = []
        for raw in str(output or "").splitlines():
            line = raw.strip()
            if line.startswith(
                "WARNING: pip index is currently an experimental command."
            ):
                continue
            lines.append(raw)
        return "\n".join(lines).strip()

    @staticmethod
    def _parse_pip_index(
        package: str, output: str
    ) -> tuple[str, str, list[str], str]:
        name, latest, versions, installed = package, "", [], ""
        for raw in output.splitlines():
            line = raw.strip()
            match = re.match(r"^(.+?) \(([^()]+)\)$", line)
            if match and not latest:
                name, latest = match.group(1).strip(), match.group(2).strip()
            elif line.startswith("Available versions:"):
                values = line.partition(":")[2]
                versions = [value.strip() for value in values.split(",") if value.strip()]
            elif line.startswith("INSTALLED:"):
                installed = line.partition(":")[2].strip()
            elif line.startswith("LATEST:") and not latest:
                latest = line.partition(":")[2].strip()
        return name, latest or (versions[0] if versions else ""), versions, installed

    def tool_install_command(self, debian_name: str, rhel_name: str = "") -> str:
        try:
            adapter = self._distribution_adapter()
        except RuntimeError:
            return ""
        package = rhel_name or debian_name
        if adapter.key == "ubuntu":
            package = debian_name
        if not PACKAGE_NAME_RE.fullmatch(package):
            return ""
        return "sudo " + adapter.install_command(package).replace(" -y", "", 1)
