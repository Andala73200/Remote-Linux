import datetime
import base64
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.core.cron_schedule import next_cron_time
from app.core.distribution import parse_os_release
from app.core.pip_repository_search import (
    RESULT_MARKER, build_repository_search_command, parse_repository_search,
)
from app.core.session_database import SessionDatabaseMixin
from app.core.session_packages import SessionPackageMixin
from app.core.session_security import SessionSecurityMixin
from app.core.session_tasks import SessionTasksMixin
from app.core.smart_probe import SmartProbeMixin
from app.core.session_ssh_keys import SessionSSHKeysMixin
from app.core.terminal_shell_state import ShellCommandTracker


class DistributionTests(unittest.TestCase):
    def test_detects_debian_and_rhel_families(self):
        debian = parse_os_release(
            'ID=debian\nPRETTY_NAME="Debian GNU/Linux 13"\n'
        )
        rhel = parse_os_release(
            'ID=rocky\nID_LIKE="rhel centos fedora"\nPRETTY_NAME="Rocky Linux 10"\n'
        )
        self.assertEqual(debian["family"], "ubuntu")
        self.assertEqual(rhel["family"], "redhat")
        self.assertEqual(rhel["name"], "Rocky Linux 10")


class TerminalIntegrationTests(unittest.TestCase):
    def test_only_exact_manager_command_is_intercepted_at_prompt(self):
        tracker = ShellCommandTracker()
        tracker.prompt(0)
        for value in (b"a", b"p", b"t"):
            payload, manager, _ = tracker.input(value, {"apt"})
            self.assertEqual(payload, value)
            self.assertIsNone(manager)
        payload, manager, command = tracker.input(b"\r", {"apt"})
        self.assertEqual(payload, b"\x15")
        self.assertEqual(manager, "apt")
        self.assertIsNone(command)

    def test_manager_with_arguments_stays_in_terminal(self):
        tracker = ShellCommandTracker()
        tracker.prompt(0)
        for value in b"apt update":
            tracker.input(bytes([value]), {"apt"})
        payload, manager, command = tracker.input(b"\r", {"apt"})
        self.assertEqual(payload, b"\r")
        self.assertIsNone(manager)
        self.assertEqual(command, "apt update")
        self.assertEqual(tracker.prompt(100), ("apt update", 100))

    def test_manager_is_not_intercepted_inside_an_interactive_program(self):
        tracker = ShellCommandTracker()
        payload, manager, _ = tracker.input(b"apt\r", {"apt"})
        self.assertEqual(payload, b"apt\r")
        self.assertIsNone(manager)

    def test_only_exact_pip_command_is_intercepted(self):
        tracker = ShellCommandTracker()
        tracker.prompt(0)
        for value in b"pip":
            tracker.input(bytes([value]), {"pip"})
        payload, manager, command = tracker.input(b"\r", {"pip"})
        self.assertEqual((payload, manager, command), (b"\x15", "pip", None))

    def test_pip_with_arguments_stays_in_terminal(self):
        tracker = ShellCommandTracker()
        tracker.prompt(0)
        for value in b"pip install django":
            tracker.input(bytes([value]), {"pip"})
        payload, manager, command = tracker.input(b"\r", {"pip"})
        self.assertEqual(payload, b"\r")
        self.assertIsNone(manager)
        self.assertEqual(command, "pip install django")


class PipPackageTests(unittest.TestCase):
    class Session(SessionPackageMixin):
        def __init__(self, responses):
            self.responses = list(responses)
            self.commands = []

        def execute(self, command, timeout=40):
            self.commands.append((command, timeout))
            return self.responses.pop(0)

    def test_pip_search_uses_the_active_venv_and_configured_indexes(self):
        session = self.Session([
            (0, json.dumps({
                "name": "django", "versions": ["5.2.1", "5.2"],
                "latest": "5.2.1",
            })),
            (0, "Name: Django\nVersion: 5.2\n"),
        ])

        result = session.package_search("django", "pip", "/srv/app/.venv")

        self.assertEqual(result["rows"][0]["installed_version"], "5.2")
        self.assertEqual(result["rows"][0]["version"], "5.2.1")
        self.assertIn("/srv/app/.venv/bin/python", session.commands[0][0])
        self.assertIn("index versions --json django", session.commands[0][0])

    def test_pip_install_preview_never_uses_sudo(self):
        session = self.Session([])
        command = session.package_install_preview(
            "django-rest-framework", "pip", "/srv/my app/.venv"
        )
        self.assertNotIn("sudo", command)
        self.assertIn("'/srv/my app/.venv/bin/python' -m pip", command)
        self.assertIn("install --upgrade django-rest-framework", command)

    def test_project_venv_path_is_shell_quoted(self):
        session = self.Session([(0, "")])
        candidate = session.project_venv("/srv/client's app")
        self.assertEqual(candidate, "/srv/client's app/.venv")
        self.assertIn("'\"'\"'", session.commands[0][0])

    def test_project_venv_at_root_keeps_an_absolute_path(self):
        session = self.Session([(0, "")])
        self.assertEqual(session.project_venv("/"), "/.venv")
        self.assertIn("/.venv/bin/activate", session.commands[0][0])

    def test_pip_rejects_shell_syntax_and_relative_venv_paths(self):
        session = self.Session([])
        with self.assertRaises(ValueError):
            session.package_install_preview("django;id", "pip", "/srv/app/.venv")
        command = session.package_install_preview("django", "pip", "relative/.venv")
        self.assertEqual(command, "python3 -m pip install --upgrade django")

    def test_pip_not_found_is_an_empty_result_without_warning(self):
        warning = (
            "WARNING: pip index is currently an experimental command. "
            "It may be removed/changed in a future release without prior warning.\n"
        )
        session = self.Session([
            (2, "no such option: --json"),
            (1, warning + "ERROR: No matching distribution found for open"),
            (0, RESULT_MARKER + "\n" + json.dumps({"rows": [], "errors": []})),
        ])

        result = session.package_search("open", "pip")

        self.assertEqual(result["rows"], [])
        self.assertEqual(len(session.commands), 3)

    def test_partial_pip_name_returns_repository_candidates(self):
        session = self.Session([
            (2, "no such option: --json"),
            (1, "ERROR: No matching distribution found for open"),
            (0, RESULT_MARKER + "\n" + json.dumps({
                "rows": [
                    {"name": "openai", "repo": "pypi.org"},
                    {"name": "openpyxl", "repo": "pypi.org"},
                ],
                "errors": [],
            })),
        ])

        rows = session.package_search("open", "pip")["rows"]

        self.assertEqual([row["name"] for row in rows], ["openai", "openpyxl"])
        self.assertTrue(all(row["partial"] for row in rows))
        self.assertIn("<remote-linux-pip-search>", session.commands[2][0])

    def test_pip_experimental_warning_is_removed_from_real_errors(self):
        warning = (
            "WARNING: pip index is currently an experimental command. "
            "It may be removed/changed in a future release without prior warning.\n"
        )
        session = self.Session([
            (2, "no such option: --json"),
            (1, warning + "ERROR: Network is unreachable"),
        ])

        with self.assertRaisesRegex(RuntimeError, "Network is unreachable") as raised:
            session.package_search("django", "pip")
        self.assertNotIn("experimental command", str(raised.exception))


class PipRepositorySearchTests(unittest.TestCase):
    def test_remote_search_reads_and_ranks_a_simple_repository(self):
        root = Path(tempfile.mkdtemp())
        (root / "index.html").write_text(
            "<html><body>"
            "<a href='openpyxl/'>openpyxl</a>"
            "<a href='something-open/'>something-open</a>"
            "<a href='openai/'>openai</a>"
            "<a href='unrelated/'>unrelated</a>"
            "</body></html>",
            encoding="utf-8",
        )
        command = build_repository_search_command("python3", "open", 20)
        environment = dict(os.environ)
        environment["PIP_INDEX_URL"] = root.as_uri() + "/"
        environment.pop("PIP_EXTRA_INDEX_URL", None)

        result = subprocess.run(
            command,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=20,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = parse_repository_search(result.stdout)["rows"]
        self.assertEqual(
            [row["name"] for row in rows],
            ["openai", "openpyxl", "something-open"],
        )

        (root / "index.html").write_text(
            "<html><body><a href='unrelated/'>unrelated</a></body></html>",
            encoding="utf-8",
        )
        cached = subprocess.run(
            command, shell=True, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=environment, timeout=20, check=False,
        )
        self.assertEqual(
            [row["name"] for row in parse_repository_search(cached.stdout)["rows"]],
            ["openai", "openpyxl", "something-open"],
        )


class CronTests(unittest.TestCase):
    def test_next_daily_execution_uses_server_timezone(self):
        now = int(datetime.datetime(
            2026, 8, 27, 5, 30, tzinfo=datetime.timezone.utc
        ).timestamp())
        self.assertEqual(
            next_cron_time("0 8 * * *", now, "Europe/Paris"),
            "2026-08-27 08:00",
        )

    def test_systemd_timer_properties_are_parsed(self):
        text = """__TIMER__backup.timer
ActiveState=active
SubState=waiting
NextElapseUSecRealtime=Thu 2026-08-27 09:00:00 CEST
LastTriggerUSec=Thu 2026-08-27 08:00:00 CEST
Unit=backup.service
Result=success
ExecMainStatus=0"""
        rows = SessionTasksMixin._parse_timers(text)
        self.assertEqual(rows[0]["command"], "backup.service")
        self.assertEqual(rows[0]["result"], "success")

    def test_duplicate_user_crontab_entries_are_collapsed(self):
        entries = [
            ("/var/spool/cron/crontabs/alice", "", "0 8 * * * backup-db"),
            ("crontab utilisateur", "alice", "0 8 * * * backup-db"),
        ]
        rows = SessionTasksMixin._parse_cron(entries, 1787814000, "UTC", "")
        self.assertEqual(len(rows), 1)


class SecurityParserTests(unittest.TestCase):
    def test_listening_port_keeps_process_and_scope(self):
        line = (
            'tcp LISTEN 0 4096 0.0.0.0:22 0.0.0.0:* '
            'users:(("sshd",pid=812,fd=3))'
        )
        row = SessionSecurityMixin._parse_port(line)
        self.assertEqual(row["port"], "22")
        self.assertEqual(row["process"], "sshd")
        self.assertEqual(row["scope"], "Toutes interfaces")


class BackupAndSmartParserTests(unittest.TestCase):
    def test_pgbackrest_backup_row_reports_sizes_and_date(self):
        row = SessionDatabaseMixin._backup_row("main", {
            "label": "20260827-010000F", "type": "full",
            "timestamp": {"start": 1787792400, "stop": 1787792460},
            "info": {"size": 1000, "repository": {"size": 400}},
            "error": False,
        })
        self.assertEqual(row["stanza"], "main")
        self.assertEqual(row["database_size"], 1000)
        self.assertEqual(row["repository_size"], 400)

    def test_pgbackrest_verifies_each_configured_stanza(self):
        class Session(SessionDatabaseMixin):
            def __init__(self):
                self.commands = []

            def backup_status(self):
                return {
                    "installed": True, "configured": True,
                    "stanza_names": ["main", "reporting"],
                }

            def _as_postgres(self, command, timeout=90):
                self.commands.append((command, timeout))
                return 0, "verified"

        session = Session()
        code, output = session.backup_verify()
        self.assertEqual(code, 0)
        self.assertEqual(len(session.commands), 2)
        self.assertIn("--stanza=main", session.commands[0][0])
        self.assertIn("--stanza=reporting", session.commands[1][0])
        self.assertIn("[main]", output)

    def test_nvme_smart_json_exposes_health_wear_and_errors(self):
        payload = {
            "device": {"name": "/dev/nvme0"}, "model_name": "Example NVMe",
            "serial_number": "ABC", "smart_status": {"passed": True},
            "temperature": {"current": 41},
            "endurance_used": {"current_percent": 7},
            "power_on_time": {"hours": 1234},
            "nvme_smart_health_information_log": {"media_errors": 0},
            "smartctl": {"messages": []},
        }
        text = "__SMART_DEVICE__/dev/nvme0\n" + json.dumps(payload) + "\n__SMART_END__"
        rows = SmartProbeMixin._parse_smart_blocks(text)
        self.assertTrue(rows[0]["passed"])
        self.assertEqual(rows[0]["wear"], 7)
        self.assertEqual(rows[0]["errors"], 0)

    def test_authorized_key_parser_computes_a_sha256_fingerprint(self):
        algorithm = b"ssh-ed25519"
        blob = len(algorithm).to_bytes(4, "big") + algorithm + b"x" * 32
        encoded = base64.b64encode(blob).decode()
        row = SessionSSHKeysMixin._parse_public_key(
            f"ssh-ed25519 {encoded} poste-andala", require_plain=True
        )
        self.assertEqual(row["type"], "ssh-ed25519")
        self.assertTrue(row["fingerprint"].startswith("SHA256:"))
        self.assertEqual(row["comment"], "poste-andala")


if __name__ == "__main__":
    unittest.main()
