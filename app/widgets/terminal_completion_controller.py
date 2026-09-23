from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Slot

from app.core.terminal_completion import completion_candidates
from app.core.terminal_completion_task import start_completion_task
from app.widgets.terminal_completion_panel import TerminalCompletionPanel

if TYPE_CHECKING:
    from app.widgets.terminal_widget import TerminalWidget


class TerminalCompletionController(QObject):
    def __init__(self, terminal: TerminalWidget):
        super().__init__(terminal)
        self.terminal = terminal
        self.panel = TerminalCompletionPanel()
        self._request_id = 0
        terminal.console.completion_requested.connect(self.request)
        self.panel.selected.connect(self.apply)

    def handle_input(self, data: bytes) -> bool:
        if data == b"\x1b" and self.panel.isVisible():
            self.cancel()
            self.terminal.console.setFocus()
            return True
        if data != b"\t":
            self.cancel()
        return False

    def request(self) -> None:
        terminal = self.terminal
        if not terminal.session or not terminal.channel or terminal.channel.closed:
            return
        source_line = terminal.tracker.completion_source()
        if source_line is None:
            return
        session = terminal.session
        path = terminal.current_path
        self._request_id += 1
        request_id = self._request_id
        self.panel.clear_candidates()
        start_completion_task(
            request_id,
            lambda: completion_candidates(session, source_line, path),
            self._ready,
            self._failed,
        )

    @Slot(int, object)
    def _ready(self, request_id: int, candidates: object) -> None:
        if request_id != self._request_id or not self.terminal.session:
            return
        values = list(candidates or [])
        if values:
            self.panel.show_candidates(values)
            self.terminal.console.setFocus()

    @Slot(int, str)
    def _failed(self, request_id: int, _text: str) -> None:
        if request_id == self._request_id:
            self.panel.clear_candidates()

    def apply(self, line: str) -> None:
        terminal = self.terminal
        if not terminal.channel or terminal.channel.closed:
            return
        try:
            terminal.channel.sendall(b"\x15" + line.encode("utf-8"))
            terminal.tracker.replace_line(line)
            self.panel.clear_candidates()
            terminal.console.setFocus()
        except Exception as exc:
            terminal._write_system(f">>> Erreur d'envoi : {exc}\r\n")

    def cancel(self) -> None:
        self._request_id += 1
        self.panel.clear_candidates()

