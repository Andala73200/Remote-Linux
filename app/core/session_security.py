from __future__ import annotations

import re

from app.core.security_parsers import (
    parse_firewall, parse_key_values, parse_last, parse_who,
)


PORT_PROCESS_RE = re.compile(r'users:\(\(\"([^\"]+)\",pid=(\d+)')


class SessionSecurityMixin:
    def _diagnostic_execute(
        self, script: str, timeout: float = 90
    ) -> tuple[int, str, bool]:
        code, output = self.sudo_execute(
            script, getattr(self, "sudo_password", None), timeout=timeout
        )
        if code == 0:
            return code, output, True
        fallback_code, fallback = self.execute(script, timeout=timeout)
        return fallback_code, fallback, False

    def identity_info(self) -> dict[str, object]:
        code, output = self.execute(
            "printf '%s|%s|%s' \"$(id -u)\" \"$(id -un)\" \"$(id -Gn)\"",
            timeout=15,
        )
        if code != 0:
            raise RuntimeError(output or "Identité distante indisponible.")
        uid_text, _, tail = output.partition("|")
        user, _, groups_text = tail.partition("|")
        try:
            uid = int(uid_text)
        except ValueError:
            uid = -1
        groups = groups_text.split()
        if uid == 0:
            level = "root"
        else:
            sudo_code, _ = self._sudo_command(
                "true", getattr(self, "sudo_password", None), timeout=15
            )
            level = "sudo" if sudo_code == 0 or {"sudo", "wheel"} & set(groups) else "user"
        return {"uid": uid, "user": user, "groups": groups, "level": level}

    def network_security(self) -> dict[str, object]:
        script = r'''
printf '__PORTS__\n'
ss -H -lntup 2>&1 || true
printf '__FIREWALL__\n'
if command -v ufw >/dev/null 2>&1; then
  printf 'Outil : UFW\n'; ufw status verbose 2>&1 || true
elif command -v firewall-cmd >/dev/null 2>&1; then
  printf 'Outil : firewalld\n'; firewall-cmd --state 2>&1 || true
  firewall-cmd --list-all 2>&1 || true
elif command -v nft >/dev/null 2>&1; then
  printf 'Outil : nftables\n'; nft list ruleset 2>&1 || true
elif command -v iptables >/dev/null 2>&1; then
  printf 'Outil : iptables\n'; iptables -S 2>&1 || true
else
  printf 'Aucun outil de pare-feu reconnu.\n'
fi
printf '__WHO__\n'; who 2>&1 || true
printf '__LAST__\n'; last -n 50 -w --time-format iso 2>&1 || last -n 50 -F -w 2>&1 || true
printf '__LASTB__\n'; lastb -n 50 -w --time-format iso 2>&1 || lastb -n 50 -F -w 2>&1 || true
printf '__SSHD__\n'
if command -v sshd >/dev/null 2>&1; then
  sshd -T 2>/dev/null | grep -E '^(permitrootlogin|passwordauthentication|pubkeyauthentication|maxauthtries|allowusers|allowgroups) ' || true
else
  printf 'Configuration effective sshd indisponible.\n'
fi
printf '__FAIL2BAN__\n'
if command -v fail2ban-client >/dev/null 2>&1; then
  fail2ban-client status 2>&1 || true
else
  printf 'Fail2ban non installé.\n'
fi
'''.strip()
        _, output, privileged = self._diagnostic_execute(script, timeout=120)
        sections = self._sections(output)
        ports = [
            row for line in sections.get("PORTS", "").splitlines()
            if (row := self._parse_port(line))
        ]
        return {
            "privileged": privileged, "ports": ports,
            "firewall": parse_firewall(sections.get("FIREWALL", "")),
            "connected": parse_who(sections.get("WHO", "")),
            "recent": parse_last(sections.get("LAST", "")),
            "failed": parse_last(sections.get("LASTB", ""), failed=True),
            "sshd": parse_key_values(sections.get("SSHD", "")),
            "fail2ban": parse_key_values(sections.get("FAIL2BAN", "")),
        }

    def linux_accounts(self) -> dict[str, object]:
        script = r'''
printf '__LIMITS__\n'
awk '$1=="UID_MIN"{u=$2}$1=="GID_MIN"{g=$2}END{print (u?u:1000)"|"(g?g:1000)}' /etc/login.defs 2>/dev/null
printf '__PASSWD__\n'; getent passwd
printf '__GROUP__\n'; getent group
'''.strip()
        code, output = self.execute(script, timeout=45)
        if code != 0:
            raise RuntimeError(output or "Lecture des comptes impossible.")
        sections = self._sections(output)
        limits = sections.get("LIMITS", "1000|1000").strip().split("|", 1)
        try:
            uid_min, gid_min = int(limits[0]), int(limits[1])
        except (ValueError, IndexError):
            uid_min = gid_min = 1000
        groups = []
        group_by_gid = {}
        explicit: dict[str, set[str]] = {}
        for line in sections.get("GROUP", "").splitlines():
            parts = line.split(":", 3)
            if len(parts) != 4:
                continue
            try:
                gid = int(parts[2])
            except ValueError:
                continue
            members = [item for item in parts[3].split(",") if item]
            groups.append({
                "name": parts[0], "gid": gid, "members": members,
                "system": parts[0] != "root" and gid < gid_min,
            })
            group_by_gid[gid] = parts[0]
            for member in members:
                explicit.setdefault(member, set()).add(parts[0])
        users = []
        connected_user = str(getattr(self.profile, "user", ""))
        for line in sections.get("PASSWD", "").splitlines():
            parts = line.split(":", 6)
            if len(parts) != 7:
                continue
            try:
                uid, gid = int(parts[2]), int(parts[3])
            except ValueError:
                continue
            membership = set(explicit.get(parts[0], set()))
            if gid in group_by_gid:
                membership.add(group_by_gid[gid])
            shell = parts[6]
            system = parts[0] not in {"root", connected_user} and (
                uid < uid_min or shell.endswith(("/nologin", "/false"))
            )
            users.append({
                "name": parts[0], "uid": uid, "gid": gid, "home": parts[5],
                "shell": shell, "groups": sorted(membership), "system": system,
            })
        return {"users": users, "groups": groups, "uid_min": uid_min}

    @staticmethod
    def _parse_port(line: str) -> dict[str, str] | None:
        parts = line.split()
        if len(parts) < 5 or parts[0] not in {"tcp", "udp"}:
            return None
        local = parts[4]
        address, separator, port = local.rpartition(":")
        if not separator:
            address, port = local, ""
        address = address.strip("[]")
        match = PORT_PROCESS_RE.search(line)
        process, pid = (match.group(1), match.group(2)) if match else ("—", "—")
        if address in {"*", "0.0.0.0", "::"}:
            scope = "Toutes interfaces"
        elif address.startswith(("127.", "::1")):
            scope = "Boucle locale"
        else:
            scope = "Interface précise"
        return {
            "protocol": parts[0].upper(), "address": address or "*", "port": port,
            "process": process, "pid": pid, "scope": scope,
        }

    @staticmethod
    def _sections(text: str) -> dict[str, str]:
        result: dict[str, list[str]] = {}
        current = ""
        for line in text.splitlines():
            if line.startswith("__") and line.endswith("__"):
                current = line.strip("_")
                result.setdefault(current, [])
            elif current:
                result[current].append(line)
        return {key: "\n".join(lines).strip() for key, lines in result.items()}
