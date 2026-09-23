from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache


DEFAULT_FG = "#f1f3f5"
DEFAULT_BG = "#0d0f12"


@dataclass(frozen=True, slots=True)
class Cell:
    char: str = " "
    fg: str = DEFAULT_FG
    bg: str = DEFAULT_BG
    bold: bool = False
    italic: bool = False
    underline: bool = False
    inverse: bool = False
    dim: bool = False
    strike: bool = False

    def blank(self) -> "Cell":
        return styled_cell(self, " ")


@lru_cache(maxsize=16384)
def styled_cell(style: Cell, char: str) -> Cell:
    """Reuse immutable terminal cells instead of allocating one per column."""
    return replace(style, char=char)


class VtScreen:
    def __init__(self, columns: int = 80, rows: int = 24, history_limit: int = 5000):
        self.columns = max(2, columns)
        self.rows = max(2, rows)
        self.history_limit = history_limit
        self.history: list[list[Cell]] = []
        self.lines = [self._blank_line() for _ in range(self.rows)]
        self.row = 0
        self.column = 0
        self.scroll_top = 0
        self.scroll_bottom = self.rows - 1
        self.cursor_visible = True
        self.wraparound = True
        self.insert_mode = False
        self.application_cursor = False
        self.bracketed_paste = False
        self.current = Cell()
        self.saved_state = None
        self._main_state = None
        self.alternate = False
        self.last_char = " "

    def _blank_line(self, cell: Cell | None = None) -> list[Cell]:
        template = (cell or Cell()).blank()
        return [template] * self.columns

    def reset(self) -> None:
        self.history.clear()
        self.lines = [self._blank_line() for _ in range(self.rows)]
        self.row = self.column = 0
        self.scroll_top = 0
        self.scroll_bottom = self.rows - 1
        self.cursor_visible = True
        self.wraparound = True
        self.insert_mode = False
        self.application_cursor = False
        self.bracketed_paste = False
        self.current = Cell()

    def resize(self, columns: int, rows: int) -> None:
        columns, rows = max(2, columns), max(2, rows)
        old_columns, old_rows = self.columns, self.rows
        if columns != self.columns:
            for line in self.lines:
                if columns > self.columns:
                    line.extend([Cell()] * (columns - self.columns))
                else:
                    del line[columns:]
            for line in self.history:
                if columns > self.columns:
                    line.extend([Cell()] * (columns - self.columns))
                else:
                    del line[columns:]
            self.columns = columns
        if rows > self.rows:
            self.lines.extend(self._blank_line() for _ in range(rows - self.rows))
        elif rows < self.rows:
            remove = self.rows - rows
            if self.row >= rows:
                moved = self.lines[:remove]
                if not self.alternate:
                    self.history.extend(moved)
                self.lines = self.lines[remove:]
                self.row = max(0, self.row - remove)
            else:
                self.lines = self.lines[:rows]
        self.rows = rows
        self.scroll_top = 0
        self.scroll_bottom = rows - 1
        self.row = min(self.row, rows - 1)
        self.column = min(self.column, columns - 1)
        self._resize_saved_main(columns, rows, old_columns, old_rows)
        self._trim_history()

    def _resize_saved_main(self, columns: int, rows: int, old_columns: int, old_rows: int) -> None:
        if not self._main_state:
            return
        lines, history, row, column, _top, _bottom, current, saved = self._main_state
        if columns != old_columns:
            for line in lines + history:
                if columns > old_columns:
                    line.extend([Cell()] * (columns - old_columns))
                else:
                    del line[columns:]
        if rows > old_rows:
            lines.extend(self._blank_line() for _ in range(rows - old_rows))
        elif rows < old_rows:
            remove = old_rows - rows
            if row >= rows:
                history.extend(lines[:remove])
                lines = lines[remove:]
                row = max(0, row - remove)
            else:
                lines = lines[:rows]
        if len(history) > self.history_limit:
            del history[: len(history) - self.history_limit]
        self._main_state = (
            lines, history, min(row, rows - 1), min(column, columns - 1),
            0, rows - 1, current, saved,
        )

    def _trim_history(self) -> None:
        if len(self.history) > self.history_limit:
            del self.history[: len(self.history) - self.history_limit]

    def save_cursor(self) -> None:
        self.saved_state = (self.row, self.column, replace(self.current))

    def restore_cursor(self) -> None:
        if self.saved_state:
            self.row, self.column, self.current = self.saved_state
            self.row = min(self.row, self.rows - 1)
            self.column = min(self.column, self.columns - 1)

    def use_alternate(self, enabled: bool) -> None:
        if enabled and not self.alternate:
            self._main_state = (
                self.lines, self.history, self.row, self.column, self.scroll_top,
                self.scroll_bottom, replace(self.current), self.saved_state,
            )
            self.lines = [self._blank_line() for _ in range(self.rows)]
            self.history = []
            self.row = self.column = 0
            self.scroll_top, self.scroll_bottom = 0, self.rows - 1
            self.alternate = True
        elif not enabled and self.alternate and self._main_state:
            (
                self.lines, self.history, self.row, self.column, self.scroll_top,
                self.scroll_bottom, self.current, self.saved_state,
            ) = self._main_state
            self._main_state = None
            self.alternate = False

    def carriage_return(self) -> None:
        self.column = 0

    def backspace(self) -> None:
        self.column = max(0, self.column - 1)

    def tab(self) -> None:
        self.column = min(self.columns - 1, ((self.column // 8) + 1) * 8)

    def linefeed(self) -> None:
        if self.row == self.scroll_bottom:
            self.scroll_up(1)
        else:
            self.row = min(self.rows - 1, self.row + 1)

    def reverse_index(self) -> None:
        if self.row == self.scroll_top:
            self.scroll_down(1)
        else:
            self.row = max(0, self.row - 1)

    def scroll_up(self, count: int = 1) -> None:
        for _ in range(max(1, count)):
            removed = self.lines.pop(self.scroll_top)
            self.lines.insert(self.scroll_bottom, self._blank_line(self.current))
            if self.scroll_top == 0 and self.scroll_bottom == self.rows - 1 and not self.alternate:
                self.history.append(removed)
        self._trim_history()

    def scroll_down(self, count: int = 1) -> None:
        for _ in range(max(1, count)):
            self.lines.pop(self.scroll_bottom)
            self.lines.insert(self.scroll_top, self._blank_line(self.current))

    def put(self, char: str) -> None:
        if not char:
            return
        if self.column >= self.columns:
            if self.wraparound:
                self.column = 0
                self.linefeed()
            else:
                self.column = self.columns - 1
        if self.insert_mode:
            line = self.lines[self.row]
            line.insert(self.column, self.current.blank())
            del line[-1]
        self.lines[self.row][self.column] = styled_cell(self.current, char)
        self.last_char = char
        self.column += 1

    def erase_line(self, mode: int = 0) -> None:
        if mode == 0:
            start, end = self.column, self.columns
        elif mode == 1:
            start, end = 0, self.column + 1
        else:
            start, end = 0, self.columns
        for col in range(start, end):
            self.lines[self.row][col] = self.current.blank()

    def erase_display(self, mode: int = 0) -> None:
        if mode in (2, 3):
            self.lines = [self._blank_line(self.current) for _ in range(self.rows)]
            if mode == 3:
                self.history.clear()
            return
        if mode == 0:
            self.erase_line(0)
            row_range = range(self.row + 1, self.rows)
        else:
            self.erase_line(1)
            row_range = range(0, self.row)
        for row in row_range:
            self.lines[row] = self._blank_line(self.current)

    def insert_chars(self, count: int) -> None:
        line = self.lines[self.row]
        for _ in range(max(1, count)):
            line.insert(self.column, self.current.blank())
            line.pop()

    def delete_chars(self, count: int) -> None:
        line = self.lines[self.row]
        for _ in range(max(1, count)):
            if self.column < len(line):
                line.pop(self.column)
                line.append(self.current.blank())

    def erase_chars(self, count: int) -> None:
        for col in range(self.column, min(self.columns, self.column + max(1, count))):
            self.lines[self.row][col] = self.current.blank()

    def insert_lines(self, count: int) -> None:
        if not self.scroll_top <= self.row <= self.scroll_bottom:
            return
        for _ in range(max(1, count)):
            self.lines.insert(self.row, self._blank_line(self.current))
            self.lines.pop(self.scroll_bottom + 1)

    def delete_lines(self, count: int) -> None:
        if not self.scroll_top <= self.row <= self.scroll_bottom:
            return
        for _ in range(max(1, count)):
            self.lines.pop(self.row)
            self.lines.insert(self.scroll_bottom, self._blank_line(self.current))

    def set_scroll_region(self, top: int, bottom: int) -> None:
        if 0 <= top < bottom < self.rows:
            self.scroll_top, self.scroll_bottom = top, bottom
            self.row = top
            self.column = 0

    def visible_lines(self, history_offset: int = 0) -> list[list[Cell]]:
        combined = self.history + self.lines
        start = max(0, min(history_offset, len(self.history)))
        return combined[start : start + self.rows]
