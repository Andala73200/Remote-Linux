from __future__ import annotations

import re


WEEKDAYS = "Mon|Tue|Wed|Thu|Fri|Sat|Sun"
LAST_DATE_RE = re.compile(
    rf"(?:\b(?:{WEEKDAYS})\b|\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}})"
)


def parse_firewall(text: str) -> dict[str, object]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    tool = "Inconnu"
    if lines and lines[0].lower().startswith("outil :"):
        tool = lines.pop(0).partition(":")[2].strip()
    summary: list[dict[str, str]] = []
    rules: list[dict[str, str]] = []
    if tool.upper() == "UFW":
        _parse_ufw(lines, summary, rules)
    elif tool.lower() == "firewalld":
        _parse_firewalld(lines, summary, rules)
    else:
        for index, line in enumerate(lines, start=1):
            rules.append({
                "target": f"Règle {index}", "protocol": "—", "action": line,
                "source": "—", "ip": "Toutes",
            })
    if not summary:
        summary.append({"parameter": "Outil", "value": tool})
    else:
        summary.insert(0, {"parameter": "Outil", "value": tool})
    return {"tool": tool, "summary": summary, "rules": rules}


def _parse_ufw(lines, summary, rules) -> None:
    in_rules = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("To") and "Action" in stripped and "From" in stripped:
            in_rules = True
            continue
        if set(stripped) <= {"-", " "}:
            continue
        if not in_rules and ":" in stripped:
            key, value = stripped.split(":", 1)
            summary.append({"parameter": key.strip(), "value": value.strip()})
            continue
        if not in_rules:
            continue
        parts = re.split(r"\s{2,}", stripped, maxsplit=2)
        if len(parts) != 3:
            continue
        target, action, source = parts
        ipv6 = "(v6)" in target or "(v6)" in source
        target = target.replace("(v6)", "").strip()
        source = source.replace("(v6)", "").strip()
        profile_match = re.search(r"\s+\(([^)]+)\)$", target)
        profile = profile_match.group(1) if profile_match else ""
        if profile_match:
            target = target[:profile_match.start()].strip()
        service, separator, protocol = target.rpartition("/")
        rules.append({
            "target": (
                (service if separator else target) + (f" ({profile})" if profile else "")
            ),
            "protocol": protocol.upper() if separator else "Service",
            "action": action, "source": source,
            "ip": "IPv6" if ipv6 else "IPv4",
        })


def _parse_firewalld(lines, summary, rules) -> None:
    zone = "Zone active"
    for line in lines:
        stripped = line.strip()
        if stripped in {"running", "not running"}:
            summary.append({"parameter": "État", "value": stripped})
            continue
        if stripped.endswith("(active)"):
            zone = stripped.split()[0]
            summary.append({"parameter": "Zone active", "value": zone})
            continue
        if ":" not in stripped:
            continue
        key, value = (part.strip() for part in stripped.split(":", 1))
        if key in {"ports", "services"}:
            for target in value.split():
                service, separator, protocol = target.rpartition("/")
                rules.append({
                    "target": service if separator else target,
                    "protocol": protocol.upper() if separator else "Service",
                    "action": "Autorisé", "source": zone, "ip": "Toutes",
                })
        else:
            summary.append({"parameter": key, "value": value or "—"})


def parse_who(text: str) -> list[dict[str, str]]:
    rows = []
    pattern = re.compile(
        r"^(\S+)\s+(\S+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})"
        r"(?:\s+\((.*)\))?$"
    )
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            user, terminal, date, hour, source = match.groups()
            rows.append({
                "user": user, "terminal": terminal, "date": f"{date} {hour}",
                "source": source or "Local",
            })
    return rows


def parse_last(text: str, failed: bool = False) -> list[dict[str, str]]:
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("wtmp begins", "btmp begins")):
            continue
        fields = line.split()
        if len(fields) < 4:
            continue
        user, terminal = fields[:2]
        if LAST_DATE_RE.match(fields[2]):
            source = "Local"
            remainder = " ".join(fields[2:])
        else:
            source = fields[2]
            remainder = " ".join(fields[3:])
        date_match = LAST_DATE_RE.search(remainder)
        if not date_match:
            continue
        if date_match.start():
            source = (source + " " + remainder[:date_match.start()].strip()).strip()
        details = remainder[date_match.start():]
        duration_match = re.search(r"\(([^)]*)\)\s*$", details)
        duration = duration_match.group(1) if duration_match else "—"
        if duration_match:
            details = details[:duration_match.start()].rstrip()
        status = "Échec" if failed else "Terminée"
        for marker, label in (
            ("still logged in", "Toujours connecté"),
            ("still running", "Toujours actif"),
            ("gone - no logout", "Déconnexion inconnue"),
            ("down", "Arrêt système"), ("crash", "Incident système"),
        ):
            if details.endswith(marker):
                details = details[:-len(marker)].rstrip()
                status = label if not failed else "Échec"
                break
        start, separator, end = details.partition(" - ")
        rows.append({
            "user": user, "terminal": terminal, "source": source,
            "start": start.strip(), "end": end.strip() if separator else "—",
            "duration": duration, "status": status,
        })
    return rows


def parse_key_values(text: str) -> list[dict[str, str]]:
    rows = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("|`- ")
        if not stripped:
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
        else:
            key, _, value = stripped.partition(" ")
        rows.append({"parameter": key.strip(), "value": value.strip() or "—"})
    return rows
