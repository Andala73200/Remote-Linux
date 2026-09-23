from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu

from app.i18n import tr


class TerminalHistoryMixin:
    def _init_command_history(self) -> None:
        self._command_history: list[str] = []
        self._history_restore_line: str | None = None
        self.console.history_requested.connect(self.show_command_history)

    def remember_history_restore_line(self, line: str | None) -> None:
        self._history_restore_line = None if line is None else str(line)

    def record_command_history(self, command: str) -> None:
        value = str(command or "").strip("\r\n")
        if not value:
            return
        if not self._command_history or self._command_history[-1] != value:
            self._command_history.append(value)
            del self._command_history[:-50]
        self.last_command = value
        button = getattr(self, "recall_button", None)
        if button is not None:
            button.setEnabled(True)

    def recall_last_command(self) -> None:
        if self._command_history:
            self._recall_command_text(self._command_history[-1])

    def show_command_history(self) -> None:
        self._restore_line_before_history_popup()
        menu = QMenu(self.console)
        menu.setToolTipsVisible(True)
        if not self._command_history:
            action = menu.addAction(tr("ui.no_terminal_history"))
            action.setEnabled(False)
        else:
            for command in reversed(self._command_history[-20:]):
                action = menu.addAction(self._history_label(command))
                action.setToolTip(command)
                action.triggered.connect(
                    lambda _checked=False, text=command: self._recall_command_text(text)
                )
        column = min(self.console.screen.column, max(0, self.console.columns - 1))
        row = min(self.console.screen.row, max(0, self.console.rows - 1))
        point = QPoint(
            column * self.console.cell_width,
            (row + 1) * self.console.cell_height,
        )
        menu.exec(self.console.viewport().mapToGlobal(point))
        self.console.setFocus()

    def _restore_line_before_history_popup(self) -> None:
        line, self._history_restore_line = self._history_restore_line, None
        if line is None or not self.channel or self.channel.closed:
            return
        try:
            self.channel.sendall(b"\x15" + line.encode("utf-8"))
            self.tracker.replace_line(line)
        except Exception:
            pass

    def _recall_command_text(self, command: str) -> None:
        if not self.channel or self.channel.closed:
            return
        normalized = str(command).replace("\r\n", "\n").replace("\r", "\n")
        try:
            payload = b"\x15" + self._safe_recall_payload(normalized)
            self.channel.sendall(payload)
            self.tracker.replace_line(normalized)
            self.completion.cancel()
            self.console.setFocus()
        except Exception as exc:
            self._write_system(f">>> Erreur d'envoi : {exc}\r\n")

    def _safe_recall_payload(self, text: str) -> bytes:
        raw = text.encode("utf-8")
        if "\n" not in text:
            return raw
        if self.console.screen.bracketed_paste:
            return b"\x1b[200~" + raw + b"\x1b[201~"
        # Readline/ZLE quoted-insert keeps embedded newlines in the edit buffer
        # instead of submitting each line when bracketed paste is unavailable.
        parts = raw.split(b"\n")
        return b"\x16\n".join(parts)

    @staticmethod
    def _history_label(command: str) -> str:
        lines = command.splitlines() or [command]
        first = lines[0].strip() or "…"
        if len(first) > 72:
            first = first[:69] + "…"
        if len(lines) == 1:
            return first
        suffix = tr("ui.history_block_lines").format(count=len(lines))
        return f"{first}  ·  {suffix}"
