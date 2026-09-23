from __future__ import annotations

import codecs

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QInputMethodEvent,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QApplication, QAbstractScrollArea

from app.widgets.vt_model import DEFAULT_BG, Cell, VtScreen
from app.widgets.vt_parser import VtParser
from app.widgets.vt_selection_mixin import VtSelectionMixin


class VtTerminal(VtSelectionMixin, QAbstractScrollArea):
    data_entered = Signal(bytes)
    resized = Signal(int, int)
    cwd_changed = Signal(str)
    virtual_env_changed = Signal(str)
    title_changed = Signal(str)
    command_finished = Signal(int)
    completion_requested = Signal()
    history_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.viewport().setAutoFillBackground(False)
        self.font = QFont("Consolas", 10)
        self.font.setStyleHint(QFont.Monospace)
        self.bold_font = QFont(self.font)
        self.bold_font.setBold(True)
        self.italic_font = QFont(self.font)
        self.italic_font.setItalic(True)
        self.bold_italic_font = QFont(self.bold_font)
        self.bold_italic_font.setItalic(True)
        self.cell_width = 8
        self.cell_height = 17
        self.ascent = 13
        self._measure_font()
        self.screen = VtScreen(80, 24)
        self.parser = VtParser(
            self.screen,
            response=self.data_entered.emit,
            cwd_changed=self.cwd_changed.emit,
            virtual_env_changed=self.virtual_env_changed.emit,
            title_changed=self.title_changed.emit,
            command_finished=self.command_finished.emit,
        )
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.input_enabled = False
        self.right_click_paste = False
        self._completion_shortcut = QKeySequence("²")
        self._up_hold_active = False
        self._up_hold_timer = QTimer(self)
        self._up_hold_timer.setSingleShot(True)
        self._up_hold_timer.setInterval(550)
        self._up_hold_timer.timeout.connect(self._emit_history_requested)
        self.selection_start: tuple[int, int] | None = None
        self.selection_end: tuple[int, int] | None = None
        self._dragging = False
        self.cursor_on = True
        self.cursor_timer = QTimer(self)
        self.cursor_timer.setInterval(520)
        self.cursor_timer.timeout.connect(self._blink_cursor)
        self.cursor_timer.start()
        self.verticalScrollBar().valueChanged.connect(lambda _value: self.viewport().update())

    @property
    def columns(self) -> int:
        return self.screen.columns

    @property
    def rows(self) -> int:
        return self.screen.rows

    def _measure_font(self) -> None:
        metrics = QFontMetricsF(self.font)
        self.cell_width = max(6, int(metrics.horizontalAdvance("M") + 0.8))
        self.cell_height = max(12, int(metrics.height() + 1.0))
        self.ascent = int(metrics.ascent())

    def set_input_enabled(self, enabled: bool) -> None:
        self.input_enabled = enabled
        self.setFocusPolicy(Qt.StrongFocus if enabled else Qt.ClickFocus)
        self.viewport().update()

    def feed_bytes(self, data: bytes) -> None:
        if not data:
            return
        at_bottom = self.verticalScrollBar().value() == self.verticalScrollBar().maximum()
        self.parser.feed(self.decoder.decode(data, final=False))
        self._update_scrollbar(at_bottom)
        self.viewport().update()

    def feed_text(self, text: str) -> None:
        self.parser.feed(text)
        self._update_scrollbar(True)
        self.viewport().update()

    def write_system(self, text: str) -> None:
        message = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        self.feed_text(f"\x1b[96m{message}\x1b[0m")

    def clear_terminal(self) -> None:
        self.screen.reset()
        self.selection_start = self.selection_end = None
        self._update_scrollbar(True)
        self.viewport().update()

    def _update_scrollbar(self, follow_bottom: bool) -> None:
        bar = self.verticalScrollBar()
        bar.setRange(0, len(self.screen.history))
        bar.setPageStep(self.screen.rows)
        if follow_bottom:
            bar.setValue(bar.maximum())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        columns = max(2, self.viewport().width() // self.cell_width)
        rows = max(2, self.viewport().height() // self.cell_height)
        if columns != self.screen.columns or rows != self.screen.rows:
            self.screen.resize(columns, rows)
            self._update_scrollbar(True)
            self.resized.emit(columns, rows)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor(DEFAULT_BG))
        offset = self.verticalScrollBar().value()
        lines = self.screen.visible_lines(offset)
        selected = self._selection_bounds()
        for row, line in enumerate(lines):
            y = row * self.cell_height
            for col, cell in enumerate(line):
                x = col * self.cell_width
                fg, bg = self._cell_colors(cell)
                absolute_row = offset + row
                if selected and self._inside_selection(absolute_row, col, selected):
                    fg, bg = QColor("#ffffff"), QColor("#245a7a")
                if bg.name().lower() != DEFAULT_BG:
                    painter.fillRect(x, y, self.cell_width, self.cell_height, bg)
                if cell.char != " ":
                    painter.setFont(self._cell_font(cell))
                    painter.setPen(fg)
                    painter.drawText(x, y + self.ascent, cell.char)
                if cell.underline:
                    painter.setPen(QPen(fg, 1))
                    painter.drawLine(x, y + self.cell_height - 2, x + self.cell_width, y + self.cell_height - 2)
                if cell.strike:
                    painter.setPen(QPen(fg, 1))
                    painter.drawLine(x, y + self.cell_height // 2, x + self.cell_width, y + self.cell_height // 2)
        if self._cursor_should_draw(offset):
            x = self.screen.column * self.cell_width
            y = self.screen.row * self.cell_height
            painter.fillRect(x, y + self.cell_height - 2, self.cell_width, 2, QColor("#f8f8f2"))

    @staticmethod
    def _cell_colors(cell: Cell) -> tuple[QColor, QColor]:
        fg, bg = QColor(cell.fg), QColor(cell.bg)
        if cell.inverse:
            fg, bg = bg, fg
        if cell.dim:
            fg.setAlpha(150)
        return fg, bg

    def _cell_font(self, cell: Cell) -> QFont:
        if cell.bold and cell.italic:
            return self.bold_italic_font
        if cell.bold:
            return self.bold_font
        if cell.italic:
            return self.italic_font
        return self.font

    def _cursor_should_draw(self, offset: int) -> bool:
        return (
            self.hasFocus() and self.cursor_on and self.screen.cursor_visible
            and offset == len(self.screen.history)
            and 0 <= self.screen.column < self.screen.columns
        )

    def _blink_cursor(self) -> None:
        self.cursor_on = not self.cursor_on
        self.viewport().update()

    def focusInEvent(self, event) -> None:
        self.cursor_on = True
        super().focusInEvent(event)
        self.viewport().update()

    def event(self, event) -> bool:
        # QWidget normally reserves Tab for focus navigation. Keep both Tab
        # variants fully native and never use them for Remote Linux features.
        if (
            self.input_enabled
            and event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Tab, Qt.Key_Backtab)
        ):
            self.data_entered.emit(
                b"\t" if event.key() == Qt.Key_Tab else b"\x1b[Z"
            )
            event.accept()
            return True
        return super().event(event)

    def set_completion_shortcut(self, shortcut: str) -> None:
        sequence = QKeySequence(str(shortcut or "²"))
        self._completion_shortcut = sequence if sequence.count() else QKeySequence("²")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        modifiers = event.modifiers()
        control = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)
        alt = bool(modifiers & Qt.AltModifier)

        if not self.input_enabled:
            if control and not alt and event.key() == Qt.Key_C and self._has_text_selection():
                self.copy_selection()
                event.accept()
                return
            return super().keyPressEvent(event)
        if self._matches_completion_shortcut(event):
            if not event.isAutoRepeat():
                self.completion_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Up:
            if event.isAutoRepeat() and self._up_hold_active:
                event.accept()
                return
            if not event.isAutoRepeat():
                self._up_hold_active = True
                self._up_hold_timer.start()
            sequence = self._key_sequence(event)
            if sequence is not None:
                self.data_entered.emit(sequence)
                self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
            event.accept()
            return
        if self._up_hold_active:
            self._up_hold_active = False
            self._up_hold_timer.stop()
        if control and not alt:
            if event.key() == Qt.Key_C and (shift or self._has_text_selection()):
                self.copy_selection()
                event.accept()
                return
            if event.key() == Qt.Key_V:
                self.paste_clipboard()
                event.accept()
                return

        sequence = self._key_sequence(event)
        if sequence is not None:
            self.data_entered.emit(sequence)
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Up and self._up_hold_active:
            # A held key generates auto-repeat release/press pairs on Qt.
            # Only the real physical release must cancel the hold timer.
            if event.isAutoRepeat():
                event.accept()
                return
            self._up_hold_active = False
            self._up_hold_timer.stop()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _emit_history_requested(self) -> None:
        if self._up_hold_active:
            self.history_requested.emit()

    def _matches_completion_shortcut(self, event: QKeyEvent) -> bool:
        if event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            return False
        event_sequence = QKeySequence(event.keyCombination())
        return (
            self._completion_shortcut.count() == 1
            and self._completion_shortcut.matches(event_sequence)
            == QKeySequence.SequenceMatch.ExactMatch
        )

    def _has_text_selection(self) -> bool:
        return (
            self.selection_start is not None
            and self.selection_end is not None
            and self.selection_start != self.selection_end
        )

    def _key_sequence(self, event: QKeyEvent) -> bytes | None:
        key, modifiers = event.key(), event.modifiers()
        text = event.text()
        printable = bool(text) and any(
            ord(char) >= 0x20 and ord(char) != 0x7F for char in text
        )
        # On Windows, AltGr is usually exposed as Ctrl+Alt. The resulting
        # Unicode text must take priority over Ctrl shortcuts and must never
        # receive an Escape prefix.
        altgr = bool(modifiers & Qt.GroupSwitchModifier) or bool(
            modifiers & Qt.ControlModifier
            and modifiers & Qt.AltModifier
            and printable
        )
        if (
            modifiers & Qt.ControlModifier
            and not altgr
            and Qt.Key_A <= key <= Qt.Key_Z
        ):
            return bytes([key - Qt.Key_A + 1])
        if modifiers & Qt.ControlModifier and not altgr:
            ctrl = {Qt.Key_Space: b"\x00", Qt.Key_BracketLeft: b"\x1b", Qt.Key_Backslash: b"\x1c",
                    Qt.Key_BracketRight: b"\x1d", Qt.Key_AsciiCircum: b"\x1e", Qt.Key_Underscore: b"\x1f"}
            if key in ctrl:
                return ctrl[key]
        app = self.screen.application_cursor
        mapping = {
            Qt.Key_Up: b"\x1bOA" if app else b"\x1b[A",
            Qt.Key_Down: b"\x1bOB" if app else b"\x1b[B",
            Qt.Key_Right: b"\x1bOC" if app else b"\x1b[C",
            Qt.Key_Left: b"\x1bOD" if app else b"\x1b[D",
            Qt.Key_Home: b"\x1bOH" if app else b"\x1b[H", Qt.Key_End: b"\x1bOF" if app else b"\x1b[F",
            Qt.Key_Insert: b"\x1b[2~", Qt.Key_Delete: b"\x1b[3~", Qt.Key_PageUp: b"\x1b[5~",
            Qt.Key_PageDown: b"\x1b[6~", Qt.Key_Backspace: b"\x7f", Qt.Key_Return: b"\r",
            Qt.Key_Enter: b"\r", Qt.Key_Tab: b"\t", Qt.Key_Backtab: b"\x1b[Z", Qt.Key_Escape: b"\x1b",
            Qt.Key_F1: b"\x1bOP", Qt.Key_F2: b"\x1bOQ", Qt.Key_F3: b"\x1bOR", Qt.Key_F4: b"\x1bOS",
            Qt.Key_F5: b"\x1b[15~", Qt.Key_F6: b"\x1b[17~", Qt.Key_F7: b"\x1b[18~", Qt.Key_F8: b"\x1b[19~",
            Qt.Key_F9: b"\x1b[20~", Qt.Key_F10: b"\x1b[21~", Qt.Key_F11: b"\x1b[23~", Qt.Key_F12: b"\x1b[24~",
        }
        if key in mapping:
            return mapping[key]
        if not text:
            return None
        data = text.encode("utf-8")
        genuine_alt = bool(modifiers & Qt.AltModifier) and not altgr
        return b"\x1b" + data if genuine_alt else data

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        if not self.input_enabled:
            return super().inputMethodEvent(event)
        committed = event.commitString()
        if committed:
            self.data_entered.emit(committed.encode("utf-8"))
        event.accept()

    def inputMethodQuery(self, query):
        if query == Qt.ImEnabled:
            return self.input_enabled
        if query == Qt.ImCursorRectangle:
            return QRectF(
                self.screen.column * self.cell_width,
                self.screen.row * self.cell_height,
                self.cell_width,
                self.cell_height,
            )
        if query in (Qt.ImCursorPosition, Qt.ImAnchorPosition):
            return 0
        if query == Qt.ImSurroundingText:
            return ""
        return super().inputMethodQuery(query)

    def paste_clipboard(self) -> None:
        text = QApplication.clipboard().text()
        if not text or not self.input_enabled:
            return
        data = text.encode("utf-8")
        if self.screen.bracketed_paste:
            data = b"\x1b[200~" + data + b"\x1b[201~"
        self.data_entered.emit(data)
