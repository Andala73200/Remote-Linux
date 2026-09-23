from __future__ import annotations

import os
import shlex

from app.core.cron_schedule import next_cron_time


class SessionTasksMixin:
    def scheduled_tasks(self) -> dict[str, object]:
        ssh_user = shlex.quote(str(getattr(self.profile, "user", "")))
        script = rf'''
printf '__NOW__\n'; date +%s
printf '__TIMEZONE__\n'
timedatectl show --property=Timezone --value 2>/dev/null || printf 'UTC\n'
printf '__TIMERS__\n'
for timer in $(
  {{ systemctl list-unit-files --type=timer --no-legend --no-pager 2>/dev/null | awk '{{print $1}}';
     systemctl list-timers --all --no-legend --no-pager 2>/dev/null | awk '{{print $(NF-1)}}'; }} |
  sed '/^$/d' | sort -u
); do
  printf '__TIMER__%s\n' "$timer"
  systemctl show "$timer" --no-pager --property=Id,ActiveState,SubState,NextElapseUSecRealtime,LastTriggerUSec,Unit 2>/dev/null
  unit=$(systemctl show "$timer" --property=Unit --value 2>/dev/null)
  [ -n "$unit" ] && systemctl show "$unit" --no-pager --property=Result,ExecMainStatus 2>/dev/null
done
printf '__CRON__\n'
for file in /etc/crontab /etc/cron.d/* /var/spool/cron/crontabs/* /var/spool/cron/*; do
  [ -f "$file" ] || continue
  printf '__CRON_FILE__%s\n' "$file"
  sed -n '1,400p' "$file" 2>/dev/null || true
done
if command -v crontab >/dev/null 2>&1; then
  printf '__CRON_USER__%s\n' {ssh_user}
  if [ "$(id -un)" = {ssh_user} ]; then
    crontab -l 2>/dev/null || true
  else
    crontab -u {ssh_user} -l 2>/dev/null || true
  fi
fi
for period in hourly daily weekly monthly; do
  directory="/etc/cron.$period"
  [ -d "$directory" ] || continue
  for file in "$directory"/*; do
    [ -f "$file" ] || continue
    printf '__CRON_PERIOD__%s|%s\n' "$period" "$file"
  done
done
printf '__CRON_LOG__\n'
journalctl -t CRON -n 250 --no-pager -o short-iso 2>/dev/null || journalctl -u cron -u crond -n 250 --no-pager -o short-iso 2>/dev/null || true
'''.strip()
        _, output, privileged = self._diagnostic_execute(script, timeout=150)
        now, timezone, timers, cron_lines, cron_log = self._split_output(output)
        timer_rows = self._parse_timers(timers)
        cron_rows = self._parse_cron(cron_lines, now, timezone, cron_log)
        return {
            "privileged": privileged, "timers": timer_rows,
            "cron": cron_rows, "cron_log": cron_log,
        }

    @staticmethod
    def _split_output(text: str) -> tuple[int, str, str, list[tuple[str, str, str]], str]:
        now, timezone, section = 0, "UTC", ""
        timer_lines: list[str] = []
        cron_lines: list[tuple[str, str, str]] = []
        cron_log: list[str] = []
        source = user = ""
        for line in text.splitlines():
            if line == "__NOW__": section = "now"; continue
            if line == "__TIMEZONE__": section = "timezone"; continue
            if line == "__TIMERS__": section = "timers"; continue
            if line == "__CRON__": section = "cron"; continue
            if line == "__CRON_LOG__": section = "cron_log"; continue
            if line.startswith("__CRON_FILE__"):
                section, source, user = "cron", line[13:], ""
                continue
            if line.startswith("__CRON_USER__"):
                section, source, user = "cron", "crontab utilisateur", line[13:]
                continue
            if line.startswith("__CRON_PERIOD__"):
                period, _, path = line[15:].partition("|")
                cron_lines.append((f"/etc/cron.{period}", "root", f"@{period} {path}"))
                continue
            if section == "now":
                try: now = int(line.strip())
                except ValueError: pass
            elif section == "timezone" and line.strip(): timezone = line.strip()
            elif section == "timers": timer_lines.append(line)
            elif section == "cron": cron_lines.append((source, user, line))
            elif section == "cron_log": cron_log.append(line)
        return now, timezone, "\n".join(timer_lines), cron_lines, "\n".join(cron_log)

    @staticmethod
    def _parse_timers(text: str) -> list[dict[str, str]]:
        rows, current = [], None
        for line in text.splitlines():
            if line.startswith("__TIMER__"):
                if current:
                    rows.append(current)
                current = {"name": line[9:], "type": "systemd"}
                continue
            if current is not None and "=" in line:
                key, value = line.split("=", 1)
                mapping = {
                    "NextElapseUSecRealtime": "next", "LastTriggerUSec": "last",
                    "Unit": "command", "Result": "result", "ExecMainStatus": "code",
                    "ActiveState": "active", "SubState": "sub",
                }
                if key in mapping:
                    current[mapping[key]] = value or "—"
        if current:
            rows.append(current)
        for row in rows:
            if not row.get("result"):
                row["result"] = "Jamais exécuté" if row.get("last") in {"", "—", "n/a"} else "—"
            if row.get("result") == "success" and row.get("code") not in {"", "0", None}:
                row["result"] = f"code {row['code']}"
        return rows

    @classmethod
    def _parse_cron(
        cls, entries: list[tuple[str, str, str]], now: int, timezone: str, log: str
    ) -> list[dict[str, str]]:
        rows, seen = [], set()
        for source, hinted_user, raw in entries:
            line = raw.strip()
            if not line or line.startswith("#") or "=" in line.split()[0]:
                continue
            fields = line.split()
            system_file = source == "/etc/crontab" or source.startswith("/etc/cron.d/")
            if line.startswith("@"):
                needed = 3 if system_file else 2
                if len(fields) < needed: continue
                schedule = fields[0]
                user = fields[1] if system_file else hinted_user or os.path.basename(source)
                command = " ".join(fields[2:] if system_file else fields[1:])
            else:
                needed = 7 if system_file else 6
                if len(fields) < needed: continue
                schedule = " ".join(fields[:5])
                user = fields[5] if system_file else hinted_user or os.path.basename(source)
                command = " ".join(fields[6:] if system_file else fields[5:])
            recent = cls._last_cron_log(command, log)
            signature = (user, schedule, command)
            if signature in seen:
                continue
            seen.add(signature)
            rows.append({
                "type": "cron", "name": source or "crontab", "user": user,
                "schedule": schedule, "last": recent,
                "next": next_cron_time(schedule, now, timezone) if now else "Non calculable",
                "result": "Non fourni par cron", "command": command,
            })
        return rows

    @staticmethod
    def _last_cron_log(command: str, log: str) -> str:
        needle = command[:100]
        for line in reversed(log.splitlines()):
            if needle and needle in line:
                return line[:25]
        return "—"
