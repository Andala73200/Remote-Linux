import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QApplication

from app.widgets.terminal_tabs import TerminalTabs
from app.widgets.terminal_widget import TerminalWidget
from app.widgets.vt_terminal import VtTerminal
from app.widgets.vt_model import VtScreen
from app.widgets.vt_parser import VtParser


APP = QApplication.instance() or QApplication([])


class TerminalInputTests(unittest.TestCase):
    def setUp(self):
        self.terminal = VtTerminal()
        self.terminal.set_input_enabled(True)
        self.entered = []
        self.terminal.data_entered.connect(self.entered.append)

    def tearDown(self):
        self.terminal.close()

    def test_tab_is_sent_to_the_remote_shell(self):
        event = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_Tab,
            Qt.NoModifier,
            "\t",
        )
        self.assertTrue(self.terminal.event(event))
        self.assertEqual(self.entered, [b"\t"])

    def test_altgr_printable_character_keeps_its_utf8_value(self):
        event = QKeyEvent(
            QEvent.KeyPress,
            Qt.Key_E,
            Qt.ControlModifier | Qt.AltModifier,
            "€",
        )
        self.assertEqual(self.terminal._key_sequence(event), "€".encode("utf-8"))

    def test_input_method_commits_unicode(self):
        event = QInputMethodEvent()
        event.setCommitString("é漢")
        self.terminal.inputMethodEvent(event)
        self.assertEqual(self.entered, ["é漢".encode("utf-8")])

    def test_virtual_environment_marker_is_not_printed(self):
        environments = []
        self.terminal.virtual_env_changed.connect(environments.append)
        self.terminal.feed_text(
            "\x1b]777;ANDALA_VENV=/srv/demo/.venv\x07"
        )
        self.assertEqual(environments, ["/srv/demo/.venv"])


class TerminalModelTests(unittest.TestCase):
    def test_repeated_cells_are_reused_without_losing_ansi_styles(self):
        screen = VtScreen(columns=20, rows=3)
        parser = VtParser(screen)

        parser.feed("xxxxx\x1b[31myyyyy\x1b[0m")

        self.assertEqual(len({id(cell) for cell in screen.lines[0][:5]}), 1)
        self.assertEqual(len({id(cell) for cell in screen.lines[0][5:10]}), 1)
        self.assertNotEqual(screen.lines[0][0].fg, screen.lines[0][5].fg)
        self.assertFalse(screen.lines[0][5].bold)


class TerminalVenvTests(unittest.TestCase):
    def setUp(self):
        self.terminal = TerminalWidget()

    def tearDown(self):
        self.terminal.close()
        APP.processEvents()

    def test_detected_inactive_venv_requests_activation_once(self):
        session = object()
        candidate = "/srv/demo/.venv"
        requested = []
        self.terminal.session = session
        self.terminal.current_path = "/srv/demo"
        self.terminal.venv_activation_requested.connect(
            lambda terminal, path: requested.append((terminal, path))
        )

        self.terminal._project_venv_ready(session, "/srv/demo", candidate)
        self.terminal.set_virtual_env(candidate)
        self.terminal._project_venv_ready(session, "/srv/demo", candidate)

        self.assertEqual(requested, [(self.terminal, candidate)])

    def test_activation_command_quotes_special_characters(self):
        commands = []
        self.terminal.send_command = commands.append
        self.terminal.activate_project_venv("/srv/client's app/.venv")
        self.assertEqual(
            commands,
            [". '/srv/client'\"'\"'s app/.venv/bin/activate'"],
        )


class TerminalTabsTests(unittest.TestCase):
    def setUp(self):
        self.tabs = TerminalTabs()

    def tearDown(self):
        self.tabs.close()
        APP.processEvents()

    def test_only_terminal_cannot_be_closed(self):
        only = self.tabs.terminals()[0]
        self.tabs.close_terminal(self.tabs.tabs.indexOf(only))
        self.assertEqual(self.tabs.terminals(), [only])

    def test_closing_current_terminal_selects_the_next_active_one(self):
        first = self.tabs.terminals()[0]
        middle = self.tabs.add_terminal()
        right = self.tabs.add_terminal()
        self.tabs.tabs.setCurrentWidget(middle)

        self.tabs.close_terminal(self.tabs.tabs.indexOf(middle))

        self.assertEqual(self.tabs.terminals(), [first, right])
        self.assertIs(self.tabs.tabs.currentWidget(), right)

    def test_closing_background_terminal_preserves_current_one(self):
        first = self.tabs.terminals()[0]
        current = self.tabs.add_terminal()
        self.tabs.tabs.setCurrentWidget(current)

        self.tabs.close_terminal(self.tabs.tabs.indexOf(first))

        self.assertIs(self.tabs.tabs.currentWidget(), current)


if __name__ == "__main__":
    unittest.main()
