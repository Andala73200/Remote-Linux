from __future__ import annotations

import datetime
import json
import shlex


class SessionDatabaseMixin:
    def _as_postgres(
        self, script: str, timeout: float = 90
    ) -> tuple[int, str]:
        if str(getattr(self.profile, "user", "")) == "postgres":
            return self.execute(script, timeout=timeout)
        if str(getattr(self.profile, "user", "")) == "root":
            code, output = self.execute(
                f"su -s /bin/sh postgres -c {shlex.quote(script)}", timeout=timeout
            )
            if code == 0:
                return code, output
        command = f"-u postgres sh -c {shlex.quote(script)}"
        code, output = self._sudo_command(
            command,
            getattr(self, "sudo_password", None),
            timeout=timeout,
            raw_sudo=True,
        )
        if code == 0:
            return code, output
        direct_code, direct_output = self.execute(script, timeout=timeout)
        return (
            (direct_code, direct_output)
            if direct_code == 0 else (code, output or direct_output)
        )

    def postgres_overview(self) -> dict[str, object]:
        code, _ = self.execute("command -v psql >/dev/null 2>&1", timeout=10)
        if code != 0:
            return {
                "installed": False,
                "install_command": self.tool_install_command(
                    "postgresql-client", "postgresql"
                ),
            }
        database_sql = """
SELECT d.datname,pg_database_size(d.datname),COALESCE(s.numbackends,0),
COALESCE(s.xact_commit,0),COALESCE(s.xact_rollback,0),COALESCE(s.blks_read,0),
COALESCE(s.blks_hit,0)
FROM pg_database d LEFT JOIN pg_stat_database s ON s.datid=d.oid
WHERE d.datallowconn AND NOT d.datistemplate ORDER BY 2 DESC;
""".strip().replace("\n", " ")
        activity_sql = """
SELECT COALESCE(datname,''),usename,state,COALESCE(client_addr::text,'local'),
COALESCE(wait_event_type,''),COALESCE(EXTRACT(EPOCH FROM (now()-query_start))::bigint,0),
left(regexp_replace(COALESCE(query,''),E'[\\n\\r]+',' ','g'),180)
FROM pg_stat_activity WHERE pid<>pg_backend_pid()
ORDER BY CASE WHEN state='active' THEN 0 ELSE 1 END,query_start NULLS LAST LIMIT 100;
""".strip().replace("\n", " ")
        script = (
            "printf '__VERSION__\\n'; psql -X -At -c 'SHOW server_version'; "
            "printf '__DATABASES__\\n'; psql -X -A -t -F '|' -c "
            f"{shlex.quote(database_sql)}; "
            "printf '__ACTIVITY__\\n'; psql -X -A -t -F '|' -c "
            f"{shlex.quote(activity_sql)}"
        )
        code, output = self._as_postgres(script, timeout=120)
        if code != 0:
            return {
                "installed": True, "accessible": False,
                "error": output or "PostgreSQL n'est pas accessible avec les droits actuels.",
            }
        sections = self._marker_sections(output)
        databases = []
        for line in sections.get("DATABASES", "").splitlines():
            parts = line.split("|", 6)
            if len(parts) != 7:
                continue
            databases.append({
                "name": parts[0], "size": self._integer(parts[1]),
                "connections": self._integer(parts[2]),
                "commits": self._integer(parts[3]),
                "rollbacks": self._integer(parts[4]),
                "blocks_read": self._integer(parts[5]),
                "blocks_hit": self._integer(parts[6]),
            })
        activity = []
        for line in sections.get("ACTIVITY", "").splitlines():
            parts = line.split("|", 6)
            if len(parts) == 7:
                activity.append(dict(zip(
                    ("database", "user", "state", "client", "wait", "seconds", "query"),
                    parts,
                )))
        return {
            "installed": True, "accessible": True,
            "version": sections.get("VERSION", "").strip(),
            "databases": databases, "activity": activity,
        }

    def postgres_tables(self, database: str) -> list[dict[str, object]]:
        if not database or any(char in database for char in "\r\n\0"):
            raise ValueError("Nom de base invalide.")
        sql = """
SELECT schemaname,relname,pg_total_relation_size(relid),n_live_tup,n_dead_tup,
seq_scan,idx_scan,COALESCE(last_autovacuum::text,''),COALESCE(last_autoanalyze::text,'')
FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 100;
""".strip().replace("\n", " ")
        command = (
            f"psql -X -A -t -F '|' -d {shlex.quote(database)} -c {shlex.quote(sql)}"
        )
        code, output = self._as_postgres(command, timeout=120)
        if code != 0:
            raise RuntimeError(output or "Lecture des tables impossible.")
        rows = []
        for line in output.splitlines():
            parts = line.split("|", 8)
            if len(parts) != 9:
                continue
            rows.append({
                "schema": parts[0], "name": parts[1],
                "size": self._integer(parts[2]), "live": self._integer(parts[3]),
                "dead": self._integer(parts[4]), "seq_scan": self._integer(parts[5]),
                "idx_scan": self._integer(parts[6]), "vacuum": parts[7],
                "analyze": parts[8],
            })
        return rows

    def backup_status(self) -> dict[str, object]:
        code, _ = self.execute("command -v pgbackrest >/dev/null 2>&1", timeout=10)
        if code != 0:
            return {
                "installed": False,
                "install_command": self.tool_install_command("pgbackrest"),
            }
        code, output = self._as_postgres(
            "pgbackrest info --output=json", timeout=120
        )
        if code != 0:
            return {
                "installed": True, "configured": False,
                "error": output or "Configuration pgBackRest introuvable.",
            }
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            return {
                "installed": True, "configured": True, "healthy": False,
                "error": f"Réponse pgBackRest invalide : {exc}",
            }
        if not isinstance(payload, list):
            return {
                "installed": True, "configured": False, "healthy": False,
                "reason": "invalid_response",
                "error": "Le format retourné par pgBackRest n'est pas reconnu.",
            }
        stanzas = payload
        if not stanzas:
            return {
                "installed": True, "configured": False, "healthy": False,
                "reason": "no_stanza", "stanzas": 0, "stanza_names": [],
                "history": [], "last": None,
                "error": (
                    "Aucune stanza pgBackRest n'est configurée.\n\n"
                    "Diagnostic : sudo -u postgres pgbackrest info\n"
                    "Création : sudo -u postgres pgbackrest --stanza=<nom> stanza-create\n"
                    "Contrôle : sudo -u postgres pgbackrest --stanza=<nom> check"
                ),
            }
        history = []
        healthy = True
        messages = []
        for stanza in stanzas:
            if not isinstance(stanza, dict):
                continue
            name = str(stanza.get("name") or "—")
            status = stanza.get("status") or {}
            if int(status.get("code") or 0) != 0:
                healthy = False
                messages.append(f"{name} : {status.get('message') or 'erreur'}")
            backups = stanza.get("backup") or []
            if not backups:
                healthy = False
                messages.append(f"{name} : aucune sauvegarde")
            for backup in backups:
                row = self._backup_row(name, backup)
                history.append(row)
                if row.get("error"):
                    healthy = False
                    messages.append(f"{name} : sauvegarde {row.get('label')} en erreur")
        history.sort(key=lambda row: int(row.get("stop_epoch") or 0), reverse=True)
        return {
            "installed": True, "configured": True, "healthy": healthy,
            "stanzas": len(stanzas),
            "stanza_names": [
                str(stanza.get("name")) for stanza in stanzas
                if isinstance(stanza, dict) and stanza.get("name")
            ],
            "history": history,
            "last": history[0] if history else None,
            "errors": "\n".join(messages),
        }

    def backup_verify(self) -> tuple[int, str]:
        status = self.backup_status()
        if not status.get("installed"):
            return 1, "pgBackRest n'est pas installé."
        if not status.get("configured"):
            return 1, str(status.get("error") or "pgBackRest n'est pas configuré.")
        names = [str(name) for name in status.get("stanza_names") or [] if name]
        if not names:
            return 1, "Aucune stanza pgBackRest ne peut être vérifiée."
        overall_code = 0
        outputs = []
        for name in names:
            option = shlex.quote(f"--stanza={name}")
            code, output = self._as_postgres(
                f"pgbackrest {option} --output=text --verbose verify",
                timeout=1800,
            )
            overall_code = overall_code or code
            outputs.append(f"[{name}]\n{output or ('OK' if code == 0 else 'Échec')}")
        return overall_code, "\n\n".join(outputs)

    @classmethod
    def _backup_row(cls, stanza: str, backup: object) -> dict[str, object]:
        data = backup if isinstance(backup, dict) else {}
        timestamp = data.get("timestamp") or {}
        info = data.get("info") or {}
        repository = info.get("repository") or {}
        stop = cls._integer(timestamp.get("stop"))
        return {
            "stanza": stanza, "label": str(data.get("label") or ""),
            "type": str(data.get("type") or ""),
            "start_epoch": cls._integer(timestamp.get("start")),
            "stop_epoch": stop, "date": cls._date(stop),
            "database_size": cls._integer(info.get("size")),
            "repository_size": cls._integer(repository.get("size")),
            "error": bool(data.get("error", False)),
        }

    @staticmethod
    def _marker_sections(text: str) -> dict[str, str]:
        sections: dict[str, list[str]] = {}
        current = ""
        for line in text.splitlines():
            if line.startswith("__") and line.endswith("__"):
                current = line.strip("_")
                sections.setdefault(current, [])
            elif current:
                sections[current].append(line)
        return {key: "\n".join(value) for key, value in sections.items()}

    @staticmethod
    def _integer(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _date(epoch: int) -> str:
        if not epoch:
            return "—"
        return datetime.datetime.fromtimestamp(
            epoch, datetime.timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")
