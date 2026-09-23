from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.config import KNOWN_HOSTS_FILE
from app.models import ConnectionProfile


def connection_hosts(profile: ConnectionProfile) -> set[tuple[str, int]]:
    """Return SSH endpoints whose trust entries belong to a profile."""
    endpoints: set[tuple[str, int]] = set()
    if profile.kind == "cloudflare":
        host = str(profile.cloudflare_host or "").strip()
        if host:
            endpoints.add((host, 22))
    else:
        host = str(profile.host or "").strip()
        if host:
            endpoints.add((host, int(profile.port or 22)))
    return endpoints


def known_host_aliases(profile: ConnectionProfile) -> set[str]:
    aliases: set[str] = set()
    for host, port in connection_hosts(profile):
        aliases.add(host.casefold())
        aliases.add(f"[{host}]:{port}".casefold())
        aliases.add(f"[{host}]:22".casefold())
    return aliases


def purge_known_hosts(
    profile: ConnectionProfile,
    path: Path = KNOWN_HOSTS_FILE,
) -> int:
    """Remove app-owned known_hosts entries for a connection profile."""
    aliases = known_host_aliases(profile)
    if not aliases or not path.exists():
        return 0

    original = path.read_text(encoding="utf-8", errors="replace")
    kept_lines: list[str] = []
    removed = 0

    for line in original.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept_lines.append(line)
            continue

        first, separator, remainder = line.partition(" ")
        if not separator:
            first, separator, remainder = line.partition("\t")
        if not separator:
            kept_lines.append(line)
            continue

        names = first.split(",")
        kept_names = [name for name in names if name.casefold() not in aliases]
        removed += len(names) - len(kept_names)
        if not kept_names:
            continue
        kept_lines.append(",".join(kept_names) + separator + remainder)

    if not removed:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write("".join(kept_lines))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return removed
