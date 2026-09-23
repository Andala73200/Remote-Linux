import unittest

from PySide6.QtWidgets import QApplication

from app.core.security_parsers import parse_firewall, parse_last, parse_who
from app.core.session_database import SessionDatabaseMixin
from app.widgets.backups_widget import BackupsWidget
from app.widgets.network_security_widget import NetworkSecurityWidget
from app.widgets.table_tools import Cell, FilteredTablePage


APP = QApplication.instance() or QApplication([])


class SecurityTableParserTests(unittest.TestCase):
    def test_ufw_summary_and_rules_become_structured_rows(self):
        data = parse_firewall("""Outil : UFW
Status: active
Default: deny (incoming), allow (outgoing)

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    Anywhere
22/tcp (v6)                ALLOW IN    Anywhere (v6)
""")
        self.assertEqual(data["tool"], "UFW")
        self.assertEqual(data["summary"][1], {
            "parameter": "Status", "value": "active",
        })
        self.assertEqual(data["rules"][0]["target"], "22")
        self.assertEqual(data["rules"][0]["protocol"], "TCP")
        self.assertEqual(data["rules"][1]["ip"], "IPv6")

    def test_who_and_last_are_split_into_columns(self):
        connected = parse_who(
            "andala pts/0 2026-08-27 11:23 (127.0.0.1)"
        )
        history = parse_last(
            "andala pts/0 192.168.1.91 Wed Aug 26 20:06 - 23:30 (03:23)"
        )
        iso_history = parse_last(
            "andala pts/0 127.0.0.1 2026-08-27T11:23:00+02:00 - "
            "2026-08-27T12:23:00+02:00 (01:00)"
        )
        failure_without_source = parse_last(
            "UNKNOWN tty1 Wed Aug 26 19:18 - 19:18 (00:00)", failed=True
        )
        self.assertEqual(connected[0]["source"], "127.0.0.1")
        self.assertEqual(connected[0]["date"], "2026-08-27 11:23")
        self.assertEqual(history[0]["start"], "Wed Aug 26 20:06")
        self.assertEqual(history[0]["end"], "23:30")
        self.assertEqual(history[0]["duration"], "03:23")
        self.assertEqual(iso_history[0]["start"], "2026-08-27T11:23:00+02:00")
        self.assertEqual(iso_history[0]["end"], "2026-08-27T12:23:00+02:00")
        self.assertEqual(failure_without_source[0]["source"], "Local")
        self.assertEqual(failure_without_source[0]["status"], "Échec")


class TableToolTests(unittest.TestCase):
    def test_numeric_sort_and_column_filter(self):
        page = FilteredTablePage(["Port", "Processus"], default_sort=0)
        page.fill([
            [Cell("100", 100), "service-b"],
            [Cell("22", 22), "sshd"],
        ])
        self.assertEqual(page.table.item(0, 0).text(), "22")
        page.filter_bar.editors[1].setText("SSHD")
        APP.processEvents()
        self.assertFalse(page.table.isRowHidden(0))
        self.assertTrue(page.table.isRowHidden(1))

    def test_port_favorite_key_does_not_depend_on_pid(self):
        first = {
            "protocol": "TCP", "address": "0.0.0.0", "port": "22",
            "process": "sshd", "pid": "100",
        }
        second = dict(first, pid="999")
        self.assertEqual(
            NetworkSecurityWidget._port_key(first),
            NetworkSecurityWidget._port_key(second),
        )

    def test_port_favorite_is_saved_for_the_current_server(self):
        class Storage:
            settings = {"network_port_favorites": {}}

            def save(self):
                pass

        class Profile:
            id = "server-a"

        class Session:
            profile = Profile()

        storage = Storage()
        widget = NetworkSecurityWidget(storage)
        widget.set_session(Session())
        widget.port_rows = [{
            "protocol": "TCP", "address": "0.0.0.0", "port": "22",
            "process": "sshd", "pid": "100", "scope": "Toutes interfaces",
        }]
        widget._fill_ports()
        widget._port_clicked(0, 0)
        self.assertEqual(len(storage.settings["network_port_favorites"]["server-a"]), 1)
        self.assertEqual(widget.port_table.table.item(0, 0).text(), "★")


class BackupNoStanzaTests(unittest.TestCase):
    def test_empty_pgbackrest_payload_is_not_configured(self):
        class Session(SessionDatabaseMixin):
            def execute(self, _command, timeout=10):
                return 0, "/usr/bin/pgbackrest"

            def _as_postgres(self, _command, timeout=90):
                return 0, "[]"

        result = Session().backup_status()
        self.assertFalse(result["configured"])
        self.assertEqual(result["reason"], "no_stanza")

    def test_no_stanza_disables_verify_and_clears_false_failure(self):
        class Profile:
            id = "server-1"

        class Session:
            profile = Profile()

        class Storage:
            settings = {"backup_verifications": {
                "server-1": {
                    "ok": False, "time": "2026-08-27T11:28:47",
                    "output": "Aucune stanza pgBackRest ne peut être vérifiée.",
                }
            }}

            def save(self):
                pass

        storage = Storage()
        widget = BackupsWidget(storage)
        widget.set_session(Session())
        widget._ready({
            "installed": True, "configured": False, "reason": "no_stanza",
            "stanza_names": [], "error": "Aucune stanza pgBackRest n'est configurée.",
        })
        self.assertFalse(widget.verify_button.isEnabled())
        self.assertNotIn("server-1", storage.settings["backup_verifications"])


if __name__ == "__main__":
    unittest.main()
