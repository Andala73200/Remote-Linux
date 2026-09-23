from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from app.widgets.vt_model import Cell, DEFAULT_BG, DEFAULT_FG, VtScreen

from app.widgets.vt_colors import ANSI_16, DEC_GRAPHICS, xterm_color

class VtParser:
    def __init__(
        self,
        screen: VtScreen,
        response: Callable[[bytes], None] | None = None,
        cwd_changed: Callable[[str], None] | None = None,
        virtual_env_changed: Callable[[str], None] | None = None,
        title_changed: Callable[[str], None] | None = None,
        command_finished: Callable[[int], None] | None = None,
    ):
        self.screen = screen
        self.response = response or (lambda _data: None)
        self.cwd_changed = cwd_changed or (lambda _path: None)
        self.virtual_env_changed = virtual_env_changed or (lambda _path: None)
        self.title_changed = title_changed or (lambda _title: None)
        self.command_finished = command_finished or (lambda _code: None)
        self.state = "normal"
        self.csi = ""
        self.osc = ""
        self.osc_escaped = False
        self.charset_target = "G0"
        self.g0_graphics = False
        self.g1_graphics = False
        self.use_g1 = False

    def feed(self, text: str) -> None:
        for char in text:
            self._feed_char(char)

    def _feed_char(self, char: str) -> None:
        if self.state == "osc":
            if self.osc_escaped:
                if char == "\\":
                    self._finish_osc()
                else:
                    self.osc += "\x1b" + char
                    self.osc_escaped = False
                return
            if char == "\x07":
                self._finish_osc()
            elif char == "\x1b":
                self.osc_escaped = True
            else:
                self.osc += char
            return
        if self.state == "csi":
            if "@" <= char <= "~":
                self._handle_csi(self.csi, char)
                self.state = "normal"
                self.csi = ""
            else:
                self.csi += char
            return
        if self.state == "charset":
            graphics = char == "0"
            if self.charset_target == "G0":
                self.g0_graphics = graphics
            else:
                self.g1_graphics = graphics
            self.state = "normal"
            return
        if self.state == "esc":
            self._handle_escape(char)
            return
        code = ord(char)
        if char == "\x1b":
            self.state = "esc"
        elif char == "\r":
            self.screen.carriage_return()
        elif char in "\n\v\f":
            self.screen.linefeed()
        elif char == "\b":
            self.screen.backspace()
        elif char == "\t":
            self.screen.tab()
        elif char == "\x0e":
            self.use_g1 = True
        elif char == "\x0f":
            self.use_g1 = False
        elif code >= 32 and char != "\x7f":
            graphics = self.g1_graphics if self.use_g1 else self.g0_graphics
            self.screen.put(DEC_GRAPHICS.get(char, char) if graphics else char)

    def _handle_escape(self, char: str) -> None:
        self.state = "normal"
        if char == "[":
            self.state = "csi"
            self.csi = ""
        elif char == "]":
            self.state = "osc"
            self.osc = ""
            self.osc_escaped = False
        elif char in "()":
            self.charset_target = "G0" if char == "(" else "G1"
            self.state = "charset"
        elif char == "7":
            self.screen.save_cursor()
        elif char == "8":
            self.screen.restore_cursor()
        elif char == "D":
            self.screen.linefeed()
        elif char == "M":
            self.screen.reverse_index()
        elif char == "E":
            self.screen.carriage_return()
            self.screen.linefeed()
        elif char == "c":
            self.screen.reset()
        elif char == "Z":
            self.response(b"\x1b[?1;2c")

    def _finish_osc(self) -> None:
        payload = self.osc
        self.state = "normal"
        self.osc = ""
        self.osc_escaped = False
        if ";" not in payload:
            return
        code, value = payload.split(";", 1)
        if code in {"0", "2"}:
            self.title_changed(value)
        elif code == "777" and value.startswith("ANDALA_CWD="):
            self.cwd_changed(value.split("=", 1)[1])
        elif code == "777" and value.startswith("ANDALA_VENV="):
            self.virtual_env_changed(value.split("=", 1)[1])
        elif code == "777" and value.startswith("REMOTE_LINUX_STATUS="):
            try:
                self.command_finished(int(value.split("=", 1)[1]))
            except ValueError:
                pass
        elif code == "7":
            marker = value.find("/")
            if marker >= 0:
                self.cwd_changed(value[marker:])

    @staticmethod
    def _params(raw: str) -> tuple[str, list[int]]:
        private = ""
        while raw and raw[0] in "?<=>!":
            private += raw[0]
            raw = raw[1:]
        raw = raw.replace(":", ";")
        values = []
        for item in raw.split(";") if raw else []:
            try:
                values.append(int(item) if item else 0)
            except ValueError:
                values.append(0)
        return private, values

    def _handle_csi(self, raw: str, final: str) -> None:
        private, values = self._params(raw)
        first = values[0] if values else 0
        count = max(1, first)
        s = self.screen
        if final == "A":
            s.row = max(s.scroll_top if private == "?" else 0, s.row - count)
        elif final == "B":
            s.row = min(s.scroll_bottom if private == "?" else s.rows - 1, s.row + count)
        elif final == "C":
            s.column = min(s.columns - 1, s.column + count)
        elif final == "D":
            s.column = max(0, s.column - count)
        elif final == "E":
            s.row = min(s.rows - 1, s.row + count); s.column = 0
        elif final == "F":
            s.row = max(0, s.row - count); s.column = 0
        elif final in {"G", "`"}:
            s.column = min(s.columns - 1, max(0, count - 1))
        elif final in {"H", "f"}:
            row = (values[0] if values else 1) or 1
            col = (values[1] if len(values) > 1 else 1) or 1
            s.row = min(s.rows - 1, row - 1); s.column = min(s.columns - 1, col - 1)
        elif final == "d":
            s.row = min(s.rows - 1, max(0, count - 1))
        elif final == "a":
            s.column = min(s.columns - 1, s.column + count)
        elif final == "e":
            s.row = min(s.rows - 1, s.row + count)
        elif final == "J":
            s.erase_display(first)
        elif final == "K":
            s.erase_line(first)
        elif final == "m" and not private:
            self._sgr(values or [0])
        elif final == "r":
            top = ((values[0] if values else 1) or 1) - 1
            bottom = ((values[1] if len(values) > 1 else s.rows) or s.rows) - 1
            s.set_scroll_region(top, bottom)
        elif final == "s":
            s.save_cursor()
        elif final == "u":
            s.restore_cursor()
        elif final == "L":
            s.insert_lines(count)
        elif final == "M":
            s.delete_lines(count)
        elif final == "@":
            s.insert_chars(count)
        elif final == "P":
            s.delete_chars(count)
        elif final == "X":
            s.erase_chars(count)
        elif final == "S":
            s.scroll_up(count)
        elif final == "T":
            s.scroll_down(count)
        elif final == "b":
            for _ in range(count):
                s.put(s.last_char)
        elif final in {"h", "l"}:
            self._mode(private, values, final == "h")
        elif final == "n":
            if first == 5:
                self.response(b"\x1b[0n")
            elif first == 6:
                prefix = "?" if private == "?" else ""
                self.response(f"\x1b[{prefix}{s.row + 1};{s.column + 1}R".encode())
        elif final == "c":
            self.response(b"\x1b[?1;2c")

    def _mode(self, private: str, values: list[int], enabled: bool) -> None:
        for mode in values:
            if private == "?":
                if mode == 1:
                    self.screen.application_cursor = enabled
                elif mode == 7:
                    self.screen.wraparound = enabled
                elif mode == 25:
                    self.screen.cursor_visible = enabled
                elif mode in {47, 1047, 1049}:
                    self.screen.use_alternate(enabled)
                elif mode == 2004:
                    self.screen.bracketed_paste = enabled
            elif mode == 4:
                self.screen.insert_mode = enabled

    def _sgr(self, codes: list[int]) -> None:
        cell = self.screen.current
        i = 0
        while i < len(codes):
            code = codes[i]
            if code == 0:
                cell = Cell()
            elif code == 1:
                cell = replace(cell, bold=True)
            elif code == 2:
                cell = replace(cell, dim=True)
            elif code == 3:
                cell = replace(cell, italic=True)
            elif code == 4:
                cell = replace(cell, underline=True)
            elif code == 7:
                cell = replace(cell, inverse=True)
            elif code == 9:
                cell = replace(cell, strike=True)
            elif code == 22:
                cell = replace(cell, bold=False, dim=False)
            elif code == 23:
                cell = replace(cell, italic=False)
            elif code == 24:
                cell = replace(cell, underline=False)
            elif code == 27:
                cell = replace(cell, inverse=False)
            elif code == 29:
                cell = replace(cell, strike=False)
            elif 30 <= code <= 37:
                cell = replace(cell, fg=ANSI_16[code - 30])
            elif 90 <= code <= 97:
                cell = replace(cell, fg=ANSI_16[8 + code - 90])
            elif 40 <= code <= 47:
                cell = replace(cell, bg=ANSI_16[code - 40])
            elif 100 <= code <= 107:
                cell = replace(cell, bg=ANSI_16[8 + code - 100])
            elif code == 39:
                cell = replace(cell, fg=DEFAULT_FG)
            elif code == 49:
                cell = replace(cell, bg=DEFAULT_BG)
            elif code in {38, 48} and i + 1 < len(codes):
                color, used = self._extended_color(codes, i + 1)
                if color:
                    if code == 38:
                        cell = replace(cell, fg=color)
                    else:
                        cell = replace(cell, bg=color)
                i += used
            i += 1
        self.screen.current = cell

    @staticmethod
    def _extended_color(codes: list[int], start: int) -> tuple[str | None, int]:
        mode = codes[start]
        if mode == 5 and start + 1 < len(codes):
            return xterm_color(codes[start + 1]), 2
        if mode == 2 and start + 3 < len(codes):
            r, g, b = [max(0, min(255, value)) for value in codes[start + 1 : start + 4]]
            return f"#{r:02x}{g:02x}{b:02x}", 4
        return None, 1
