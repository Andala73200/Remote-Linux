from __future__ import annotations

import re
import shlex
from collections.abc import Callable


ExecuteFn = Callable[..., tuple[int, str]]
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9.+:-]+$")


def search_apt(
    execute: ExecuteFn, query: str
) -> list[dict[str, str]]:
    code, output = execute(
        "LC_ALL=C apt-cache search --names-only -- "
        f"{shlex.quote(query)} | head -n 100",
        timeout=60,
    )
    if code != 0:
        raise RuntimeError(output or "Recherche APT impossible.")
    _, installed_text = execute(
        "dpkg-query -W -f='${Package}|${Version}\\n' 2>/dev/null",
        timeout=30,
    )
    installed = _installed_map(installed_text)
    rows = []
    for line in output.splitlines():
        name, separator, summary = line.partition(" - ")
        name = name.strip()
        if not separator or not PACKAGE_NAME_RE.fullmatch(name):
            continue
        rows.append({
            "name": name, "version": installed.get(name, ""),
            "installed": "Oui" if name in installed else "Non",
            "repo": "Dépôts APT configurés", "summary": summary.strip(),
        })
    return rows


def search_dnf(
    execute: ExecuteFn, query: str
) -> list[dict[str, str]]:
    command = (
        "LC_ALL=C dnf -q repoquery --latest-limit 1 "
        "--qf '%{name}|%{evr}|%{reponame}|%{summary}' -- "
        f"{shlex.quote('*' + query + '*')} | head -n 100"
    )
    code, output = execute(command, timeout=90)
    if code != 0:
        raise RuntimeError(output or "Recherche DNF impossible.")
    _, installed_text = execute(
        "rpm -qa --qf '%{NAME}|%{VERSION}-%{RELEASE}\\n'", timeout=30
    )
    installed = _installed_map(installed_text)
    rows, seen = [], set()
    for line in output.splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4 or parts[0] in seen:
            continue
        seen.add(parts[0])
        rows.append({
            "name": parts[0], "version": parts[1], "repo": parts[2],
            "installed": "Oui" if parts[0] in installed else "Non",
            "summary": parts[3],
        })
    return rows


def _installed_map(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        name, separator, version = line.partition("|")
        if separator:
            result[name] = version
    return result


def search_yum(
    execute: ExecuteFn, query: str
) -> list[dict[str, str]]:
    command = (
        "LC_ALL=C yum -q list available "
        f"{shlex.quote('*' + query + '*')} | head -n 120"
    )
    code, output = execute(command, timeout=90)
    if code != 0:
        raise RuntimeError(output or "Recherche YUM impossible.")
    _, installed_text = execute(
        "rpm -qa --qf '%{NAME}|%{VERSION}-%{RELEASE}\\n'", timeout=30
    )
    installed = _installed_map(installed_text)
    rows, seen = [], set()
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) < 3 or parts[0] in {"Available", "Installed"}:
            continue
        package_arch = parts[0]
        if "." not in package_arch:
            continue
        name, _arch = package_arch.rsplit(".", 1)
        if not PACKAGE_NAME_RE.fullmatch(name) or name in seen:
            continue
        seen.add(name)
        rows.append({
            "name": name,
            "version": parts[1],
            "repo": parts[2],
            "installed": "Oui" if name in installed else "Non",
            "summary": "",
        })
    return rows
